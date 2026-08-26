"""Unit tests for the deterministic RuleBasedSolver."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.decision.rule_based_solver import RuleBasedSolver, SolverConfig
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import (
    ProposedActionType,
    RiskContext,
)
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_context(
    *,
    trend_direction: str,
    momentum_pct: float,
    volatility_std: float = 0.001,
) -> MarketContext:
    event = ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts(),
        payload={"symbol": "btcusdt", "trade_id": 1, "price": 100.0, "quantity": 1.0},
    )
    snapshot = ContextSnapshot.from_events((event,))

    def feature(name: str, value: object) -> ContextFeature:
        return ContextFeature(
            name=name, value=value, computation_timestamp=ts(), execution_time=0.0
        )

    features = (
        (
            "trend",
            feature(
                "trend",
                {
                    "direction": trend_direction,
                    "change_pct": momentum_pct,
                    "first_price": 99.0,
                    "last_price": 100.0,
                    "sample_count": 5,
                },
            ),
        ),
        (
            "momentum",
            feature("momentum", {"rate_of_change_pct": momentum_pct, "sample_count": 3}),
        ),
        (
            "volatility",
            feature(
                "volatility", {"std_dev": volatility_std, "mean_return": 0.0, "return_count": 4}
            ),
        ),
        (
            "volume",
            feature("volume", {"total_volume": 50.0, "average_volume": 10.0, "trade_count": 5}),
        ),
        (
            "liquidity",
            feature("liquidity", {"trade_count": 5}),
        ),
    )
    return MarketContext(snapshot=snapshot, features=features, created_at=ts())


def risk_context() -> RiskContext:
    return RiskContext(
        account_equity=100_000.0,
        open_exposure_pct=0.0,
        daily_loss_pct=0.0,
        monthly_loss_pct=0.0,
        total_loss_pct=0.0,
        drawdown_pct=0.0,
        position_count=0,
    )


class TestRuleBasedSolver:
    def test_up_trend_proposes_long(self):
        solver = RuleBasedSolver()
        proposal = solver.reason(
            make_context(trend_direction="up", momentum_pct=0.2),
            risk_context(),
        )
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG
        assert proposal.symbol == "btcusdt"

    def test_down_trend_proposes_short(self):
        solver = RuleBasedSolver()
        proposal = solver.reason(
            make_context(trend_direction="down", momentum_pct=-0.2),
            risk_context(),
        )
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_SHORT

    def test_conflicting_trend_and_momentum_stands_aside(self):
        solver = RuleBasedSolver()
        proposal = solver.reason(
            make_context(trend_direction="up", momentum_pct=-0.2),
            risk_context(),
        )
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE

    def test_flat_trend_stands_aside(self):
        solver = RuleBasedSolver()
        proposal = solver.reason(
            make_context(trend_direction="flat", momentum_pct=0.0),
            risk_context(),
        )
        assert proposal.primary_action is None or (
            proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
        )

    def test_high_volatility_blocks_directional_entry(self):
        solver = RuleBasedSolver()
        proposal = solver.reason(
            make_context(
                trend_direction="up",
                momentum_pct=0.2,
                volatility_std=0.08,
            ),
            risk_context(),
        )
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE

    def test_deterministic_same_input_same_proposal(self):
        solver = RuleBasedSolver()
        context = make_context(trend_direction="up", momentum_pct=0.2)
        first = solver.reason(context, risk_context())
        second = solver.reason(context, risk_context())
        assert first == second

    def test_confidence_within_bounds(self):
        solver = RuleBasedSolver()
        proposal = solver.reason(
            make_context(trend_direction="up", momentum_pct=0.2),
            risk_context(),
        )
        assert 0.0 <= proposal.confidence <= 1.0

    def test_base_size_fraction_applied(self):
        solver = RuleBasedSolver(SolverConfig(base_size_fraction=0.25))
        proposal = solver.reason(
            make_context(trend_direction="up", momentum_pct=0.2),
            risk_context(),
        )
        assert proposal.primary_action is not None
        assert proposal.primary_action.size_fraction == pytest.approx(0.25)

    def test_proposal_carries_risk_context(self):
        solver = RuleBasedSolver()
        ctx = risk_context()
        proposal = solver.reason(make_context(trend_direction="up", momentum_pct=0.2), ctx)
        assert proposal.risk_context == ctx

    def test_proposal_id_is_deterministic(self):
        solver = RuleBasedSolver()
        context = make_context(trend_direction="up", momentum_pct=0.2)
        assert context.snapshot.symbol.lower() in solver.reason(context, risk_context()).proposal_id
