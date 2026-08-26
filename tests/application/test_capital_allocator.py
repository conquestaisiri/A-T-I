"""Tests for the portfolio capital allocation service (T3-29-1).

The guardrail is the subject: capital is allocated ONLY to passports whose
pooled evidence passed the verdict gates (PROMOTE_TO_PAPER, not retired).
Every exclusion is named. The plan is honest (no eligible -> allocation
None), the sizing reuses the T2-14-1 correlation-damped allocator, and
rebalance() turns the plan into exact per-strategy deltas (excluded
strategies must exit — the gate rule is hard, not negotiable).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.capital_allocator import CapitalAllocationService
from backend.application.research.decision_pipeline_evaluator import OutOfSampleReport
from backend.application.research.evidence_engine import EvidenceEngine
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
)
from backend.domain.research.portfolio_correlations import (
    PairCorrelation,
    PairCorrelationState,
    PortfolioCorrelationMatrix,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import SqlitePassportRepository


def store(tmp_path: Any) -> SqlitePassportRepository:
    return SqlitePassportRepository(Database(tmp_path / "p.db"))


def engine(st: SqlitePassportRepository) -> EvidenceEngine:
    return EvidenceEngine(st)


def base_kwargs() -> dict[str, Any]:
    return {
        "hypothesis": "OFI predicts 1-minute return",
        "dataset_id": "btcusdt",
        "dataset_version": 1,
        "features": ("ofi", "spread"),
        "model": "RuleBasedSolver",
        "trial_count": 50,
        "train_period": ("2021-01-01", "2024-12-31"),
        "validation_period": ("2025-01-01", "2025-12-31"),
        "test_period": ("2026-01-01", "2026-06-30"),
        "experiment_id": "EXP-1",
    }


def report_with(pooled: PooledEvidence) -> OutOfSampleReport:
    return OutOfSampleReport(
        symbol="btcusdt",
        costs=EvaluationCosts(half_spread_pct=0.0002, taker_fee_pct=0.0004),
        cv_spec={"train_size": 80, "test_size": 20, "expanding": True},
        folds=(),
        pooled=pooled,
    )


def pooled(*, excess: float, sharpe: float | None = 1.1, folds: int = 8) -> PooledEvidence:
    return PooledEvidence(
        n_folds=folds,
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
        mean_excess_return_pct=excess,
        positive_fold_rate=0.75,
        beats_buy_and_hold_rate=0.75,
        mean_max_drawdown_pct=-8.0,
        deflated_sharpe=sharpe,
        reasoner="RuleBasedSolver",
        cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
    )


def matrix(ids: tuple[str, ...], rho: float = 0.0) -> PortfolioCorrelationMatrix:
    n = len(ids)
    rows = tuple(tuple(1.0 if i == j else rho for j in range(n)) for i in range(n))
    pairs = tuple(
        PairCorrelation(
            left=ids[i],
            right=ids[j],
            state=PairCorrelationState.MEASURED,
            value=rho,
            n_shared=160,
        )
        for i in range(n)
        for j in range(i + 1, n)
    )
    return PortfolioCorrelationMatrix(ids=ids, matrix=rows, pairs=pairs)


def issue(svc: EvidenceEngine, pid: str, report: OutOfSampleReport) -> None:
    svc.issue_passport(passport_id=pid, **base_kwargs(), report=report)


class TestEvidenceGate:
    def test_only_gate_passing_passports_get_capital(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))  # PROMOTE_TO_PAPER
        # sharpe <= 0 -> REJECT, which dies on arrival (RETIRED tombstone).
        issue(svc, "STRAT-B", report_with(pooled(excess=0.9, sharpe=-0.2)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A", "STRAT-B")))
        assert plan.allocation is not None
        by_id = {v.passport_id: v for v in plan.verdicts}
        assert by_id["STRAT-A"].eligible is True
        assert by_id["STRAT-A"].score == pytest.approx(1.5)
        assert by_id["STRAT-B"].eligible is False
        assert "retired" in by_id["STRAT-B"].reason  # REJECT verdicts die on arrival
        weights = {w.strategy_id: w.weight for w in plan.allocation.weights}
        assert weights["STRAT-B"] == 0.0
        assert weights["STRAT-A"] == pytest.approx(1.0)

    def test_observe_gets_no_capital(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5, sharpe=None)))  # OBSERVE
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A",)))
        verdict = plan.verdicts[0]
        assert verdict.eligible is False
        assert "insufficient evidence" in verdict.reason
        assert plan.allocation is None
        assert "no passport" in plan.unavailable_reason

    def test_retired_passport_gets_no_capital(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        svc.transition("STRAT-A", to_status=PassportStatus.RETIRED, reason="death")
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A",)))
        assert plan.verdicts[0].eligible is False
        assert "retired" in plan.verdicts[0].reason
        assert plan.allocation is None

    def test_unevaluated_passport_gets_no_capital(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        st.save_passport(
            StrategyPassport(
                passport_id="STRAT-RAW",
                created_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
                hypothesis="unevaluated",
                dataset_id="d",
                dataset_version=1,
                features=(),
                model="m",
                trial_count=1,
                evidence={},
                verdict=PassportVerdict(verdict=EvidenceVerdict.OBSERVE),
                status=PassportStatus.RESEARCH,
            )
        )
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A", "STRAT-RAW")))
        by_id = {v.passport_id: v for v in plan.verdicts}
        assert by_id["STRAT-RAW"].eligible is False
        assert "no evaluated evidence" in by_id["STRAT-RAW"].reason

    def test_negative_excess_gets_no_capital(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=-0.5)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A",)))
        assert plan.verdicts[0].eligible is False
        assert "minimum" in plan.verdicts[0].reason
        assert plan.allocation is None

    def test_empty_population_is_honest(self, tmp_path: object) -> None:
        st = store(tmp_path)
        plan = CapitalAllocationService(st).plan(matrix(()))
        assert plan.allocation is None
        assert plan.verdicts == ()
        assert "no passport" in plan.unavailable_reason

    def test_eligible_missing_from_matrix_refused(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        with pytest.raises(ValueError, match="missing from the correlation matrix"):
            CapitalAllocationService(st).plan(matrix(("STRAT-OTHER",)))


class TestSizing:
    def test_correlation_discounts_redundancy(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        issue(svc, "STRAT-B", report_with(pooled(excess=1.5)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A", "STRAT-B"), rho=0.9))
        assert plan.allocation is not None
        weights = {w.strategy_id: w.weight for w in plan.allocation.weights}
        assert weights["STRAT-A"] == pytest.approx(0.5)
        assert weights["STRAT-B"] == pytest.approx(0.5)

    def test_independent_strategies_keep_full_weight(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        issue(svc, "STRAT-B", report_with(pooled(excess=1.5)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A", "STRAT-B"), rho=0.0))
        allocation = plan.allocation
        assert allocation is not None
        weights = {w.strategy_id: w.weight for w in allocation.weights}
        assert weights["STRAT-A"] == pytest.approx(0.5)

    def test_weights_sum_to_one(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        for i, pid in enumerate(("STRAT-A", "STRAT-B", "STRAT-C")):
            issue(svc, pid, report_with(pooled(excess=1.0 + i)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A", "STRAT-B", "STRAT-C"), rho=0.3))
        assert plan.allocation is not None
        total = sum(w.weight for w in plan.allocation.weights)
        assert total == pytest.approx(1.0)


class TestRebalance:
    def test_deltas_move_from_current_to_target(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        issue(svc, "STRAT-B", report_with(pooled(excess=0.9, sharpe=-0.2)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A", "STRAT-B")))
        deltas = CapitalAllocationService(st).rebalance(
            {"STRAT-A": 0.6, "STRAT-B": 0.4},
            plan,
        )
        by_id = {d.passport_id: d for d in deltas}
        assert by_id["STRAT-A"].target_weight == pytest.approx(1.0)
        assert by_id["STRAT-A"].delta == pytest.approx(0.4)
        assert by_id["STRAT-B"].target_weight == 0.0
        assert by_id["STRAT-B"].delta == pytest.approx(-0.4)
        assert "retired" in by_id["STRAT-B"].reason

    def test_excluded_strategies_must_exit(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        svc.transition("STRAT-A", to_status=PassportStatus.RETIRED, reason="death")
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A",)))
        deltas = CapitalAllocationService(st).rebalance({"STRAT-A": 1.0}, plan)
        assert deltas[0].target_weight == 0.0
        assert deltas[0].delta == pytest.approx(-1.0)
        assert "retired" in deltas[0].reason

    def test_deltas_cover_union_of_ids(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A",)))
        deltas = CapitalAllocationService(st).rebalance(
            {"STRAT-A": 0.5, "STRAT-OLD": 0.5},
            plan,
        )
        by_id = {d.passport_id: d for d in deltas}
        assert by_id["STRAT-OLD"].target_weight == 0.0
        assert by_id["STRAT-OLD"].delta == pytest.approx(-0.5)

    def test_no_change_means_zero_deltas(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A",)))
        deltas = CapitalAllocationService(st).rebalance({"STRAT-A": 1.0}, plan)
        assert deltas[0].delta == pytest.approx(0.0)


class TestPlanShape:
    def test_plan_roundtrips_through_dict(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=1.5)))
        issue(svc, "STRAT-B", report_with(pooled(excess=0.9, sharpe=-0.2)))
        plan = CapitalAllocationService(st).plan(matrix(("STRAT-A", "STRAT-B"), rho=0.5))
        as_dict = plan.as_dict()
        assert as_dict["allocation"] is not None
        assert len(as_dict["verdicts"]) == 2
        assert as_dict["correlation_sensitivity"] == 1.0
        assert sum(w["weight"] for w in as_dict["allocation"]["weights"]) == pytest.approx(1.0)

    def test_min_score_floor_is_configurable(self, tmp_path: object) -> None:
        st = store(tmp_path)
        svc = engine(st)
        issue(svc, "STRAT-A", report_with(pooled(excess=0.5)))
        plan = CapitalAllocationService(st, min_score=1.0).plan(matrix(("STRAT-A",)))
        assert plan.verdicts[0].eligible is False
        assert "minimum" in plan.verdicts[0].reason
        assert plan.allocation is None
