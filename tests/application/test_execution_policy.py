"""Tests for ExecutionPolicy port + implementations (ADR 0029)."""

from __future__ import annotations

import pytest
from backend.application.execution.execution_policy import (
    AlwaysMarketPolicy,
    ExecutionStyle,
    MarketStateSnapshot,
    PassiveIfSpreadTightPolicy,
    build_execution_policy,
)


def _snap(
    bid: float | None = 99.99,
    ask: float | None = 100.01,
    mark: float = 100.0,
    momentum: float | None = 0.1,
) -> MarketStateSnapshot:
    return MarketStateSnapshot(
        symbol="btcusdt", mark_price=mark, best_bid=bid, best_ask=ask, momentum_pct=momentum
    )


class TestAlwaysMarket:
    def test_always_returns_market(self):
        p = AlwaysMarketPolicy()
        plan = p.plan_execution("enter_long", 0.1, _snap())
        assert plan.style is ExecutionStyle.MARKET
        assert not plan.post_only

    def test_always_market_no_book(self):
        p = AlwaysMarketPolicy()
        plan = p.plan_execution("enter_long", 0.1, _snap(bid=None, ask=None))
        assert plan.style is ExecutionStyle.MARKET


class TestPassiveIfSpreadTight:
    def test_tight_spread_gives_passive(self):
        p = PassiveIfSpreadTightPolicy(max_spread_bps=5.0)
        plan = p.plan_execution("enter_long", 0.1, _snap(bid=99.99, ask=100.01))
        # spread = 2 bps < 5 -> passive
        assert plan.style is ExecutionStyle.PASSIVE_LIMIT
        assert plan.post_only

    def test_wide_spread_falls_back_to_market(self):
        p = PassiveIfSpreadTightPolicy(max_spread_bps=1.0)
        plan = p.plan_execution("enter_long", 0.1, _snap(bid=99.90, ask=100.10))
        # spread = 20 bps > 1 -> market
        assert plan.style is ExecutionStyle.MARKET

    def test_no_book_falls_back_to_market(self):
        p = PassiveIfSpreadTightPolicy()
        plan = p.plan_execution("enter_long", 0.1, _snap(bid=None, ask=None))
        assert plan.style is ExecutionStyle.MARKET

    def test_crossed_book_falls_back_to_market(self):
        p = PassiveIfSpreadTightPolicy()
        plan = p.plan_execution("enter_long", 0.1, _snap(bid=100.02, ask=100.00))
        assert plan.style is ExecutionStyle.MARKET

    def test_momentum_against_buy_goes_market(self):
        p = PassiveIfSpreadTightPolicy()
        plan = p.plan_execution("enter_long", 0.1, _snap(momentum=-0.8))
        assert plan.style is ExecutionStyle.MARKET

    def test_momentum_against_sell_goes_market(self):
        p = PassiveIfSpreadTightPolicy()
        plan = p.plan_execution("enter_short", 0.1, _snap(momentum=0.8))
        assert plan.style is ExecutionStyle.MARKET


class TestFactory:
    def test_build_always_market(self):
        p = build_execution_policy("always_market")
        assert isinstance(p, AlwaysMarketPolicy)

    def test_build_passive(self):
        p = build_execution_policy("passive_if_spread_tight")
        assert isinstance(p, PassiveIfSpreadTightPolicy)

    def test_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown"):
            build_execution_policy("nonexistent")
