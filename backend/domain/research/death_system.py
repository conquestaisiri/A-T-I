# backend/domain/research/death_system.py
"""Death-system contracts (task T3-26-1): the degrade -> demote -> retire ladder.

This is the *death system*: the explicit policy for how a strategy that
stops working leaves the population. It is fed by two independent evidence
sources — the edge monitor (T2-15, ADWIN drift on rolling returns) and the
campaign verdicts (T3-24 paper / T3-25 canary, stay-limit breaches) — and it
answers one question: what happens to the passport now?

Ladder
------
- ``STAY`` — no trigger fired; nothing changes.
- ``DEGRADE`` — one step down the promotion chain (edge decay fires).
- ``DEMOTE`` — two steps down the promotion chain (a campaign was retired
  by its stay limits: the P4-001 automatic-rollback discipline applied).
- ``RETIRE`` — terminal: the passport is dead (retired status) because the
  strategy is at the bottom of the chain with no lower step to fall back to.

Risk precedence (explicit, per the Engineering Constitution): when sources
disagree, **the harshest applicable action wins**. Two independent breach
signals never soften a decision; retreating too far is safer than staying
too long. The ladder order is the severity order: RETIRE > DEMOTE > DEGRADE
> STAY.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from backend.domain.research.passport import PassportStatus
from backend.domain.research.promotion import ModelEnvironment


class DemotionAction(enum.StrEnum):
    """What the death system decides for one passport."""

    STAY = "stay"
    DEGRADE = "degrade"
    DEMOTE = "demote"
    RETIRE = "retire"


# Severity order: earlier is milder, later is harsher. harshest() uses it so
# the risk-precedence rule is a single, auditable definition.
_ACTION_SEVERITY: tuple[DemotionAction, ...] = (
    DemotionAction.STAY,
    DemotionAction.DEGRADE,
    DemotionAction.DEMOTE,
    DemotionAction.RETIRE,
)


def harshest(*actions: DemotionAction) -> DemotionAction:
    """The harshest action among the inputs (risk precedence).

    Explicit disagreement rule: when the edge monitor and the campaign
    verdicts want different actions, the harshest wins — retreating too far
    is safer than staying too long, and two independent breach signals never
    soften a decision.
    """
    if not actions:
        return DemotionAction.STAY
    try:
        return max(actions, key=_ACTION_SEVERITY.index)
    except ValueError as exc:
        raise ValueError(f"unknown demotion action in {actions!r}") from exc


@dataclass(frozen=True, slots=True)
class DeathDecision:
    """One death-system decision for one passport.

    ``action`` is the ladder verdict; ``to_environment`` names the demotion
    target when the action is DEGRADE or DEMOTE (None for STAY and RETIRE);
    ``reasons`` preserves every trigger that fired, verbatim, so the
    decision is reproducible from the audit trail alone.
    """

    passport_id: str
    action: DemotionAction
    to_environment: ModelEnvironment | None = None
    reasons: tuple[str, ...] = ()
    from_status: PassportStatus | None = None

    @property
    def demotes(self) -> bool:
        return self.action in (DemotionAction.DEGRADE, DemotionAction.DEMOTE)

    def as_dict(self) -> dict[str, Any]:
        return {
            "passport_id": self.passport_id,
            "action": self.action.value,
            "to_environment": self.to_environment.value if self.to_environment else None,
            "reasons": list(self.reasons),
            "from_status": self.from_status.value if self.from_status else None,
        }
