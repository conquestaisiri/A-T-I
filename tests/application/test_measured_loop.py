"""Tests for the measured research feedback loop (T3-30-1).

The loop must land a passport on the ledger per successful iteration,
record misses honestly (no report / error / ledger refusal), and measure
loop quality from passport survival read at measurement time — so a
death-system retirement after issue lowers the loop's survival rate. With
nothing issued, no survival rate is reported, ever.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.decision_pipeline_evaluator import (
    OutOfSampleReport,
)
from backend.application.research.evidence_engine import EvidenceEngine
from backend.application.research.measured_loop import (
    MeasuredLoopConfig,
    MeasuredResearchLoop,
)
from backend.domain.research.hypothesis import Hypothesis, HypothesisSource
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import PassportStatus
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import (
    SqlitePassportRepository,
)


def engine(tmp_path: object) -> EvidenceEngine:
    return EvidenceEngine(SqlitePassportRepository(Database(tmp_path / "p.db")))  # type: ignore[operator]


def hypothesis(hypothesis_id: str, claim: str = "OFI predicts the next return") -> Hypothesis:
    return Hypothesis(
        hypothesis_id=hypothesis_id,
        claim=claim,
        mechanism="order-flow imbalance contains information",
        source=HypothesisSource.RULE,
    )


def pooled(mean_excess: float, deflated_sharpe: float | None = 1.1) -> PooledEvidence:
    return PooledEvidence(
        n_folds=8,
        total_test_bars=160,
        total_trades=40,
        total_wins=22,
        total_losses=18,
        total_fees=12.0,
        total_slippage_bps=3.5,
        gross_profit=250.0,
        gross_loss=180.0,
        mean_return_pct=1.2,
        median_return_pct=0.9,
        mean_excess_return_pct=mean_excess,
        positive_fold_rate=0.75,
        beats_buy_and_hold_rate=0.75,
        mean_max_drawdown_pct=-8.0,
        deflated_sharpe=deflated_sharpe,
        reasoner="RuleBasedSolver",
        cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
    )


def report(pooled_evidence: PooledEvidence) -> OutOfSampleReport:
    return OutOfSampleReport(
        symbol="btcusdt",
        costs=EvaluationCosts(half_spread_pct=0.0002, taker_fee_pct=0.0004),
        cv_spec={"train_size": 80, "test_size": 20, "expanding": True},
        folds=(),
        pooled=pooled_evidence,
    )


def evaluator(
    pooled_by_id: dict[str, PooledEvidence],
) -> Callable[[Hypothesis], OutOfSampleReport | None]:
    def evaluate(h: Hypothesis) -> OutOfSampleReport | None:
        pooled_evidence = pooled_by_id.get(h.hypothesis_id)
        return report(pooled_evidence) if pooled_evidence is not None else None

    return evaluate


class TestLoop:
    def test_each_iteration_issues_a_passport(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7), "H2": pooled(0.4)}),
            MeasuredLoopConfig(run_id="L1"),
        )
        result = loop.run([hypothesis("H1"), hypothesis("H2")])
        assert len(result.records) == 2
        assert [r.passport_id for r in result.records] == ["STRAT-L1-001", "STRAT-L1-002"]
        assert all(r.status == "candidate" for r in result.records)
        assert result.quality.passports_issued == 2
        assert result.quality.survival_rate == pytest.approx(1.0)
        assert result.quality.promoted == 2

    def test_miss_is_recorded_not_fabricated(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7)}),
            MeasuredLoopConfig(run_id="L2"),
        )
        result = loop.run([hypothesis("H1"), hypothesis("H2")])
        miss = result.records[1]
        assert miss.passport_id is None
        assert miss.verdict is None
        assert "no report" in miss.reason
        assert result.quality.passports_issued == 1
        assert result.quality.survival_rate == pytest.approx(1.0)

    def test_nothing_issued_has_no_survival_rate(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({}),
            MeasuredLoopConfig(run_id="L3"),
        )
        result = loop.run([hypothesis("H1")])
        assert result.quality.survival_rate is None
        assert "would be fabricated" in result.quality.unavailable_reason

    def test_evaluation_error_is_recorded(self, tmp_path: object) -> None:
        svc = engine(tmp_path)

        def broken(h: Hypothesis) -> OutOfSampleReport | None:
            raise RuntimeError("boom")

        loop = MeasuredResearchLoop(svc, broken, MeasuredLoopConfig(run_id="L4"))
        result = loop.run([hypothesis("H1")])
        assert result.records[0].passport_id is None
        assert "evaluation error: boom" in result.records[0].reason
        assert result.quality.survival_rate is None

    def test_rejected_on_arrival_counts_as_dead(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7), "H2": pooled(-1.0, deflated_sharpe=-1.0)}),
            MeasuredLoopConfig(run_id="L5"),
        )
        result = loop.run([hypothesis("H1"), hypothesis("H2")])
        records = {r.passport_id: r for r in result.records}
        assert records["STRAT-L5-002"].status == "retired"  # dead on arrival
        assert result.quality.dead == 1
        assert result.quality.rejected == 1
        assert result.quality.survival_rate == pytest.approx(0.5)

    def test_survival_reads_the_ledger_at_measurement_time(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7), "H2": pooled(0.4)}),
            MeasuredLoopConfig(run_id="L6"),
        )
        loop.run([hypothesis("H1"), hypothesis("H2")])
        assert loop.quality().survival_rate == pytest.approx(1.0)
        # The death system retires H2's passport afterwards: survival drops.
        svc.rerecord_evidence(
            "STRAT-L6-002",
            report=report(pooled(-1.0, deflated_sharpe=-1.0)),
            reason="rejection gate",
        )
        assert svc.passport("STRAT-L6-002") is not None
        assert svc.passport("STRAT-L6-002").status is PassportStatus.RETIRED  # type: ignore[union-attr]
        quality = loop.quality()
        assert quality.alive == 1
        assert quality.dead == 1
        assert quality.survival_rate == pytest.approx(0.5)

    def test_window_limits_the_measure(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7), "H2": pooled(0.4)}),
            MeasuredLoopConfig(run_id="L7"),
        )
        loop.run([hypothesis("H1"), hypothesis("H2")])
        windowed = loop.quality(window=1)
        assert windowed.passports_issued == 1
        assert windowed.alive == 1
        assert windowed.survival_rate == pytest.approx(1.0)

    def test_duplicate_ledger_refusal_is_recorded(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-L8-001",
            hypothesis="pre-existing",
            dataset_id="btcusdt",
            dataset_version=1,
            features=(),
            model="m",
            trial_count=1,
            report=report(pooled(0.7)),
        )
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7)}),
            MeasuredLoopConfig(run_id="L8"),
        )
        result = loop.run([hypothesis("H1")])
        record = result.records[0]
        assert record.passport_id is None
        assert "passport refused" in record.reason
        assert result.quality.survival_rate is None  # nothing this loop issued

    def test_max_iterations_bounds_the_run(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7), "H2": pooled(0.4), "H3": pooled(0.4)}),
            MeasuredLoopConfig(run_id="L9", max_iterations=2),
        )
        result = loop.run([hypothesis("H1"), hypothesis("H2"), hypothesis("H3")])
        assert len(result.records) == 2
        assert result.records[1].passport_id == "STRAT-L9-002"

    def test_hypothesis_feature_plan_wins_over_default(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7)}),
            MeasuredLoopConfig(run_id="L10", features=("spread",)),
        )
        hypothesis_with_plan = Hypothesis(
            hypothesis_id="H1",
            claim="c",
            mechanism="m",
            feature_plan=("ofi",),
        )
        result = loop.run([hypothesis_with_plan])
        passport_id = result.records[0].passport_id
        assert passport_id is not None
        passport = svc.passport(passport_id)
        assert passport is not None
        assert passport.features == ("ofi",)

    def test_verdict_mix_reported(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator(
                {
                    "H1": pooled(0.7),
                    "H2": pooled(0.4, deflated_sharpe=None),  # too few folds -> OBSERVE
                    "H3": pooled(-1.0, deflated_sharpe=-1.0),  # REJECT
                }
            ),
            MeasuredLoopConfig(run_id="L11"),
        )
        result = loop.run([hypothesis("H1"), hypothesis("H2"), hypothesis("H3")])
        assert result.quality.promoted == 1
        assert result.quality.observed == 1
        assert result.quality.rejected == 1
        assert result.quality.survival_rate == pytest.approx(2.0 / 3.0)

    def test_report_roundtrips_to_dict(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        loop = MeasuredResearchLoop(
            svc,
            evaluator({"H1": pooled(0.7)}),
            MeasuredLoopConfig(run_id="L12"),
        )
        result = loop.run([hypothesis("H1")])
        payload = result.as_dict()
        assert payload["records"][0]["passport_id"] == "STRAT-L12-001"
        assert payload["quality"]["survival_rate"] == 1.0
