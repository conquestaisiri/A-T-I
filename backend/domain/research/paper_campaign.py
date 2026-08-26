# backend/domain/research/paper_campaign.py
"""Long autonomous paper-campaign contracts (task P4-004, build-order #37).

This is the system's *autonomous apprenticeship*: a candidate earns its way
through a long, unattended paper campaign before any live touch is
considered. Unlike the canary (P4-003) — which is live-touch and therefore
requires explicit operator ``authorized=True`` — a paper campaign is a pure
sandbox: it never moves money, so it runs by default under the standing
autonomy mandate.

Principles
----------
- **Paper is the autonomous sandbox.** No authorization gate here. The
  candidate runs an injected day-by-day decision loop for the full window and
  is measured, not commanded.
- **Evidence is earned, not claimed.** The runner accumulates real
  ``paper_days_deployed`` and ``paper_sharpe`` from each day's outcome and
  only then asks the P4-001 promotion gate whether the candidate may proceed
  to canary. Unknown is never promoted.
- **Stay limits are automatic.** Any day that breaches drawdown,
  underperformance, or operational health retires the campaign early with the
  specific reasons preserved — the exact P4-001 rollback discipline applied
  to the sandbox.
- **Canary eligibility is not a live order.** ``eligible_for_canary=True``
  only means the gate was granted. Actually starting the canary still
  requires the operator's explicit authorization in
  :class:`~backend.application.research.canary_harness.CanaryHarness`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from backend.domain.research.promotion import CandidateEvidence


class PaperDayAction(enum.StrEnum):
    """What one campaign day decided."""

    CONTINUE = "continue"
    RETIRED = "retired"


class PaperCampaignAction(enum.StrEnum):
    """Final verdict of one autonomous paper campaign.

    - ``COMPLETED_ADVANCED`` — the full window ran breach-free AND the P4-001
      canary gate was granted; the candidate is eligible for a canary.
    - ``COMPLETED_HOLD`` — the full window ran breach-free but evidence is
      still insufficient for canary; the candidate stays in paper.
    - ``RETIRED`` — a stay-limit breach ended the campaign early.
    """

    COMPLETED_ADVANCED = "completed_advanced"
    COMPLETED_HOLD = "completed_hold"
    RETIRED = "retired"


@dataclass(frozen=True, slots=True)
class PaperDayOutcome:
    """Result of one day of decision-loop execution in paper.

    The caller's ``day_fn`` returns these; the runner turns them into
    :class:`PaperDay` records and the campaign verdict.
    """

    day: int
    return_pct: float = 0.0
    expected_return_pct: float = 0.0
    failed_orders: int = 0
    total_orders: int = 0


@dataclass(frozen=True, slots=True)
class PaperDay:
    """One recorded campaign day."""

    day: int
    action: PaperDayAction
    return_pct: float = 0.0
    drawdown_pct: float = 0.0
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "action": self.action.value,
            "return_pct": self.return_pct,
            "drawdown_pct": self.drawdown_pct,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PaperCampaignResult:
    """Final judge of one autonomous paper campaign.

    ``action`` is the verdict:
    - ``COMPLETED_ADVANCED`` — earned canary eligibility.
    - ``COMPLETED_HOLD`` — needs more paper evidence; remains in paper.
    - ``RETIRED`` — automatic rollback on a stay-limit breach.

    ``evidence`` is the accumulated evidence handed to the promotion gate.
    """

    candidate_id: str
    days_run: int
    action: PaperCampaignAction
    sharpe: float
    drawdown_pct: float
    evidence: CandidateEvidence
    periods: tuple[PaperDay, ...] = ()
    reason: str = ""

    @property
    def eligible_for_canary(self) -> bool:
        return self.action is PaperCampaignAction.COMPLETED_ADVANCED

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "days_run": self.days_run,
            "action": self.action.value,
            "sharpe": self.sharpe,
            "drawdown_pct": self.drawdown_pct,
            "eligible_for_canary": self.eligible_for_canary,
            "evidence": self.evidence.as_dict(),
            "periods": [p.as_dict() for p in self.periods],
            "reason": self.reason,
        }
