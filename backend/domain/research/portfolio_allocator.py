# backend/domain/research/portfolio_allocator.py
"""Correlation-aware portfolio allocation contracts (task T2-14-1).

This is the portfolio-level half of the correlation work (T2-13-2 built
the measured surface): given per-strategy evidence scores and the
portfolio's correlation matrix, decide *how much* of the risk budget each
strategy earns. Redundancy is not rewarded — a pair of near-identical
edges buys the portfolio only one edge, so its combined claim shrinks
while genuinely independent strategies keep theirs.

Honesty invariants
------------------
- **Correlation comes from the measured matrix or it does not exist.**
  A scored strategy absent from the matrix is a refusal, not a neutral:
  allocating it without its correlation surface would silently skip the
  dampening this layer exists to apply.
- **Dampening is symmetric within a correlated pair.** Redundancy
  penalizes both members equally; the score ratio inside the pair is
  preserved, only its combined claim is discounted.
- **The allocation is auditable.** Every weight carries its dampening
  factor and its correlation load (how much of the final portfolio is
  correlated with it), so the discount is never hidden in a black box.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class AllocatedWeight:
    """One strategy's share of the portfolio risk budget.

    Attributes
    ----------
    strategy_id: str
        The passport id.
    score: float
        The evidence score the allocation was based on.
    weight: float
        Final portfolio weight (all weights sum to 1.0).
    dampening: float
        The redundancy discount applied (1.0 = none; smaller = more
        correlated with the rest of the portfolio).
    correlation_load: float
        Sum over peers of ``rho * peer_weight``: how much of the final
        portfolio is correlated with this strategy (negative rho reduces
        it). Informational audit trail.
    """

    strategy_id: str
    score: float
    weight: float
    dampening: float
    correlation_load: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "score": round(self.score, 8),
            "weight": round(self.weight, 8),
            "dampening": round(self.dampening, 8),
            "correlation_load": round(self.correlation_load, 8),
        }


@dataclass(frozen=True, slots=True)
class PortfolioAllocation:
    """The full correlation-aware allocation.

    Attributes
    ----------
    weights: tuple[AllocatedWeight, ...]
        One entry per scored strategy, in matrix id order, positive-score
        strategies first then zero-score ones (weight 0.0, dampening 1.0).
    correlation_sensitivity: float
        The sensitivity the dampening used (explicit, never hidden).
    """

    weights: tuple[AllocatedWeight, ...]
    correlation_sensitivity: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "correlation_sensitivity": self.correlation_sensitivity,
            "weights": [weight.as_dict() for weight in self.weights],
        }


__all__ = ["AllocatedWeight", "PortfolioAllocation"]
