# backend/application/execution/execution_policy.py
"""Execution policy port + default policies (ADR 0029).

Decides HOW a risk-approved action is executed: market vs passive limit,
slicing, cancel conditions. The port is injected into DecisionPipelineService;
the default ``AlwaysMarket`` preserves current behavior exactly.

Policies:
- AlwaysMarket: submit as IOC market order (baseline, zero risk)
- PassiveIfSpreadTight: post-only limit at mid when spread is tight,
  fall back to market if not filled within timeout bars

Both are pure decision-makers: they receive market state and return an
ExecutionPlan; they never touch the gateway themselves.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any, Protocol


class ExecutionStyle(enum.StrEnum):
    """How the order should be executed."""

    MARKET = "market"
    PASSIVE_LIMIT = "passive_limit"


@dataclass(frozen=True, slots=True)
class MarketStateSnapshot:
    """Minimal market state the policy needs to make its decision."""

    symbol: str
    mark_price: float
    best_bid: float | None = None
    best_ask: float | None = None
    momentum_pct: float | None = None
    volatility_std_dev: float | None = None


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    """The output of an execution policy."""

    style: ExecutionStyle
    post_only: bool = False
    limit_offset_bps: float = 0.0  # offset from mid for passive limits
    timeout_bars: int = 0  # 0 = no timeout (IOC market)
    reason: str = ""


class ExecutionPolicy(Protocol):
    """Decides HOW to execute a risk-approved action."""

    def plan_execution(
        self,
        action_type: Any,
        size_fraction: float,
        market_state: MarketStateSnapshot,
    ) -> ExecutionPlan: ...


class AlwaysMarketPolicy:
    """Baseline: always execute as market order. Zero-risk, current behavior."""

    def plan_execution(
        self,
        action_type: Any,
        size_fraction: float,
        market_state: MarketStateSnapshot,
    ) -> ExecutionPlan:
        return ExecutionPlan(
            style=ExecutionStyle.MARKET,
            reason="always_market",
        )


class PassiveIfSpreadTightPolicy:
    """Post-only limit at mid when spread is tight; fallback to market otherwise.

    Saves maker fee (0.02% vs 0.04%) on filled orders. Risk: unfilled orders
    miss the move (opportunity cost). The timeout_bars parameter bounds this.
    """

    def __init__(
        self,
        *,
        max_spread_bps: float = 3.0,
        limit_offset_bps: float = 0.0,
        timeout_bars: int = 5,
    ) -> None:
        if max_spread_bps <= 0:
            raise ValueError("max_spread_bps must be positive")
        if timeout_bars < 1:
            raise ValueError("timeout_bars must be >= 1")
        self._max_spread_bps = max_spread_bps
        self._limit_offset_bps = limit_offset_bps
        self._timeout_bars = timeout_bars

    def plan_execution(
        self,
        action_type: Any,
        size_fraction: float,
        market_state: MarketStateSnapshot,
    ) -> ExecutionPlan:
        bb = market_state.best_bid
        ba = market_state.best_ask
        mark = market_state.mark_price

        if bb is None or ba is None or bb >= ba or mark <= 0:
            return ExecutionPlan(style=ExecutionStyle.MARKET, reason="no_book")

        spread_bps = (ba - bb) / mark * 10_000
        if spread_bps > self._max_spread_bps:
            return ExecutionPlan(
                style=ExecutionStyle.MARKET,
                reason=f"spread_too_wide_{spread_bps:.1f}bps",
            )

        # Signal decay check: if momentum is strongly against us, don't wait
        mom = market_state.momentum_pct
        if mom is not None:
            is_buy = "long" in str(action_type).lower() or action_type in ("buy", "enter_long")
            if is_buy and mom < -0.5:
                return ExecutionStyle.MARKET and ExecutionPlan(  # type: ignore[return-value]
                    style=ExecutionStyle.MARKET,
                    reason="momentum_against_buy",
                )
            if not is_buy and mom > 0.5:
                return ExecutionPlan(style=ExecutionStyle.MARKET, reason="momentum_against_sell")

        return ExecutionPlan(
            style=ExecutionStyle.PASSIVE_LIMIT,
            post_only=True,
            limit_offset_bps=self._limit_offset_bps,
            timeout_bars=self._timeout_bars,
            reason=f"spread_tight_{spread_bps:.1f}bps",
        )


def build_execution_policy(policy_name: str) -> ExecutionPolicy:
    """Factory: build an execution policy by name."""
    if policy_name == "always_market":
        return AlwaysMarketPolicy()
    if policy_name == "passive_if_spread_tight":
        return PassiveIfSpreadTightPolicy()
    raise ValueError(f"Unknown execution policy: {policy_name}")
