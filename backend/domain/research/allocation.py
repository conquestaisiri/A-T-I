# backend/domain/research/allocation.py
"""Strategy-allocation contracts (task P3-003).

The allocator divides a *risk budget* (a portfolio volatility cap) between
competing strategies. This module owns the value objects; the engine is in
``backend.application.research.strategy_allocator``.

Principles
----------
- **Strategies compete for risk budget.** A strategy's share of the budget is
  earned, not granted: higher risk (volatility) earns less budget, weaker fit
  to the current regime earns less, and the portfolio's total risk is capped by
  the operator's budget.
- **Correlation and regime fit are part of the competition.** The covariance
  between strategies dilutes or concentrates the risk each weight consumes, so
  correlated strategies are held back; regime fit scales each strategy's
  claim.
- **The allocator cannot bypass the risk gate.** Allocation is refused
  outright (all weights zero, status ``"blocked"``) when the risk gate does
  not allow new exposure — an attractive strategy cannot override a veto.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class StrategyProfile:
    """A strategy competing for the risk budget.

    Attributes
    ----------
    name: str
        Strategy identifier (unique within an allocation).
    expected_return_pct: float
        The strategy's own expected return, as a percentage.
    volatility_pct: float
        The strategy's risk (annualized volatility), as a percentage.
        Must be strictly positive.
    regime_fit: float
        How well the strategy fits the current regime, in ``[0, 1]``
        (1 = ideal fit). Scales the strategy's claim on the budget.
    """

    name: str
    expected_return_pct: float
    volatility_pct: float
    regime_fit: float = 1.0


@dataclass(frozen=True, slots=True)
class StrategyAllocation:
    """One strategy's share of the risk budget.

    Attributes
    ----------
    strategy_name: str
        Which strategy.
    weight: float
        Weight in the portfolio (non-negative; a fraction of the budget when
        the budget binds).
    reason: str
        Short human-readable reason this weight was chosen.
    """

    strategy_name: str
    weight: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"strategy_name": self.strategy_name, "weight": self.weight, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """The outcome of one risk-budget allocation.

    Attributes
    ----------
    allocations: tuple[StrategyAllocation, ...]
        Per-strategy weights, highest weight first. Empty when blocked.
    status: str
        ``"allocated"`` when the risk gate allowed exposure and weights were
        produced; ``"blocked"`` when the risk gate vetoed the allocation (all
        weights zero).
    risk_budget_pct: float
        The portfolio volatility cap this allocation was made against.
    portfolio_expected_return_pct: float
        Weighted expected return of the allocated portfolio.
    portfolio_volatility_pct: float
        Realised portfolio volatility of the allocated weights; at or below
        ``risk_budget_pct``.
    blocked_reason: str | None
        Why the allocation was blocked, when it was.
    """

    allocations: tuple[StrategyAllocation, ...]
    status: str
    risk_budget_pct: float
    portfolio_expected_return_pct: float
    portfolio_volatility_pct: float
    blocked_reason: str | None = None

    @property
    def blocked(self) -> bool:
        """Whether the risk gate refused this allocation."""
        return self.status == "blocked"

    def weight_for(self, strategy_name: str) -> float:
        """The weight assigned to ``strategy_name`` (0 when absent)."""
        for allocation in self.allocations:
            if allocation.strategy_name == strategy_name:
                return allocation.weight
        return 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "allocations": [a.as_dict() for a in self.allocations],
            "status": self.status,
            "risk_budget_pct": self.risk_budget_pct,
            "portfolio_expected_return_pct": self.portfolio_expected_return_pct,
            "portfolio_volatility_pct": self.portfolio_volatility_pct,
            "blocked_reason": self.blocked_reason,
        }
