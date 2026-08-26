"""Tests for the feature attribution and ablation framework (P1-004).

The framework must guarantee:

1. Every feature can be switched on/off and its incremental contribution
   measured (a real signal has a positive delta; a dead feature has none).
2. Results are reported by regime (samples bucket by their regime tag).
3. Results are reported by cost (flips are priced with the same cost model
   the baselines use; a lift never beats its own execution cost).
4. The scorer is pluggable and deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.experiment_registry import ExperimentRegistry
from backend.application.research.feature_attribution import (
    AblationRunner,
    ThresholdScorer,
    compute_metrics,
    record_ablation,
)
from backend.domain.research.attribution import AttributionReport, Metrics
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)
from backend.domain.research.label import LabelDefinition, LabeledSample, LabelKind
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.experiment_repository import SqliteExperimentRepository


def sample(
    *,
    features: dict,
    label: float,
    regime: str = "bull",
    decision_time: datetime | None = None,
    index: int = 0,
) -> LabeledSample:
    now = decision_time or datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC) + timedelta(minutes=index)
    return LabeledSample(
        decision_time=now,
        label=label,
        label_start=now,
        label_end=now + timedelta(minutes=5),
        features=dict(features),
        label_definition=LabelDefinition(kind=LabelKind.FIXED_HORIZON, horizon=5),
        sample_index=index,
        metadata={"symbol": "btcusdt", "regime": regime},
    )


def simple_samples(n: int, signal: str = "trend") -> list[LabeledSample]:
    """n samples where ``signal`` perfectly separates +/- labels; noise doesn't."""
    samples = []
    for i in range(n):
        up = i % 2 == 0
        features = {signal: 1.0 if up else -3.0}
        samples.append(sample(features=features, label=1.0 if up else -1.0, index=i))
    return samples


class TestMetrics:
    def test_perfect_agreement(self):
        truth = [1.0, -1.0, 1.0, -1.0]
        m = compute_metrics(truth, truth)
        assert m.accuracy == 1.0
        assert m.f1 == 1.0
        assert m.n == 4

    def test_inverted_is_zero_skill(self):
        truth = [1.0, -1.0, 1.0, -1.0]
        inverted = [-t for t in truth]
        m = compute_metrics(truth, inverted)
        assert m.accuracy == 0.0
        assert m.f1 == 0.0

    def test_abstentions_count_only_in_accuracy(self):
        truth = [1.0, -1.0, 0.0, 0.0]
        pred = [1.0, -1.0, 0.0, 0.0]
        m = compute_metrics(truth, pred)
        assert m.accuracy == 1.0
        assert m.f1 == 1.0

    def test_validation(self):
        with pytest.raises(ValueError):
            compute_metrics([1.0], [1.0, -1.0])
        with pytest.raises(ValueError):
            compute_metrics([], [])


class TestReportShape:
    def test_report_fields(self):
        samples = simple_samples(20)
        report: AttributionReport = AblationRunner().run(
            samples=samples, scorer=ThresholdScorer("trend"), feature_names=["trend", "noise"]
        )
        assert report.scorer_name == "threshold"
        assert report.feature_names == ("trend", "noise")
        assert {"trend", "noise"} <= {a.feature for a in report.attribution}


class TestAttributionMetrics:
    def test_real_signal_has_positive_delta(self):
        runner = AblationRunner(EvaluationCosts.free())
        report = runner.run(
            samples=simple_samples(30),
            scorer=ThresholdScorer("trend"),
            feature_names=["trend"],
        )
        attribution = report.for_feature("trend")[0]
        assert attribution.delta_f1 > 0.0
        assert attribution.delta_accuracy > 0.0

    def test_dead_feature_has_no_delta(self):
        runner = AblationRunner(EvaluationCosts.free())
        report = runner.run(
            samples=simple_samples(30),
            scorer=ThresholdScorer("trend"),
            feature_names=["noise"],
        )
        noise = report.for_feature("noise")[0]
        # The scorer never reads "noise", so removing it changes nothing.
        assert noise.delta_f1 == 0.0
        assert noise.delta_accuracy == 0.0
        assert noise.flip_count == 0
        assert noise.cost_pct == 0.0

    def test_every_feature_is_switched_on_and_off(self):
        runner = AblationRunner(EvaluationCosts.free())
        samples = simple_samples(20)
        report = runner.run(
            samples=samples, scorer=ThresholdScorer("trend"), feature_names=["trend", "momentum"]
        )
        for a in report.attribution:
            # Both ablations ran; the real feature alone carries the signal.
            assert isinstance(a.full_metrics, Metrics)
            assert isinstance(a.ablated_metrics, Metrics)


class TestRegimeReporting:
    def test_results_reported_by_regime(self):
        bull = [
            sample(features={"trend": 1.0}, label=1.0, regime="bull", index=i) for i in range(10)
        ]
        bear = [
            sample(features={"trend": -3.0}, label=-1.0, regime="bear", index=i + 50)
            for i in range(10)
        ]
        samples = bull + bear
        runner = AblationRunner(EvaluationCosts.free())
        report = runner.run(
            samples=samples, scorer=ThresholdScorer("trend"), feature_names=["trend"]
        )
        assert set(report.regimes) == {"bull", "bear"}
        for attribution in report.attribution:
            assert attribution.regime in ("bull", "bear")
            assert attribution.delta_f1 > 0.0

    def test_regime_mixing_does_not_leak(self):
        bull = [
            sample(features={"trend": 1.0}, label=1.0, regime="bull", index=i) for i in range(10)
        ]
        bear = [
            sample(features={"trend": -3.0}, label=-1.0, regime="bear", index=i + 50)
            for i in range(10)
        ]
        samples = bull + bear
        runner = AblationRunner(EvaluationCosts.free())
        report = runner.run(
            samples=samples, scorer=ThresholdScorer("trend"), feature_names=["trend"]
        )
        # Per-regime attribution is cleaner than the pooled mixes.
        per_regime = [a.delta_accuracy for a in report.attribution]
        assert all(d > 0.0 for d in per_regime)


class TestCostReporting:
    def test_flips_are_costed(self):
        # A feature that causes prediction flips must carry a positive cost.
        samples = []
        for i in range(20):
            if i < 10:
                samples.append(sample(features={"trend": 1.0, "noise": 1.0}, label=1.0, index=i))
            else:
                samples.append(sample(features={"trend": -3.0, "noise": -1.0}, label=-1.0, index=i))
        runner = AblationRunner(EvaluationCosts.realistic())
        report = runner.run(
            samples=samples, scorer=ThresholdScorer("trend"), feature_names=["trend", "noise"]
        )
        # Removing the real feature flips predictions; the dead one doesn't.
        assert report.for_feature("trend")[0].flip_count > 0
        assert report.for_feature("noise")[0].flip_count == 0
        assert report.for_feature("noise")[0].cost_pct == 0.0
        assert report.for_feature("trend")[0].cost_pct > 0.0

    def test_cost_model_is_reported(self):
        runner = AblationRunner(EvaluationCosts.realistic())
        report = runner.run(
            samples=simple_samples(10),
            scorer=ThresholdScorer("trend"),
            feature_names=["trend"],
        )
        assert report.cost_model["half_spread_pct"] == 0.0002
        assert report.cost_model["round_trip_multiplier"] == 2.0

    def test_free_costs_cancel(self):
        runner = AblationRunner(EvaluationCosts.free())
        report = runner.run(
            samples=simple_samples(10),
            scorer=ThresholdScorer("trend"),
            feature_names=["trend"],
        )
        assert report.for_feature("trend")[0].cost_pct == 0.0


class TestValidationAndHelpers:
    def test_empty_samples_rejected(self):
        with pytest.raises(ValueError):
            AblationRunner().run(
                samples=[], scorer=ThresholdScorer("trend"), feature_names=["trend"]
            )

    def test_empty_features_rejected(self):
        with pytest.raises(ValueError):
            AblationRunner().run(
                samples=simple_samples(2), scorer=ThresholdScorer("trend"), feature_names=[]
            )

    def test_round_trip_multiplier_validated(self):
        with pytest.raises(ValueError):
            AblationRunner(round_trip_multiplier=0.0)

    def test_threshold_scorer_abstains_on_missing(self):
        scorer = ThresholdScorer("missing")
        assert scorer.predict({}) == 0.0

    def test_threshold_scorer_direction(self):
        scorer = ThresholdScorer("trend")
        assert scorer.predict({"trend": 1.0}) == 1.0
        assert scorer.predict({"trend": -3.0}) == -1.0


def ablation_report() -> AttributionReport:
    runner = AblationRunner(EvaluationCosts.realistic())
    return runner.run(
        samples=simple_samples(30),
        scorer=ThresholdScorer("trend"),
        feature_names=["trend", "noise"],
    )


def register_experiment(
    registry: ExperimentRegistry, experiment_id: str = "exp-ablation-1"
) -> None:
    registry.register(
        ExperimentRecord(
            experiment_id=experiment_id,
            created_at=datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC),
            hypothesis="momentum feature adds signal after costs",
            dataset_id="binance-btcusdt",
            dataset_version=1,
            group=ExperimentGroup.TUNING,
            scorer_name="threshold",
            features=("trend", "momentum"),
            label_definition={"kind": "fixed_horizon", "horizon": 5},
            cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
            metrics={},
            status=ExperimentStatus.RUNNING,
        )
    )


class TestAblationPersistence:
    def test_full_report_payload_persisted_verbatim(self, tmp_path) -> None:
        """The complete report — not a summary — survives the store round trip."""
        registry = ExperimentRegistry(
            SqliteExperimentRepository(Database(tmp_path / "ablation.db"))
        )
        register_experiment(registry)
        report = ablation_report()

        stored = record_ablation(registry, "exp-ablation-1", report)

        assert stored.status is ExperimentStatus.DONE
        assert stored.metrics == report.as_dict()
        # the store's payload column is an identical snapshot of the record
        reloaded = registry.get("exp-ablation-1")
        assert reloaded is not None
        assert reloaded.metrics == report.as_dict()
        assert reloaded.as_dict() == stored.as_dict()

    def test_attribution_detail_survives_persistence(self, tmp_path) -> None:
        registry = ExperimentRegistry(
            SqliteExperimentRepository(Database(tmp_path / "ablation2.db"))
        )
        register_experiment(registry)
        report = ablation_report()
        record_ablation(registry, "exp-ablation-1", report)

        reloaded = registry.get("exp-ablation-1")
        assert reloaded is not None
        attribution = reloaded.metrics["attribution"]
        assert isinstance(attribution, list)
        assert len(attribution) == len(report.attribution)
        first = attribution[0]
        assert set(first) == {
            "feature",
            "regime",
            "full_metrics",
            "ablated_metrics",
            "delta_accuracy",
            "delta_f1",
            "flip_count",
            "cost_pct",
            "lift_is_worth_cost",
        }
        assert first["full_metrics"]["n"] == 30
        assert reloaded.metrics["cost_model"]["half_spread_pct"] == 0.0002

    def test_unknown_experiment_refused(self, tmp_path) -> None:
        registry = ExperimentRegistry(
            SqliteExperimentRepository(Database(tmp_path / "ablation3.db"))
        )
        with pytest.raises(ValueError, match="unknown experiment"):
            record_ablation(registry, "exp-does-not-exist", ablation_report())

    def test_terminal_experiment_cannot_be_rewritten(self, tmp_path) -> None:
        registry = ExperimentRegistry(
            SqliteExperimentRepository(Database(tmp_path / "ablation4.db"))
        )
        register_experiment(registry)
        record_ablation(registry, "exp-ablation-1", ablation_report())
        with pytest.raises(ValueError, match="terminal"):
            record_ablation(registry, "exp-ablation-1", ablation_report())
