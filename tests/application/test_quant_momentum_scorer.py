"""Unit tests for the deterministic quant momentum scorer (P5-007 cell)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.decision.quant_momentum_scorer import (
    QuantMomentumScorer,
    QuantScorerConfig,
)
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import ProposedActionType, RiskContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_context(
    *,
    roc: float | None,
    volatility_std_pct: float | None = 0.01,
    include_volatility: bool = True,
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

    features: list[tuple[str, ContextFeature]] = [
        ("momentum", feature("momentum", {"rate_of_change_pct": roc, "sample_count": 3})),
        ("trend", feature("trend", {"direction": "up", "change_pct": roc, "sample_count": 5})),
    ]
    if include_volatility:
        features.append(
            (
                "volatility",
                feature(
                    "volatility",
                    {"std_dev_pct": volatility_std_pct, "mean_return_pct": 0.0, "return_count": 4},
                ),
            )
        )
    return MarketContext(snapshot=snapshot, features=tuple(features), created_at=ts())


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


class TestQuantMomentumScorer:
    def test_positive_momentum_proposes_long(self):
        proposal = QuantMomentumScorer().reason(make_context(roc=0.2), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG
        assert pytest.approx(proposal.primary_action.size_fraction) == 0.10 * 1.2

    def test_negative_momentum_proposes_short(self):
        proposal = QuantMomentumScorer().reason(make_context(roc=-0.2), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_SHORT
        assert pytest.approx(proposal.primary_action.size_fraction) == 0.10 * 1.2

    def test_stands_aside_when_momentum_unavailable(self):
        proposal = QuantMomentumScorer().reason(make_context(roc=None), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE

    def test_stands_aside_when_momentum_zero(self):
        proposal = QuantMomentumScorer().reason(make_context(roc=0.0), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE

    def test_stands_aside_below_minimum_roc(self):
        scorer = QuantMomentumScorer(QuantScorerConfig(min_roc_pct=0.2))
        proposal = scorer.reason(make_context(roc=0.1), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
        assert "below the minimum" in proposal.primary_action.rationale

    def test_size_scaling_is_capped(self):
        proposal = QuantMomentumScorer().reason(make_context(roc=10.0), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.size_fraction == 0.25

    def test_no_volatility_guard(self):
        proposal = QuantMomentumScorer().reason(
            make_context(roc=0.5, volatility_std_pct=0.5), risk_context()
        )
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG

    def test_entry_carries_protective_bracket(self):
        proposal = QuantMomentumScorer().reason(make_context(roc=0.2), risk_context())
        assert proposal.pre_trade_plan is not None
        assert proposal.pre_trade_plan.has_bracket
        assert proposal.post_trade_plan is not None

    def test_bracket_falls_back_without_volatility_feature(self):
        proposal = QuantMomentumScorer().reason(
            make_context(roc=0.2, include_volatility=False), risk_context()
        )
        assert proposal.pre_trade_plan is not None
        assert proposal.pre_trade_plan.has_bracket

    def test_stand_aside_has_no_plan(self):
        proposal = QuantMomentumScorer().reason(make_context(roc=None), risk_context())
        assert proposal.pre_trade_plan is None
        assert proposal.post_trade_plan is None

    def test_deterministic(self):
        scorer = QuantMomentumScorer()
        first = scorer.reason(make_context(roc=0.2), risk_context())
        second = scorer.reason(make_context(roc=0.2), risk_context())
        assert first.proposal_id == second.proposal_id
        assert first.actions == second.actions
        assert first.pre_trade_plan == second.pre_trade_plan


class TestQuantScorerConfig:
    def test_zero_base_size_rejected(self):
        with pytest.raises(ValueError):
            QuantScorerConfig(base_size_fraction=0.0)

    def test_max_below_base_rejected(self):
        with pytest.raises(ValueError):
            QuantScorerConfig(base_size_fraction=0.2, max_size_fraction=0.1)

    def test_negative_min_roc_rejected(self):
        with pytest.raises(ValueError):
            QuantScorerConfig(min_roc_pct=-0.1)
