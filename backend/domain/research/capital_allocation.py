# backend/domain/research/capital_allocation.py
"""Portfolio-level capital allocation contracts (task T3-29-1).

The portfolio-level optimizer: from the passport population and the
measured correlation matrix, decide how the risk budget is split. This
module owns the *contracts*; :mod:`backend.application.research.capital_allocator`
owns the store-backed decision logic.

Honesty invariants
------------------
- **No allocation before evidence gates.** Capital is a reward for
  surviving the gates, never a hypothesis-funding mechanism: only a
  passport whose pooled evidence passed ``verdict_for_evidence``
  (PROMOTE_TO_PAPER) and which is not retired can earn weight. Every
  exclusion carries a named reason (gates failed / insufficient
  evidence / not evaluated / dead).
- **The plan names every strategy.** A plan is not just the weights — it
  is the verdict per passport: eligible or excluded, and why. A capital
  decision that could not be audited would be a risk decision.
- **Nothing eligible -> no allocation, honestly.** A zero-strategy plan is
  ``allocation=None`` with the reason, never an empty or fabricated
  portfolio.
- **The deltas are the reallocation.** ``rebalance()`` compares the
  current weights against the target plan and reports exactly what must
  move (and the excluded ids that must exit), so the operator never has
  to reverse-engineer a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.domain.research.portfolio_allocator import PortfolioAllocation


@dataclass(frozen=True, slots=True)
class AllocationVerdict:
    """One passport's capital verdict: eligible or excluded, with the reason.

    Attributes
    ----------
    passport_id: str
        The passport id.
    eligible: bool
        True when the passport's evidence passed the gates and it may earn
        capital; False otherwise.
    reason: str
        Why (``"eligible: evidence gates passed"`` or the exclusion cause).
    score: float | None
        The evidence score used for sizing (pooled mean excess return),
        None for excluded strategies without one.
    """

    passport_id: str
    eligible: bool
    reason: str
    score: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "passport_id": self.passport_id,
            "eligible": self.eligible,
            "reason": self.reason,
            "score": round(self.score, 8) if self.score is not None else None,
        }


@dataclass(frozen=True, slots=True)
class AllocationDelta:
    """One strategy's required weight change between current and target.

    Attributes
    ----------
    passport_id: str
        The passport id.
    current_weight: float
        The weight currently allocated (0.0 when not held).
    target_weight: float
        The weight the plan targets (0.0 for excluded ids).
    delta: float
        ``target_weight - current_weight`` (positive = add exposure,
        negative = cut exposure).
    reason: str
        Why the change is required (the plan's verdict reason for that id).
    """

    passport_id: str
    current_weight: float
    target_weight: float
    delta: float
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "passport_id": self.passport_id,
            "current_weight": round(self.current_weight, 8),
            "target_weight": round(self.target_weight, 8),
            "delta": round(self.delta, 8),
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class CapitalAllocationPlan:
    """The auditable portfolio capital plan for the whole population.

    Attributes
    ----------
    allocation: PortfolioAllocation | None
        The correlation-damped target weights when at least one passport
        is eligible; None otherwise (never an empty fabricated portfolio).
    verdicts: tuple[AllocationVerdict, ...]
        The per-passport verdicts, in issue order (oldest first, ties by
        passport id): every passport is named, eligible or not.
    unavailable_reason: str
        Why ``allocation`` is None (empty when the allocation exists).
    correlation_sensitivity: float
        The dampening sensitivity the allocation used (explicit, never
        hidden).
    """

    allocation: PortfolioAllocation | None
    verdicts: tuple[AllocationVerdict, ...]
    unavailable_reason: str = ""
    correlation_sensitivity: float = 1.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "allocation": self.allocation.as_dict() if self.allocation is not None else None,
            "verdicts": [verdict.as_dict() for verdict in self.verdicts],
            "unavailable_reason": self.unavailable_reason,
            "correlation_sensitivity": self.correlation_sensitivity,
        }


__all__ = ["AllocationDelta", "AllocationVerdict", "CapitalAllocationPlan"]
