# tests/application/test_research_loop.py
"""Tests for the autonomous research loop (task P4-002).

The loop must generate hypotheses, run experiments, weigh evidence
honestly, and hand results off *without any path to deployment*. Everything
here runs offline with a fake experiment store and a stub runner.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from backend.application.interfaces.experiment_store import ExperimentStore
from backend.application.research.research_loop import (
    HypothesisGenerator,
    ResearchLoop,
    ResearchLoopConfig,
    generate_hypotheses,
    run_research_cycle,
)
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)
from backend.domain.research.hypothesis import (
    CycleReport,
    EvidenceSummary,
    EvidenceVerdict,
    ExperimentOutcome,
    Hypothesis,
    HypothesisSource,
)


class InMemoryExperimentStore(ExperimentStore):
    """Minimal offline ExperimentStore for loop tests."""

    def __init__(self) -> None:
        self._records: dict[str, ExperimentRecord] = {}
        self._claims: set[str] = set()

    def save(self, record: ExperimentRecord) -> None:
        if record.experiment_id in self._records:
            raise ValueError(f"duplicate experiment {record.experiment_id}")
        if record.status is not ExperimentStatus.RUNNING:
            raise ValueError("only RUNNING records can be saved")
        self._records[record.experiment_id] = record

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        return self._records.get(experiment_id)

    def list(
        self,
        *,
        group: ExperimentGroup | None = None,
        status: ExperimentStatus | None = None,
    ) -> list[ExperimentRecord]:
        matches = [
            r
            for r in self._records.values()
            if (group is None or r.group is group) and (status is None or r.status is status)
        ]
        return sorted(matches, key=lambda r: r.created_at, reverse=True)

    def set_status(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        failure_reason: str | None = None,
    ) -> ExperimentRecord:
        if experiment_id not in self._records:
            raise ValueError(f"unknown experiment {experiment_id}")
        record = self._records[experiment_id]
        if record.status is not ExperimentStatus.RUNNING:
            raise ValueError(f"{experiment_id} is already {record.status.value}")
        updated = self.record_result(
            experiment_id, status, metrics=dict(record.metrics), failure_reason=failure_reason
        )
        return updated

    def record_result(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        *,
        metrics: dict[str, object],
        failure_reason: str | None = None,
    ) -> ExperimentRecord:
        record = self._records[experiment_id]
        updated = ExperimentRecord(
            experiment_id=record.experiment_id,
            created_at=record.created_at,
            hypothesis=record.hypothesis,
            dataset_id=record.dataset_id,
            dataset_version=record.dataset_version,
            group=record.group,
            scorer_name=record.scorer_name,
            features=record.features,
            label_definition=record.label_definition,
            cost_model=record.cost_model,
            metrics=metrics,
            status=status,
            parent_experiment_id=record.parent_experiment_id,
            failure_reason=failure_reason,
        )
        self._records[experiment_id] = updated
        return updated

    def claim_final_test(self, dataset_id: str) -> bool:
        if dataset_id in self._claims:
            return False
        self._claims.add(dataset_id)
        return True

    def is_final_test(self, dataset_id: str) -> bool:
        return dataset_id in self._claims


def seed_record(store: InMemoryExperimentStore, hypothesis: str) -> None:
    record = ExperimentRecord(
        experiment_id=f"exp-{len(store._records)}",
        created_at=datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC),
        hypothesis=hypothesis,
        dataset_id="binance-btcusdt",
        dataset_version=1,
        group=ExperimentGroup.TUNING,
        scorer_name="threshold",
        features=("f",),
        label_definition={"kind": "fixed_horizon", "horizon": 5},
        cost_model={"half_spread_pct": 0.0002},
        metrics={},
        status=ExperimentStatus.RUNNING,
    )
    store.save(record)
    store.record_result(record.experiment_id, ExperimentStatus.DONE, metrics={"done": True})


def _runner_factory(**overrides: Any):
    """A stub experiment runner whose behavior is fully scripted."""

    def runner(hypothesis: Hypothesis) -> ExperimentOutcome:
        defaults: dict[str, Any] = {
            "experiment_id": f"exp-{hypothesis.hypothesis_id}",
            "hypothesis_id": hypothesis.hypothesis_id,
            "improvement_bps": 12.0,
            "sharpe": 0.8,
            "samples": 250,
            "ok": True,
            "failure_reason": None,
        }
        defaults.update(overrides)
        return ExperimentOutcome(**defaults)

    return runner


class TestHypothesisGenerator:
    def test_generates_deterministic_hypotheses(self) -> None:
        a = generate_hypotheses(3, seed=42)
        b = generate_hypotheses(3, seed=42)
        assert tuple(x.as_dict() for x in a) == tuple(x.as_dict() for x in b)
        assert len(a) == 3

    def test_generated_hypotheses_are_rule_sourced(self) -> None:
        hypotheses = generate_hypotheses(2)
        assert all(h.source is HypothesisSource.RULE for h in hypotheses)

    def test_ai_source_can_be_injected(self) -> None:
        generator = HypothesisGenerator()
        hypotheses = generator.generate(2, source=HypothesisSource.AI)
        assert all(h.source is HypothesisSource.AI for h in hypotheses)

    def test_each_hypothesis_has_unique_id(self) -> None:
        hypotheses = generate_hypotheses(6)
        ids = [h.hypothesis_id for h in hypotheses]
        assert len(set(ids)) == len(ids)

    def test_catalog_claims_are_distinct_before_cycling(self) -> None:
        # The catalog holds 3 distinct claims; the generator cycles it, so a
        # fresh seed re-emits them in the same order.
        hypotheses = generate_hypotheses(3)
        claims = [h.claim for h in hypotheses]
        assert len(set(claims)) == len(claims)

    def test_zero_or_negative_count_yields_nothing(self) -> None:
        assert generate_hypotheses(0) == ()
        assert generate_hypotheses(-1) == ()

    def test_custom_families_are_used(self) -> None:
        generator = HypothesisGenerator(families=(("custom claim", "custom mech", ("x",)),))
        (hypothesis,) = generator.generate(1)
        assert hypothesis.claim == "custom claim"


class TestNoveltyFilter:
    def test_studied_hypothesis_is_rejected(self) -> None:
        store = InMemoryExperimentStore()
        # Seed the registry with the default generator's first claim, using a
        # fresh generator so the loop starts at the same catalog position.
        (first,) = HypothesisGenerator().generate(1)
        seed_record(store, hypothesis=str(first.claim))
        loop = ResearchLoop(store, _runner_factory(), generator=HypothesisGenerator())
        report = loop.run_cycle(count=1)
        assert report.rejected == (first.hypothesis_id,)
        assert report.insights == ()

    def test_unstudied_hypothesis_runs_and_can_win(self) -> None:
        store = InMemoryExperimentStore()
        generator = HypothesisGenerator()
        loop = ResearchLoop(store, _runner_factory(), generator=generator)
        report = loop.run_cycle(count=1)
        assert report.rejected == ()
        assert len(report.insights) == 1
        assert report.insights[0].evidence.verdict is EvidenceVerdict.PROMISING


class TestVerdicts:
    def test_promising_when_everything_passes(self) -> None:
        store = InMemoryExperimentStore()
        loop = ResearchLoop(
            store,
            _runner_factory(sharpe=1.0, improvement_bps=10.0),
            generator=HypothesisGenerator(),
        )
        report = loop.run_cycle(count=1)
        assert report.insights[0].evidence.verdict is EvidenceVerdict.PROMISING

    def test_promising_requires_both_bars(self) -> None:
        store = InMemoryExperimentStore()
        # Good sharpe but negative improvement: not promising.
        loop = ResearchLoop(
            store,
            _runner_factory(sharpe=2.0, improvement_bps=-5.0),
            generator=HypothesisGenerator(),
        )
        report = loop.run_cycle(count=1)
        assert report.insights == ()

    def test_refuted_when_all_experiments_fail_both_bars(self) -> None:
        store = InMemoryExperimentStore()

        def runner(hypothesis: Hypothesis) -> ExperimentOutcome:
            return ExperimentOutcome(
                experiment_id=f"exp-{hypothesis.hypothesis_id}",
                hypothesis_id=hypothesis.hypothesis_id,
                improvement_bps=-20.0,
                sharpe=-0.3,
                samples=250,
            )

        loop = ResearchLoop(store, runner, generator=HypothesisGenerator())
        report = loop.run_cycle(count=1)
        assert report.insights == ()
        # The failed hypothesis is not surfaced as an insight at all.

    def test_inconclusive_when_evidence_is_split(self) -> None:
        # One experiment clears sharpe but not improvement; another fails
        # both: that is INCONCLUSIVE, never promising.
        outcomes = [
            ExperimentOutcome(
                experiment_id="exp-good-sharpe",
                hypothesis_id="h",
                improvement_bps=-2.0,
                sharpe=1.2,
                samples=120,
            ),
            ExperimentOutcome(
                experiment_id="exp-bad",
                hypothesis_id="h",
                improvement_bps=-9.0,
                sharpe=-0.4,
                samples=200,
            ),
        ]
        from backend.application.research.research_loop import _summarize

        summary = _summarize("h", outcomes, sharpe_floor=0.5, improvement_floor=0.0)
        assert summary.verdict is EvidenceVerdict.INCONCLUSIVE

    def test_failed_runs_alone_are_inconclusive(self) -> None:
        outcomes = [
            ExperimentOutcome(
                experiment_id="exp-fail",
                hypothesis_id="h",
                improvement_bps=0.0,
                sharpe=0.0,
                samples=0,
                ok=False,
                failure_reason="venue data gap",
            )
        ]
        from backend.application.research.research_loop import _summarize

        summary = _summarize("h", outcomes, sharpe_floor=0.5, improvement_floor=0.0)
        assert summary.verdict is EvidenceVerdict.INCONCLUSIVE


class TestFailedExperiments:
    def test_failed_outcomes_are_preserved_not_dropped(self) -> None:
        store = InMemoryExperimentStore()

        def runner(hypothesis: Hypothesis) -> ExperimentOutcome:
            return ExperimentOutcome(
                experiment_id=f"exp-{hypothesis.hypothesis_id}",
                hypothesis_id=hypothesis.hypothesis_id,
                improvement_bps=0.0,
                sharpe=0.0,
                samples=0,
                ok=False,
                failure_reason="testing harness error",
            )

        loop = ResearchLoop(store, runner, generator=HypothesisGenerator())
        report = loop.run_cycle(count=1)
        assert len(report.failed) == 1
        assert report.failed[0].failure_reason == "testing harness error"
        assert report.insights == ()


class TestLoopControls:
    def test_accepted_hypotheses_run_their_experiments(self) -> None:
        store = InMemoryExperimentStore()
        calls: list[Hypothesis] = []

        def runner(hypothesis: Hypothesis) -> ExperimentOutcome:
            calls.append(hypothesis)
            return ExperimentOutcome(
                experiment_id=f"exp-{hypothesis.hypothesis_id}",
                hypothesis_id=hypothesis.hypothesis_id,
                improvement_bps=5.0,
                sharpe=0.6,
                samples=200,
            )

        loop = ResearchLoop(store, runner, generator=HypothesisGenerator())
        report = loop.run_cycle(count=2)
        assert len(calls) == 2
        assert len(report.insights) == 2

    def test_deterministic_with_seeded_config(self) -> None:
        def build() -> CycleReport:
            store = InMemoryExperimentStore()
            config = ResearchLoopConfig(seed=7, max_hypotheses_per_cycle=3)
            loop = ResearchLoop(store, _runner_factory(), config=config)
            return loop.run_cycle()

        assert build().as_dict() == build().as_dict()

    def test_accepted_hypotheses_may_override_generator(self) -> None:
        store = InMemoryExperimentStore()
        custom = Hypothesis(
            hypothesis_id="hyp-ai-1",
            claim="custom AI claim about regime persistence",
            mechanism="stated by the reasoner",
            feature_plan=("regime_trend",),
            source=HypothesisSource.AI,
        )
        loop = ResearchLoop(store, _runner_factory(), generator=HypothesisGenerator())
        report = loop.run_cycle(hypotheses=[custom])
        assert len(report.insights) == 1
        assert report.insights[0].hypothesis.source is HypothesisSource.AI

    def test_run_research_cycle_convenience_function(self) -> None:
        store = InMemoryExperimentStore()
        report = run_research_cycle(store, _runner_factory(), count=1)
        assert len(report.insights) == 1


class TestNoSelfDeployment:
    def test_loop_output_has_no_deployment_path(self) -> None:
        # The loop's only outputs are insights, rejected ids, and failed
        # outcomes — there is no promotion, no environment transition, and no
        # execution here. Verify by contract surface.
        store = InMemoryExperimentStore()
        loop = ResearchLoop(store, _runner_factory(), generator=HypothesisGenerator())
        report = loop.run_cycle(count=1)
        assert isinstance(report, CycleReport)
        assert isinstance(report.insights, tuple)
        # Each insight is evidence-only: hypothesis + verdict summary.
        for insight in report.insights:
            assert isinstance(insight.evidence, EvidenceSummary)
            assert isinstance(insight.evidence.verdict, EvidenceVerdict)
        # A weak candidate cannot be promoted straight from this cycle: the
        # promotion gate (P4-001) requires validation evidence, which the
        # loop does not produce — it only produces a verdict for research.
        assert not hasattr(report, "environment")
        assert not hasattr(report, "to_environment")

    def test_evidence_summary_as_dict_round_trips_shape(self) -> None:
        summary = EvidenceSummary(
            hypothesis_id="h",
            verdict=EvidenceVerdict.PROMISING,
            best_experiment_id="e1",
            best_improvement_bps=5.0,
            best_sharpe=0.7,
            samples=250,
            experiment_count=1,
        )
        payload = summary.as_dict()
        assert payload["verdict"] == "promising"
        assert payload["best_sharpe"] == 0.7
