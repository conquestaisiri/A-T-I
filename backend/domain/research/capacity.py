# backend/domain/research/capacity.py
"""Strategy capacity contracts (task T2-18-1).

Capacity answers "how large may this strategy trade before its own market
impact eats the edge?" — the size at which the expected impact of an order
consumes a fixed share of the strategy's expected reward. The answer is
built from the *calibrated* square-root impact law (the symbol's own
``eta``, fit from ATI's fills), never from a hard-coded assumption.

Honesty invariants
------------------
- **No calibration, no capacity.** Without a usable ``eta`` the capacity
  is None: a size bound derived from a guessed impact coefficient would be
  fabricated.
- **No edge, no capacity.** With a non-positive expected edge there is
  nothing to spend on impact; capacity is zero (``executable`` False).
- **The half-spread alone can kill a trade.** When the fixed half-spread
  cost already exceeds the impact budget, no size is viable: capacity is
  zero, honestly, instead of a tiny-but-nonzero number that would still
  lose money.
- **The model's validity window is bounded.** Participation is capped at
  ``max_participation`` (default 100% of ADV — beyond that the square-root
  law is not credible). A negative calibrated ``eta`` (slippage below the
  spread baseline) never crosses the budget within the window, so the
  estimate is reported ``unbounded_within_model`` at the cap, with the
  capped impact recorded — not infinity.
- **Impact share is explicit.** The budget is ``max_impact_share`` of the
  expected edge (default 20%), a configurable, recorded assumption.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ImpactCurvePoint:
    """One point on the trade-size vs impact curve.

    Attributes
    ----------
    participation_pct: float
        Order size as a fraction of ADV (e.g. 0.05 = 5% of daily volume).
    quantity: float
        The order size in ADV units.
    impact_bps: float
        Expected impact at this size, per the calibrated square-root law.
    """

    participation_pct: float
    quantity: float
    impact_bps: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "participation_pct": round(self.participation_pct, 6),
            "quantity": round(self.quantity, 6),
            "impact_bps": round(self.impact_bps, 6),
        }


@dataclass(frozen=True, slots=True)
class CapacityEstimate:
    """Per-symbol (or per-passport) capacity bound and its audit trail.

    Attributes
    ----------
    symbol: str
        The symbol the estimate applies to.
    eta, r_squared, observations: float, float, int
        The calibration used (its audit trail).
    edge_bps: float
        The expected edge per unit the budget was drawn against.
    max_impact_share: float
        The recorded budget fraction (impact must stay within this share
        of the edge).
    capacity_quantity: float
        The largest viable order size in ADV units (0.0 when not
        executable; at the participation cap when unbounded within model).
    capacity_participation_pct: float
        Same bound as a fraction of ADV.
    capacity_impact_bps: float
        Expected impact at the capacity size.
    executable: bool
        False when no size is viable (no edge, or half-spread alone
        exceeds the budget). Capacity is then 0.0.
    unbounded_within_model: bool
        True when the impact law never crosses the budget inside the
        participation cap (e.g. negative calibrated eta); the estimate is
        then the capped size, not infinity.
    curve: tuple[ImpactCurvePoint, ...]
        The trade-size vs impact curve up to the bound (audit / display).
    """

    symbol: str
    eta: float
    r_squared: float
    observations: int
    edge_bps: float
    max_impact_share: float
    capacity_quantity: float
    capacity_participation_pct: float
    capacity_impact_bps: float
    executable: bool
    unbounded_within_model: bool
    curve: tuple[ImpactCurvePoint, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "calibration": {
                "eta": round(self.eta, 6),
                "r_squared": round(self.r_squared, 6),
                "observations": self.observations,
            },
            "edge_bps": round(self.edge_bps, 6),
            "max_impact_share": self.max_impact_share,
            "capacity_quantity": round(self.capacity_quantity, 6),
            "capacity_participation_pct": round(self.capacity_participation_pct, 6),
            "capacity_impact_bps": round(self.capacity_impact_bps, 6),
            "executable": self.executable,
            "unbounded_within_model": self.unbounded_within_model,
            "curve": [point.as_dict() for point in self.curve],
        }


__all__ = ["CapacityEstimate", "ImpactCurvePoint"]
