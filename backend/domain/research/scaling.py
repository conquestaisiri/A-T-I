# backend/domain/research/scaling.py
"""Gradual post-canary capital-scaling contracts (build-order #40).

A production-enabled candidate is not deployed at full size: it *ramps*.
Capital exposure climbs in bounded, operator-bounded steps over time, and any
stay-limit breach during the ramp snaps the exposure back down (rollback).
This is the discipline that turns \"earned\" promotion into a responsible live
deployment.

Principles
----------
- **Scale is bounded.** The ramp moves a fraction of the operator-bounded
  maximum allocation toward ``max_fraction``, never beyond it. The operator
  owns the ceiling; the harness only manages the path to it.
- **Scale is earned, not granted.** Exposure increases only after a clean
  holding period at the current tier. A breach freezes or cuts exposure.
- **Rollback is automatic.** The P4-001 stay discipline applies at every
  tier: breaching drawdown/underperformance/operational health cuts the
  exposure to the floor and grounds the ramp.
- **Tiers are observable.** Every step is recorded with its exposure, action
  and reason, so the operator can see exactly why the ramp is where it is.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class ScalingAction(enum.StrEnum):
    """What one scaling period decided."""

    ADVANCE = "advance"  # exposure increased one tier after a clean stay
    HOLD = "hold"  # no breach, but no tier change this period
    CUT = "cut"  # stay-limit breach: exposure pulled back to the floor
    CAPPED = "capped"  # already at max_fraction; nothing left to grant


class ScalingBoundary(enum.StrEnum):
    """Where the ramp ended, for the operator dashboard."""

    RAMPING = "ramping"  # exited early or still climbing
    CAPPED = "capped"  # reached the operator-bounded ceiling cleanly
    CUT = "cut"  # a stay-limit breach ended the ramp


@dataclass(frozen=True, slots=True)
class ScaleTier:
    """One step in the capital ramp."""

    tier: int
    capital_fraction: float  # 0.0..max_fraction
    action: ScalingAction
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "capital_fraction": self.capital_fraction,
            "action": self.action.value,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class ScalingProgramResult:
    """Final state of one gradual-scaling program.

    ``action`` is the last period's verdict; ``boundary`` where the ramp
    ended; ``tiers`` records every step; ``current_fraction`` is where
    exposure stands now.
    """

    candidate_id: str
    boundary: ScalingBoundary
    action: ScalingAction
    current_fraction: float
    tiers: tuple[ScaleTier, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "boundary": self.boundary.value,
            "action": self.action.value,
            "current_fraction": self.current_fraction,
            "tiers": [t.as_dict() for t in self.tiers],
            "reason": self.reason,
        }
