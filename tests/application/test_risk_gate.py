"""Unit tests for the CircuitBreakerRiskGate (veto authority)."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.risk.circuit_breaker_risk_gate import (
    CircuitBreakerRiskGate,
    RiskGateConfig,
)
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.decision.trade_plan import PreTradePlan, StopLevel
from backend.domain.risk.risk_decision import RiskVerdict


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_proposal(**overrides: Any) -> DecisionProposal:
    params: dict[str, Any] = dict(
        proposal_id="prop-1",
        correlation_id="corr-1",
        created_at=ts(),
        symbol="btcusdt",
        hypothesis=Hypothesis(
            statement="trend",
            supporting_evidence=(EvidenceItem(source="trend", summary="up", value=1.0),),
            opposing_evidence=(),
        ),
        confidence=0.8,
        uncertainty="none",
        actions=(
            ProposedAction(
                action_type=ProposedActionType.ENTER_LONG,
                size_fraction=0.1,
                order=1,
                rationale="go",
            ),
        ),
        risk_context=RiskContext(
            account_equity=100_000.0,
            open_exposure_pct=0.1,
            daily_loss_pct=0.0,
            monthly_loss_pct=0.0,
            total_loss_pct=0.0,
            drawdown_pct=0.0,
            position_count=1,
        ),
        alternatives=(),
        rationale="go",
        pre_trade_plan=PreTradePlan(
            stop_loss=StopLevel(distance_pct=0.05),
            take_profit=StopLevel(distance_pct=0.10),
            risk_per_trade_pct=0.02,
            risk_reward_ratio=2.0,
        ),
    )
    params.update(overrides)
    return DecisionProposal(**params)


def risk_context(**overrides: Any) -> RiskContext:
    params: dict[str, Any] = dict(
        account_equity=100_000.0,
        open_exposure_pct=0.1,
        daily_loss_pct=0.0,
        monthly_loss_pct=0.0,
        total_loss_pct=0.0,
        drawdown_pct=0.0,
        position_count=1,
    )
    params.update(overrides)
    return RiskContext(**params)


class TestConfigValidation:
    def test_invalid_max_loss_rejected(self):
        with pytest.raises(ValueError):
            RiskGateConfig(max_daily_loss_pct=1.5)

    def test_defaults_are_sane(self):
        config = RiskGateConfig()
        assert config.max_risk_per_trade_pct == 0.02
        assert config.max_risk_per_symbol_pct == 0.01
        assert config.max_portfolio_risk_pct == 0.03
        assert config.max_daily_loss_pct == 0.06
        assert config.max_monthly_loss_pct == 0.10
        assert config.max_drawdown_pct == 0.20
        assert config.max_total_loss_pct == 0.50
        assert config.max_fraction_of_risk_budget == 0.60
        assert config.require_exit_bracket_on_entry is True


class TestCircuitBreakers:
    def test_daily_loss_halt_rejects(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            risk_context=risk_context(daily_loss_pct=0.06),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REJECTED
        assert "Daily loss limit" in decision.reason

    def test_monthly_loss_halt_rejects(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            risk_context=risk_context(monthly_loss_pct=0.16),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REJECTED

    def test_drawdown_halt_rejects(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            risk_context=risk_context(drawdown_pct=0.30),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REJECTED

    def test_total_loss_halt_rejects(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            risk_context=risk_context(total_loss_pct=0.60),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REJECTED

    def test_threshold_at_limit_rejects(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(risk_context=risk_context(daily_loss_pct=0.06))
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REJECTED

    def test_below_daily_limit_approved(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(risk_context=risk_context(daily_loss_pct=0.05))
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.APPROVED

    def test_breaker_precedence_total_first(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            risk_context=risk_context(daily_loss_pct=0.06, total_loss_pct=0.60),
        )
        decision = gate.evaluate(proposal)
        assert "Total loss" in decision.reason


class TestSafeActions:
    @pytest.mark.parametrize(
        "action_type",
        [
            ProposedActionType.EXIT,
            ProposedActionType.SCALE_OUT,
            ProposedActionType.REDUCE_RISK,
            ProposedActionType.STAND_ASIDE,
        ],
    )
    def test_safety_actions_never_vetoed(self, action_type):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=action_type,
                    size_fraction=0.5,
                    order=1,
                    rationale="reduce risk",
                ),
            ),
            risk_context=risk_context(daily_loss_pct=0.06, total_loss_pct=0.60),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.APPROVED

    def test_no_actions_is_informational_approval(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(actions=())
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.APPROVED
        assert decision.approved_size_fraction is None


class TestSizing:
    def test_within_limits_approved_at_requested_size(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.ENTER_LONG,
                    size_fraction=0.10,
                    order=1,
                    rationale="go",
                ),
            ),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.APPROVED
        assert decision.approved_size_fraction == 0.10

    def test_above_position_cap_is_reduced(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.ENTER_LONG,
                    size_fraction=0.50,
                    order=1,
                    rationale="big",
                ),
            ),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REDUCED
        assert decision.approved_size_fraction == pytest.approx(0.20)

    def test_exposure_limit_reduces_size(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            risk_context=risk_context(open_exposure_pct=0.55),
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.ENTER_LONG,
                    size_fraction=0.20,
                    order=1,
                    rationale="go",
                ),
            ),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REDUCED
        assert decision.approved_size_fraction == pytest.approx(0.05)

    def test_exposure_full_rejects(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            risk_context=risk_context(open_exposure_pct=0.60),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.REJECTED
        assert "exposure" in decision.reason.lower()

    def test_reduced_still_approved_for_execution(self):
        gate = CircuitBreakerRiskGate()
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.ENTER_LONG,
                    size_fraction=0.50,
                    order=1,
                    rationale="big",
                ),
            ),
        )
        decision = gate.evaluate(proposal)
        assert decision.approved is True


class TestToxicityVeto:
    """VPIN toxicity veto: never add risk into a toxic book."""

    @staticmethod
    def _feed_toxic_flow(gate: CircuitBreakerRiskGate, symbol: str = "btcusdt") -> None:
        # One-sided buys -> max imbalance per bucket. Default bucket size is
        # 1000, so 10_000 units produce 10 complete buckets (>= 8 evidence).
        for _ in range(10_000):
            gate.record_toxicity_flow(symbol, 1.0)

    def test_tracked_but_calm_symbol_is_not_toxic(self):
        gate = CircuitBreakerRiskGate()
        for _ in range(10_000):
            gate.record_toxicity_flow("btcusdt", 1.0 if _ % 2 == 0 else -1.0)
        toxicity = gate.toxicity("btcusdt")
        assert toxicity is not None
        assert toxicity.toxic is False
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.APPROVED

    def test_toxic_book_rejects_risk_increasing_action(self):
        gate = CircuitBreakerRiskGate()
        self._feed_toxic_flow(gate)
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.REJECTED
        assert "Toxicity veto" in decision.reason

    def test_toxic_book_veto_respects_evidence_buckets(self):
        gate = CircuitBreakerRiskGate(
            RiskGateConfig(veto_on_toxicity=True, min_toxicity_evidence_buckets=50)
        )
        self._feed_toxic_flow(gate)  # only 10 buckets of evidence
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.APPROVED

    def test_toxicity_veto_can_be_disabled(self):
        gate = CircuitBreakerRiskGate(RiskGateConfig(veto_on_toxicity=False))
        self._feed_toxic_flow(gate)
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.APPROVED

    def test_safety_actions_pass_during_toxic_book(self):
        gate = CircuitBreakerRiskGate()
        self._feed_toxic_flow(gate)
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.SCALE_OUT,
                    size_fraction=0.5,
                    order=1,
                    rationale="reduce risk",
                ),
            ),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.APPROVED

    def test_untracked_symbol_never_triggers_veto(self):
        gate = CircuitBreakerRiskGate()
        self._feed_toxic_flow(gate, symbol="ethusdt")
        decision = gate.evaluate(make_proposal())  # btcusdt is untracked
        assert decision.verdict is RiskVerdict.APPROVED

    def test_toxicity_state_is_serialisable(self):
        gate = CircuitBreakerRiskGate()
        self._feed_toxic_flow(gate)
        data = gate.toxicity("btcusdt").as_dict()
        assert set(data) == {
            "vpin",
            "toxicity_quartile",
            "toxic",
            "buckets",
            "current_bucket_volume",
        }
        assert data["toxic"] is True


class TestReconciliationVeto:
    """Reconciliation veto (P0-012): a position mismatch blocks new risk
    gate-wide until reconciliation passes again."""

    def test_inconsistent_symbol_blocks_risk_increasing_action(self) -> None:
        gate = CircuitBreakerRiskGate()
        gate.set_reconciliation_state("btcusdt", consistent=False)
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.REJECTED
        assert "Reconciliation veto" in decision.reason
        assert decision.approved_size_fraction == 0.0

    def test_consistent_symbol_does_not_block(self) -> None:
        gate = CircuitBreakerRiskGate()
        gate.set_reconciliation_state("btcusdt", consistent=True)
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.APPROVED

    def test_mismatch_on_other_symbol_does_not_block_this_symbol(self) -> None:
        # Per-symbol halt: a mismatch on ethusdt does not block btcusdt.
        gate = CircuitBreakerRiskGate()
        gate.set_reconciliation_state("ethusdt", consistent=False)
        proposal = make_proposal(symbol="btcusdt")
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.APPROVED
        assert "ethusdt" not in decision.reason

    def test_mismatch_clears_when_reconciled_consistent(self) -> None:
        gate = CircuitBreakerRiskGate()
        gate.set_reconciliation_state("btcusdt", consistent=False)
        gate.set_reconciliation_state("btcusdt", consistent=True)
        assert gate.reconciliation_mismatches() == frozenset()
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.APPROVED

    def test_mismatches_are_tracked_by_symbol(self) -> None:
        gate = CircuitBreakerRiskGate()
        gate.set_reconciliation_state("btcusdt", consistent=False)
        gate.set_reconciliation_state("ethusdt", consistent=True)
        gate.set_reconciliation_state("solusdt", consistent=False)
        assert gate.reconciliation_mismatches() == frozenset({"btcusdt", "solusdt"})

    def test_safety_actions_pass_during_mismatch(self) -> None:
        gate = CircuitBreakerRiskGate()
        gate.set_reconciliation_state("btcusdt", consistent=False)
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.EXIT,
                    size_fraction=0.5,
                    order=1,
                    rationale="exit",
                ),
            ),
        )
        decision = gate.evaluate(proposal)
        assert decision.verdict is RiskVerdict.APPROVED

    def test_veto_can_be_disabled(self) -> None:
        gate = CircuitBreakerRiskGate(RiskGateConfig(block_on_reconciliation_mismatch=False))
        gate.set_reconciliation_state("btcusdt", consistent=False)
        decision = gate.evaluate(make_proposal())
        assert decision.verdict is RiskVerdict.APPROVED


class TestImpactVeto:
    """Square-root impact veto (integration #26)."""

    @staticmethod
    def _feed_calibration(gate: CircuitBreakerRiskGate) -> None:
        # Calibrate with a known eta by generating fills that exactly follow
        # the square-root model with a large coefficient.
        for i in range(40):
            qty = 100.0 * (i + 1)
            gate.record_impact_fill(
                "btcusdt",
                quantity=qty,
                realized_slippage_bps=5.0 + 0.8 * 50.0 * math.sqrt(qty / 10_000.0),
            )

    @staticmethod
    def _tight_reward_proposal(**overrides: Any) -> DecisionProposal:
        # 1% take-profit = 100 bps reward -> 25% ratio allows 25 bps impact.
        params = dict(
            pre_trade_plan=PreTradePlan(
                stop_loss=StopLevel(distance_pct=0.005),
                take_profit=StopLevel(distance_pct=0.01),
                risk_per_trade_pct=0.02,
                risk_reward_ratio=2.0,
            )
        )
        params.update(overrides)
        return make_proposal(**params)

    @staticmethod
    def _tight_reward_proposal_with_size(size_fraction: float) -> DecisionProposal:
        return make_proposal(
            pre_trade_plan=PreTradePlan(
                stop_loss=StopLevel(distance_pct=0.005),
                take_profit=StopLevel(distance_pct=0.01),
                risk_per_trade_pct=0.02,
                risk_reward_ratio=2.0,
            ),
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.ENTER_LONG,
                    size_fraction=size_fraction,
                    order=1,
                    rationale="go",
                ),
            ),
        )

    def test_veto_requires_market_stats_and_fills(self):
        gate = CircuitBreakerRiskGate()
        decision = gate.evaluate(make_proposal(), mark_price=60_000.0)
        # No calibration/market stats -> normal path, no impact rejection.
        assert "Impact veto" not in decision.reason

    def test_market_stats_validation(self):
        gate = CircuitBreakerRiskGate()
        with pytest.raises(ValueError):
            gate.set_market_stats(
                "btcusdt", avg_daily_volume=0.0, volatility_bps=50.0, half_spread_bps=1.0
            )
        with pytest.raises(ValueError):
            gate.set_market_stats(
                "btcusdt", avg_daily_volume=10_000.0, volatility_bps=-1.0, half_spread_bps=1.0
            )

    def test_calibration_requires_market_stats(self):
        gate = CircuitBreakerRiskGate()
        with pytest.raises(ValueError):
            gate.record_impact_fill("btcusdt", quantity=100.0, realized_slippage_bps=2.0)

    def test_high_impact_rejects_risk_increase(self):
        gate = CircuitBreakerRiskGate()
        gate.set_market_stats(
            "btcusdt", avg_daily_volume=10_000.0, volatility_bps=50.0, half_spread_bps=1.0
        )
        self._feed_calibration(gate)  # eta ~ 0.8; impact = 1 + 0.8*50*sqrt(part)
        # Requested 10% notional at mark 1.0 -> 10k units = 100% ADV ->
        # impact = 1 + 0.8*50*1 = 41 bps > 25 bps allowed. Veto trips.
        decision = gate.evaluate(self._tight_reward_proposal(), mark_price=1.0)
        assert decision.verdict is RiskVerdict.REJECTED
        assert "Impact veto" in decision.reason

    def test_small_size_passes_impact_veto(self):
        gate = CircuitBreakerRiskGate()
        gate.set_market_stats(
            "btcusdt", avg_daily_volume=10_000.0, volatility_bps=50.0, half_spread_bps=1.0
        )
        self._feed_calibration(gate)
        # 1% notional at mark 1.0 -> 1k units = 10% ADV -> sqrt(0.1) ->
        # impact = 1 + 40*sqrt(0.1) ~ 13.6 bps < 25 bps allowed. Passes.
        proposal = self._tight_reward_proposal_with_size(0.01)
        decision = gate.evaluate(proposal, mark_price=1.0)
        assert "Impact veto" not in decision.reason

    def test_high_impact_skips_without_mark_price(self):
        gate = CircuitBreakerRiskGate()
        gate.set_market_stats(
            "btcusdt", avg_daily_volume=10_000.0, volatility_bps=50.0, half_spread_bps=1.0
        )
        self._feed_calibration(gate)
        decision = gate.evaluate(self._tight_reward_proposal())  # no mark_price
        assert "Impact veto" not in decision.reason

    def test_impact_veto_can_be_disabled(self):
        gate = CircuitBreakerRiskGate(RiskGateConfig(veto_on_excess_impact=False))
        gate.set_market_stats(
            "btcusdt", avg_daily_volume=10_000.0, volatility_bps=50.0, half_spread_bps=1.0
        )
        self._feed_calibration(gate)
        decision = gate.evaluate(self._tight_reward_proposal(), mark_price=1.0)
        assert decision.verdict in (RiskVerdict.APPROVED, RiskVerdict.REDUCED)

    def test_safety_action_passes_during_high_impact(self):
        gate = CircuitBreakerRiskGate()
        gate.set_market_stats(
            "btcusdt", avg_daily_volume=10_000.0, volatility_bps=50.0, half_spread_bps=1.0
        )
        self._feed_calibration(gate)
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.EXIT,
                    size_fraction=0.5,
                    order=1,
                    rationale="exit",
                ),
            ),
        )
        decision = gate.evaluate(proposal, mark_price=1.0)
        assert decision.verdict is RiskVerdict.APPROVED
