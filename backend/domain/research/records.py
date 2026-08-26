# backend/domain/research/records.py
"""Durable autonomy-records contracts (workstream WS1, outcome corpus).

Every research/autonomy harness today produces an immutable in-memory result
(``AutonomyProgramResult``, ``PaperCampaignResult``, ``CanaryProgramResult``,
``ScalingProgramResult``, ``GateDecision``, ``RollbackDecision``) and then the
record vanishes. This module is the *persistence view* of that output: the
frozen, JSON-serializable records the outcome corpus stores, so the operator
can replay exactly how a candidate reached (or failed to reach) each rung.

Principles
----------
- **Records are immutable facts.** Once written they never change; a
  repository must reject a write over an existing key. The corpus is an
  append-only ledger of what the system actually did.
- **Records are self-describing.** Each carries its candidate, its campaign or
  program id, the environment/stage it concerns, and an ISO-8601 UTC
  timestamp, so point-in-time queries are structural, not guessed.
- **Records are plain data.** They import only domain symbols and hold
  primitives/tuples; adapters (application layer) translate rich harness
  results into these records. Nothing here touches SQL, storage, or time
  sources.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from backend.domain.research.paper_campaign import PaperCampaignAction
from backend.domain.research.promotion import ModelEnvironment


class CampaignStatus(enum.StrEnum):
    """State-machine status of one paper campaign (WS2 lifecycle).

    Distinct from the final verdict: ``status`` is *where the run is right
    now*, while the campaign's ``action`` (a :class:`PaperCampaignAction`) is
    the *terminal verdict* once the run finishes.
    """

    PENDING = "pending"  # created, not yet started
    RUNNING = "running"  # executing day-by-day
    COMPLETED = "completed"  # reached a terminal verdict (advanced or hold)
    RETIRED = "retired"  # stay-limit breach ended it early
    CANCELLED = "cancelled"  # operator cancelled an unfinished run


class PromotionAction(enum.StrEnum):
    """Kind of promotion-engine gate event a record captures."""

    EVALUATE = "evaluate"  # a gate was evaluated (granted or denied)
    PROMOTE = "promote"  # a promotion was applied or refused


@dataclass(frozen=True, slots=True)
class DayOutcomeRecord:
    """One paper campaign day, as recorded for the outcome corpus."""

    candidate_id: str
    campaign_id: str
    day: int
    return_pct: float = 0.0
    expected_return_pct: float = 0.0
    failed_orders: int = 0
    total_orders: int = 0
    recorded_at: str = ""  # ISO-8601 UTC

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "campaign_id": self.campaign_id,
            "day": self.day,
            "return_pct": self.return_pct,
            "expected_return_pct": self.expected_return_pct,
            "failed_orders": self.failed_orders,
            "total_orders": self.total_orders,
            "recorded_at": self.recorded_at,
        }


@dataclass(frozen=True, slots=True)
class CampaignRunRecord:
    """Lifecycle record of one paper campaign.

    ``action`` stays ``None`` until a terminal verdict is reached; ``status``
    moves PENDING -> RUNNING -> terminal. Summary metrics (sharpe, drawdown)
    are filled when the campaign completes or retires.
    """

    candidate_id: str
    campaign_id: str
    status: CampaignStatus = CampaignStatus.PENDING
    action: PaperCampaignAction | None = None
    target_days: int = 0
    days_run: int = 0
    sharpe: float | None = None
    drawdown_pct: float | None = None
    reason: str = ""
    started_at: str = ""  # ISO-8601 UTC
    completed_at: str = ""  # ISO-8601 UTC

    @property
    def terminal(self) -> bool:
        return self.status in (
            CampaignStatus.COMPLETED,
            CampaignStatus.RETIRED,
            CampaignStatus.CANCELLED,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "campaign_id": self.campaign_id,
            "status": self.status.value,
            "action": self.action.value if self.action else None,
            "target_days": self.target_days,
            "days_run": self.days_run,
            "sharpe": self.sharpe,
            "drawdown_pct": self.drawdown_pct,
            "reason": self.reason,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class StageSnapshot:
    """One ladder stage captured at program-run time."""

    stage: str
    verdict: str
    reason: str = ""
    evidence: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "verdict": self.verdict,
            "reason": self.reason,
            "evidence": self.evidence,
        }


@dataclass(frozen=True, slots=True)
class ProgramRunRecord:
    """Durable copy of one composed autonomy-program run.

    Stores the full ladder snapshot so the operator can replay how the
    candidate earned (or failed) each rung.
    """

    program_id: str
    candidate_id: str
    final_environment: str
    earned: tuple[str, ...] = ()
    stages: tuple[StageSnapshot, ...] = ()
    notes: tuple[str, ...] = ()
    started_at: str = ""  # ISO-8601 UTC
    completed_at: str = ""  # ISO-8601 UTC
    _metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "candidate_id": self.candidate_id,
            "final_environment": self.final_environment,
            "earned": list(self.earned),
            "stages": [s.as_dict() for s in self.stages],
            "notes": list(self.notes),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass(frozen=True, slots=True)
class PromotionDecisionRecord:
    """One promotion-engine gate event, for the operator audit trail.

    ``action`` discriminates the event kind: an ``EVALUATE`` records the
    gate's ``required``/``satisfied``/``reasons`` and whether it ``allowed``
    promotion; a ``PROMOTE`` records that a promotion to ``environment`` was
    granted or refused. Automatic demotions live in :class:`RollbackRecord`.
    """

    candidate_id: str
    action: PromotionAction
    environment: ModelEnvironment
    allowed: bool
    required: tuple[str, ...] = ()
    satisfied: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    occurred_at: str = ""  # ISO-8601 UTC

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "action": self.action.value,
            "environment": self.environment.value,
            "allowed": self.allowed,
            "required": list(self.required),
            "satisfied": list(self.satisfied),
            "reasons": list(self.reasons),
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True, slots=True)
class RollbackRecord:
    """One automatic demotion, for the operator audit trail."""

    candidate_id: str
    from_environment: ModelEnvironment
    to_environment: ModelEnvironment | None
    reasons: tuple[str, ...] = ()
    occurred_at: str = ""  # ISO-8601 UTC

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "from_environment": self.from_environment.value,
            "to_environment": self.to_environment.value if self.to_environment else None,
            "reasons": list(self.reasons),
            "occurred_at": self.occurred_at,
        }
