# backend/domain/research/canary.py
"""Live-canary campaign contracts (task P4-003).

The canary is the last barracks before production: a *bounded* campaign in
which a promotion-approved candidate runs at a known exposure while its live
performance is monitored every period. The canary begins only with explicit
operator authorization, runs for a fixed number of days, and is judged in
three ways: it can be retired early by the P4-001 stay limits (automatic
rollback), held for more evidence, or recommended to production when the
promotion gate grants it.

Principles
----------
- **Explicit authorization is a hard gate.** A canary is live-touch; without
  the operator's explicit ``authorized=True`` nothing starts. This is the
  same fail-safe posture as the P0-014 live-trading guard.
- **The harness monitors; it never executes.** Actual orders still flow
  through the operator-wired gateway. The harness only reads each period's
  :class:`DeploymentMonitor` and decides stay/rollback.
- **Evidence decides the exit.** Early exit on breach, HOLD when evidence is
  insufficient for production, PRODUCTION_READY only when the full promotion
  gate (P4-001) grants it.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class CanaryAction(enum.StrEnum):
    """What one canary period decided."""

    CONTINUE = "continue"
    RETIRED = "retired"
    HOLD = "hold"
    PRODUCTION_READY = "production_ready"


@dataclass(frozen=True, slots=True)
class CanaryPeriod:
    """Outcome of one monitored canary period."""

    day: int
    action: CanaryAction
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day, "action": self.action.value, "reason": self.reason}


@dataclass(frozen=True, slots=True)
class CanaryProgramResult:
    """Final judge of one bounded canary campaign.

    ``action`` is the campaign verdict:
    - ``PRODUCTION_READY`` — the promotion gate granted production.
    - ``HOLD`` — the campaign ended without breaching but evidence is not yet
      enough for production.
    - ``RETIRED`` — an automatic stay-limit rollback ended the campaign early.
    """

    candidate_id: str
    authorized: bool
    days_run: int
    action: CanaryAction
    periods: tuple[CanaryPeriod, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "authorized": self.authorized,
            "days_run": self.days_run,
            "action": self.action.value,
            "periods": [p.as_dict() for p in self.periods],
            "reason": self.reason,
        }


class CanaryNotAuthorized(RuntimeError):
    """Raised when a canary campaign is requested without explicit operator
    authorization. A canary is live-touch; it never starts by default."""
