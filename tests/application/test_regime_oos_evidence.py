"""Tests for regime-conditioned OOS evidence (T2-11-1).

The builder must:

1. Attribute every fold of an ``OutOfSampleReport`` to the regime dominating
   its test window, using the *exact* price series the report ran on.
2. Never assess a fold whose test window is dominated by the classifier's
   warm-up tag or that has no buy-and-hold reference (no fabricated regime
   wins).
3. Produce a robustness score that is advisory and deterministic, and a
   classification error (with every fold unassessed) when the price series is
   unclassifiable.
4. Refuse obviously misaligned inputs (fold windows beyond the price series).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.baseline_evaluation import (
    EvaluationCosts,
)
from backend.application.research.decision_pipeline_evaluator import (
    DecisionPipelineEvaluator,
    FoldOutcome,
    OutOfSampleReport,
)
from backend.application.research.regime_oos_evidence import RegimeOosEvidenceBuilder
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.oos_evaluation import PooledEvidence


def make_market(
    seed: int = 11,
    n: int = 280,
    symbol: str = "btcusdt",
    drift: float = 0.0003,
    vol: float = 0.004,
) -> list[ObservationEvent]:
    import random

    rng = random.Random(seed)
    price = 100.0
    events: list[ObservationEvent] = []
    t = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        price *= 1.0 + rng.gauss(drift, vol)
        events.append(
            ObservationEvent(
                source_id="synthetic",
                source_name="Synthetic",
                event_type=ObservationEventType.TRADE,
                timestamp=t,
                payload={
                    "symbol": symbol,
                    "trade_id": i,
                    "price": round(price, 4),
                    "quantity": 1.0,
                },
            )
        )
        t += timedelta(seconds=5)
    return events


def prices_of(events: list[ObservationEvent]) -> list[float]:
    return [float(e.payload["price"]) for e in events]


def default_report(events: list[ObservationEvent]) -> OutOfSampleReport:
    evaluator = DecisionPipelineEvaluator(cv=WalkForwardCV(train_size=80, test_size=20))
    return evaluator.evaluate(events)


def _fold_without_bh(fold: FoldOutcome) -> FoldOutcome:
    """A copy of ``fold`` with the buy-and-hold baseline removed."""
    baselines = tuple(b for b in fold.baselines if b.name != "buy_and_hold")
    return FoldOutcome(
        fold=fold.fold,
        train_range=fold.train_range,
        test_range=fold.test_range,
        report=fold.report,
        baselines=baselines,
    )


class TestBuilderValidation:
    def test_rejects_empty_prices(self):
        report = default_report(make_market())
        with pytest.raises(ValueError, match="non-empty"):
            RegimeOosEvidenceBuilder().build(report, [])

    def test_rejects_report_without_folds(self):
        report = OutOfSampleReport(
            symbol="btcusdt",
            costs=EvaluationCosts(half_spread_pct=0.0002, taker_fee_pct=0.0004),
            cv_spec={"train_size": 80, "test_size": 20, "expanding": True},
            folds=(),
            pooled=PooledEvidence(
                n_folds=0,
                total_test_bars=0,
                total_trades=0,
                total_wins=0,
                total_losses=0,
                total_fees=0.0,
                total_slippage_bps=0.0,
                gross_profit=0.0,
                gross_loss=0.0,
                mean_return_pct=0.0,
                median_return_pct=0.0,
                mean_excess_return_pct=0.0,
                positive_fold_rate=0.0,
                beats_buy_and_hold_rate=0.0,
                mean_max_drawdown_pct=0.0,
                deflated_sharpe=None,
                reasoner="test",
                cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
            ),
        )
        with pytest.raises(ValueError, match="at least one fold"):
            RegimeOosEvidenceBuilder().build(report, prices_of(make_market()))

    def test_rejects_fold_window_beyond_prices(self):
        report = default_report(make_market())
        with pytest.raises(ValueError, match="test window ends at step"):
            RegimeOosEvidenceBuilder().build(report, prices_of(make_market())[:200])

    def test_rejects_bad_min_folds(self):
        with pytest.raises(ValueError, match="min_dominant_folds"):
            RegimeOosEvidenceBuilder(min_dominant_folds=0)


class TestBuilderEvidence:
    def test_allocates_every_fold(self):
        events = make_market()
        report = default_report(events)
        evidence = RegimeOosEvidenceBuilder().build(report, prices_of(events))
        assert len(evidence.folds) == len(report.folds)
        for allocation in evidence.folds:
            assert allocation.fold == report.folds[allocation.fold].fold
            assert allocation.test_range == report.folds[allocation.fold].test_range

    def test_warmup_dominant_window_is_not_assessed(self):
        # First fold's test window begins within the classifier's warm-up; a
        # builder that naively attributed it would fabricate regime alpha.
        events = make_market()
        report = default_report(events)
        evidence = RegimeOosEvidenceBuilder(
            classifier=__import__(
                "backend.application.research.regime_evaluation",
                fromlist=["VolatilityRegimeClassifier"],
            ).VolatilityRegimeClassifier(window=40, vol_threshold=0.001)
        ).build(report, prices_of(events))
        first = evidence.folds[0]
        if first.dominant_regime == "warmup":
            assert not first.assessed
            assert "warm-up" in first.note

    def test_no_buy_and_hold_reference_not_assessed(self):
        events = make_market()
        report = default_report(events)
        report = OutOfSampleReport(
            symbol=report.symbol,
            costs=report.costs,
            cv_spec=report.cv_spec,
            folds=tuple(_fold_without_bh(f) for f in report.folds),
            pooled=report.pooled,
        )
        evidence = RegimeOosEvidenceBuilder().build(report, prices_of(events))
        assert all(not a.assessed for a in evidence.folds)
        assert evidence.robustness_score is None

    def test_flat_series_is_classification_error(self):
        report = default_report(make_market())
        flat = [100.0] * len(prices_of(make_market()))
        evidence = RegimeOosEvidenceBuilder().build(report, flat)
        assert evidence.classification_error is not None
        assert evidence.robustness_score is None
        assert all(not a.assessed for a in evidence.folds)

    def test_report_matches_report_symbol(self):
        events = make_market()
        report = default_report(events)
        evidence = RegimeOosEvidenceBuilder().build(report, prices_of(events))
        assert evidence.symbol == report.symbol
        assert evidence.classifier_name == "volatility_regime"
        assert list(evidence.classifier_params)  # params captured

    def test_deterministic(self):
        events = make_market()
        report = default_report(events)
        a = RegimeOosEvidenceBuilder().build(report, prices_of(events))
        b = RegimeOosEvidenceBuilder().build(report, prices_of(events))
        assert a.as_dict() == b.as_dict()

    def test_as_dict_round_trip(self):
        events = make_market()
        report = default_report(events)
        evidence = RegimeOosEvidenceBuilder().build(report, prices_of(events))
        payload = evidence.as_dict()
        assert payload["symbol"] == report.symbol
        assert len(payload["folds"]) == len(report.folds)
        assert payload["regimes"] == [s.as_dict() for s in evidence.regimes]
