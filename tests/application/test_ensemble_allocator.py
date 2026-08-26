"""Tests for the ensemble/competition allocator wiring (T2-13-1).

The wiring must:

1. Let only evidence-gate-passing candidates compete (verdict
   PROMOTE_TO_PAPER, not RETIRED); everyone else is excluded with a reason.
2. Never fabricate volatility: a competing passport without an
   operator-supplied estimate is excluded, and pooled mean excess is the
   only expected-return input.
3. Use the T2-11-1 regime robustness score as regime fit when present.
4. Never bypass the risk gate: a blocked allocation stays blocked with
   zero weights.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pytest
from backend.application.research.ensemble_allocator import EnsembleAllocator
from backend.application.research.strategy_population import (
    StrategyPopulationService,
)
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import (
    SqlitePassportRepository,
)


def store(tmp_path):
    return SqlitePassportRepository(Database(tmp_path / "p.db"))


def passport(
    passport_id: str,
    *,
    verdict: EvidenceVerdict = EvidenceVerdict.PROMOTE_TO_PAPER,
    status: PassportStatus = PassportStatus.CANDIDATE,
    mean_excess: float = 0.5,
    dsr: float = 1.0,
    regime_score: float | None = None,
) -> StrategyPassport:
    evidence = {
        "pooled": {
            "n_folds": 8,
            "total_test_bars": 160,
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "total_fees": 0.0,
            "total_slippage_bps": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "mean_return_pct": mean_excess + 0.2,
            "median_return_pct": mean_excess,
            "mean_excess_return_pct": mean_excess,
            "positive_fold_rate": 0.7,
            "beats_buy_and_hold_rate": 0.7,
            "mean_max_drawdown_pct": -5.0,
            "deflated_sharpe": dsr,
            "reasoner": "RuleBasedSolver",
            "cost_model": {"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
        }
    }
    if regime_score is not None:
        evidence["regime_evidence"] = {"robustness_score": regime_score}
    return StrategyPassport(
        passport_id=passport_id,
        created_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        hypothesis=f"hypothesis of {passport_id}",
        dataset_id="btcusdt",
        dataset_version=1,
        features=("trend", "momentum"),
        model="RuleBasedSolver",
        trial_count=10,
        evidence=evidence,
        verdict=PassportVerdict(verdict),
        status=status,
    )


def allocator(tmp_path) -> EnsembleAllocator:
    return EnsembleAllocator(StrategyPopulationService(store(tmp_path)))


def vols(*ids: str, value: float = 10.0) -> dict[str, float]:
    return {pid: value for pid in ids}


class TestEligibilityGates:
    def test_rejected_candidates_are_excluded(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("good", verdict=EvidenceVerdict.PROMOTE_TO_PAPER))
        s.save_passport(passport("bad", verdict=EvidenceVerdict.REJECT))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("good", "bad")
        )
        assert result.competitors == ("good",)
        assert any(pid == "bad" for pid, _ in result.excluded)
        assert any("verdict 'reject'" in reason for _, reason in result.excluded)

    def test_observe_candidates_are_excluded(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("good", verdict=EvidenceVerdict.PROMOTE_TO_PAPER))
        s.save_passport(passport("maybe", verdict=EvidenceVerdict.OBSERVE))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("good", "maybe")
        )
        assert result.competitors == ("good",)
        assert any(
            "evidence gates did not pass" in reason
            for pid, reason in result.excluded
            if pid == "maybe"
        )

    def test_research_candidates_are_excluded(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("good", verdict=EvidenceVerdict.PROMOTE_TO_PAPER))
        s.save_passport(
            passport("planned", verdict=EvidenceVerdict.OBSERVE, status=PassportStatus.RESEARCH)
        )
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("good", "planned")
        )
        assert result.competitors == ("good",)
        assert any(pid == "planned" for pid, _ in result.excluded)

    def test_retired_candidates_are_excluded_even_with_good_verdict(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("good", verdict=EvidenceVerdict.PROMOTE_TO_PAPER))
        s.save_passport(
            passport(
                "dead",
                verdict=EvidenceVerdict.PROMOTE_TO_PAPER,
                status=PassportStatus.RETIRED,
            )
        )
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("good", "dead")
        )
        assert result.competitors == ("good",)
        assert any(pid == "dead" and "retired" in reason for pid, reason in result.excluded)

    def test_no_volatility_estimate_excludes_competitor(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("good"))
        s.save_passport(passport("no_vol"))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("good")
        )
        assert result.competitors == ("good",)
        assert any("volatility" in reason for pid, reason in result.excluded if pid == "no_vol")

    def test_non_positive_volatility_excludes_competitor(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("good"))
        s.save_passport(passport("bad_vol"))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id={"good": 10.0, "bad_vol": -5.0},
        )
        assert result.competitors == ("good",)
        assert any(pid == "bad_vol" for pid, _ in result.excluded)

    def test_unknown_volatility_ids_are_ignored(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("good"))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id={"good": 10.0, "ghost": 20.0},
        )
        assert result.competitors == ("good",)

    def test_no_eligible_candidates_returns_no_allocation(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("rejected", verdict=EvidenceVerdict.REJECT))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("rejected")
        )
        assert result.allocation is None
        assert result.competitors == ()
        assert "no candidate" in result.reason


class TestAllocationBehaviour:
    def test_allocates_risk_budget_across_competitors(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a", mean_excess=0.5))
        s.save_passport(passport("b", mean_excess=0.4))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "b", value=10.0),
        )
        allocation = result.allocation
        assert allocation is not None
        assert allocation.status == "allocated"
        assert allocation.weight_for("a") == pytest.approx(math.sqrt(0.5), abs=1e-6)
        assert allocation.weight_for("b") == pytest.approx(math.sqrt(0.5), abs=1e-6)
        assert allocation.portfolio_volatility_pct == pytest.approx(10.0, abs=1e-6)

    def test_expected_return_is_pooled_mean_excess(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a", mean_excess=2.0))
        s.save_passport(passport("b", mean_excess=0.5))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "b", value=10.0),
        )
        allocation = result.allocation
        assert allocation is not None
        # Equal vols/fits => equal risk-parity weights of sqrt(0.5) each.
        w = math.sqrt(0.5)
        assert allocation.portfolio_expected_return_pct == pytest.approx(
            w * 2.0 + w * 0.5, abs=1e-5
        )

    def test_higher_volatility_earns_less_budget(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("risky", mean_excess=2.0))
        s.save_passport(passport("calm", mean_excess=2.0))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id={"risky": 20.0, "calm": 10.0},
        )
        allocation = result.allocation
        assert allocation is not None
        assert allocation.weight_for("calm") == pytest.approx(
            2 * allocation.weight_for("risky"), abs=1e-5
        )

    def test_regime_robustness_reweights_competition(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("robust", mean_excess=2.0, regime_score=1.0))
        s.save_passport(passport("fragile", mean_excess=2.0, regime_score=0.5))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("robust", "fragile", value=10.0),
        )
        allocation = result.allocation
        assert allocation is not None
        assert allocation.weight_for("robust") == pytest.approx(
            2 * allocation.weight_for("fragile"), abs=1e-5
        )

    def test_min_regime_fit_eliminates_candidates(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("robust", mean_excess=2.0, regime_score=1.0))
        s.save_passport(passport("fragile", mean_excess=2.0, regime_score=0.5))
        result = EnsembleAllocator(
            StrategyPopulationService(store(tmp_path)), min_regime_fit=0.8
        ).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("robust", "fragile", value=10.0),
        )
        allocation = result.allocation
        assert allocation is not None
        assert allocation.weight_for("robust") == pytest.approx(1.0, abs=1e-6)
        assert allocation.weight_for("fragile") == pytest.approx(0.0)

    def test_risk_gate_block_stays_blocked(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("b"))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "b"),
            risk_gate_allowed=False,
            blocked_reason="toxicity veto",
        )
        allocation = result.allocation
        assert allocation is not None
        assert allocation.blocked
        assert allocation.blocked_reason == "toxicity veto"
        assert all(a.weight == 0.0 for a in allocation.allocations)
        assert result.reason == "risk gate blocked the allocation"

    def test_accepts_built_registry(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("b"))
        registry = StrategyPopulationService(s).registry()
        result = EnsembleAllocator(registry).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("a", "b")
        )
        assert result.allocation is not None
        assert result.allocation.status == "allocated"

    def test_invalid_min_regime_fit(self, tmp_path):
        with pytest.raises(ValueError, match="min_regime_fit"):
            EnsembleAllocator(StrategyPopulationService(store(tmp_path)), min_regime_fit=1.5)

    def test_as_dict(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("bad", verdict=EvidenceVerdict.REJECT))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("a", "bad")
        )
        payload = result.as_dict()
        assert payload["competitors"] == ["a"]
        assert len(payload["excluded"]) == 1
        assert payload["excluded"][0]["passport_id"] == "bad"
        assert payload["allocation"] is not None
        assert payload["allocation"]["status"] == "allocated"

    def test_deterministic_across_calls(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a", mean_excess=0.5, regime_score=0.9))
        s.save_passport(passport("b", mean_excess=0.3, regime_score=0.6))
        svc = StrategyPopulationService(s)
        first = EnsembleAllocator(svc).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("a", "b")
        )
        second = EnsembleAllocator(svc).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("a", "b")
        )
        assert first.as_dict() == second.as_dict()


class TestCorrelationWiring:
    """T2-13-2: correlation input from shared OOS return series."""

    def test_both_correlation_sources_are_a_contradiction(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("b"))
        with pytest.raises(ValueError, match="not both"):
            allocator(tmp_path).allocate(
                risk_budget_pct=10.0,
                volatility_pct_by_id=vols("a", "b"),
                correlations=[[1.0, 0.0], [0.0, 1.0]],
                returns_by_id={"a": [1.0, 2.0], "b": [2.0, 1.0]},
            )

    def test_series_source_produces_an_allocation(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a", mean_excess=0.5))
        s.save_passport(passport("b", mean_excess=0.5))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "b"),
            returns_by_id={"a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0]},
        )
        assert result.allocation is not None
        assert result.allocation.status == "allocated"
        assert result.competitors == ("a", "b")
        assert result.excluded == ()

    def test_anti_correlation_changes_weighting_vs_identity(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a", mean_excess=0.5))
        s.save_passport(passport("b", mean_excess=0.5))
        base = allocator(tmp_path).allocate(
            risk_budget_pct=10.0, volatility_pct_by_id=vols("a", "b")
        )
        correlated = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "b"),
            returns_by_id={"a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0]},
        )
        # Under the identity surface the two identical profiles split the
        # budget evenly; strong anti-correlation must change the split.
        assert correlated.allocation is not None
        assert base.allocation is not None
        weight_a_base = next(
            a.weight for a in base.allocation.allocations if a.strategy_name == "a"
        )
        weight_a_corr = next(
            a.weight for a in correlated.allocation.allocations if a.strategy_name == "a"
        )
        assert weight_a_corr != pytest.approx(weight_a_base)

    def test_series_source_excludes_candidate_without_series(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("no_series"))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "no_series"),
            returns_by_id={"a": [1.0, 2.0]},
        )
        assert result.competitors == ("a",)
        assert any(
            "return series" in reason for pid, reason in result.excluded if pid == "no_series"
        )

    def test_all_candidates_without_series_yields_no_allocation(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("b"))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "b"),
            returns_by_id={},
        )
        assert result.allocation is None
        assert result.competitors == ()
        assert len(result.excluded) == 2
        assert "no candidate passed the evidence gates with a return series" in result.reason

    def test_misaligned_series_raise(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("b"))
        with pytest.raises(ValueError, match="lengths differ"):
            allocator(tmp_path).allocate(
                risk_budget_pct=10.0,
                volatility_pct_by_id=vols("a", "b"),
                returns_by_id={"a": [1.0, 2.0, 3.0], "b": [1.0, 2.0]},
            )

    def test_risk_gate_still_decides_with_series_source(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("a"))
        s.save_passport(passport("b"))
        result = allocator(tmp_path).allocate(
            risk_budget_pct=10.0,
            volatility_pct_by_id=vols("a", "b"),
            returns_by_id={"a": [1.0, 2.0], "b": [2.0, 1.0]},
            risk_gate_allowed=False,
            blocked_reason="turbulence veto",
        )
        assert result.allocation is not None
        assert result.allocation.status == "blocked"
        assert result.allocation.blocked_reason == "turbulence veto"
        assert all(a.weight == 0.0 for a in result.allocation.allocations)
