# backend/domain/research/ensemble.py
"""Ensemble/competition allocation contracts (task T2-13-1).

The strategy population registry (T2-12) lists every evaluated candidate;
this module is the contract for *wiring the competition into allocation*:
only candidates that passed the evidence gates (verdict PROMOTE_TO_PAPER
and not RETIRED) may compete for the risk budget, and only with numbers the
evidence actually supports.

Honesty invariants
------------------
- **Evidence gates first.** A candidate that was REJECTed, is still OBSERVE
  (insufficient evidence) or RESEARCH (not yet evaluated) cannot compete.
  The competition filter is the same conservative verdict the evidence
  engine produces — nothing here re-decides who is credible.
- **No fabricated risk numbers.** Volatility is never guessed from the
  pooled evidence: the operator supplies an explicit annualized volatility
  estimate per competing candidate, and a candidate without one is excluded
  with a recorded reason.
- **Expected return is the pooled number.** ``mean_excess_return_pct`` from
  the passport's own pooled evidence is the only expected-return input — the
  same frame the verdict gates grade.
- **Regime fit is the regime evidence.** The T2-11-1 regime robustness score
  (fraction of qualifying regimes with positive mean excess) feeds the
  allocator's ``regime_fit`` when present; 1.0 (neutral) when absent.
- **The risk gate cannot be bypassed.** Allocation is refused outright when
  the risk gate does not allow exposure; the underlying allocator enforces
  this structurally.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.domain.research.allocation import AllocationResult


@dataclass(frozen=True, slots=True)
class EnsembleAllocationResult:
    """The outcome of wiring the population into the allocator.

    Attributes
    ----------
    allocation: AllocationResult | None
        The allocator's result when at least one candidate competed; None
        when no candidate was eligible (nothing to allocate).
    competitors: tuple[str, ...]
        Passport ids that competed for the budget, in allocator order.
    excluded: tuple[tuple[str, str], ...]
        ``(passport_id, reason)`` for every candidate the gates excluded.
    reason: str
        One-line summary: ``"allocated"`` when the allocator ran, otherwise
        why no allocation was produced.
    """

    allocation: AllocationResult | None
    competitors: tuple[str, ...]
    excluded: tuple[tuple[str, str], ...]
    reason: str

    def as_dict(self) -> dict[str, Any]:
        """Serialise the ensemble result to a plain dictionary."""
        return {
            "allocation": self.allocation.as_dict() if self.allocation is not None else None,
            "competitors": list(self.competitors),
            "excluded": [{"passport_id": pid, "reason": reason} for pid, reason in self.excluded],
            "reason": self.reason,
        }


__all__ = ["EnsembleAllocationResult"]
