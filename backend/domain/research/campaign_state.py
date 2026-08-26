# backend/domain/research/campaign_state.py
"""Paper-campaign lifecycle state machine (workstream WS2).

A campaign is a long-running, resumable process: it is created PENDING,
started RUNNING, and then reaches exactly one terminal status (COMPLETED,
RETIRED, or CANCELLED). This module is the *single authority* on legal
transitions — the store enforces the same rules at the persistence layer, but
service code must be able to reason about the lifecycle purely, without a
database, and deterministically.

Rules
-----
- **Forward-only.** PENDING -> RUNNING -> one terminal status. There is no
  path out of a terminal status and no path back to PENDING.
- **Idempotent start.** Asking to start an already-RUNNING campaign is not an
  error (a retried supervisor tick must not crash); it is a no-op. Starting a
  PENDING campaign is the only real transition into RUNNING.
- **Cancel is allowed before start.** A created-but-never-started campaign
  (PENDING) may still be cancelled by the operator; only a RUNNING campaign
  reaches a terminal verdict.
- **Terminal is absorbing.** A terminal campaign can never be restarted,
  cancelled, retired, or completed again.
- **The machine is stateless and pure.** It holds no timestamps and no
  storage; the caller applies a clock and persists the result.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.research.records import CampaignStatus


@dataclass(frozen=True, slots=True)
class CampaignTransition:
    """A verified lifecycle step, ready to be persisted."""

    from_status: CampaignStatus
    to_status: CampaignStatus
    occurred_at: str  # ISO-8601 UTC, supplied by the caller's clock
    reason: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "from_status": self.from_status.value,
            "to_status": self.to_status.value,
            "occurred_at": self.occurred_at,
            "reason": self.reason,
        }


_TERMINAL_STATUSES = frozenset(
    {
        CampaignStatus.COMPLETED,
        CampaignStatus.RETIRED,
        CampaignStatus.CANCELLED,
    }
)

# Legal forward moves. A status is a key only if it can leave that status.
# A created-but-never-started campaign (PENDING) may still be cancelled by the
# operator; only RUNNING may reach the terminal verdicts.
_ALLOWED_TRANSITIONS: dict[CampaignStatus, frozenset[CampaignStatus]] = {
    CampaignStatus.PENDING: frozenset({CampaignStatus.RUNNING, CampaignStatus.CANCELLED}),
    CampaignStatus.RUNNING: frozenset(
        {CampaignStatus.COMPLETED, CampaignStatus.RETIRED, CampaignStatus.CANCELLED}
    ),
    CampaignStatus.COMPLETED: frozenset(),
    CampaignStatus.RETIRED: frozenset(),
    CampaignStatus.CANCELLED: frozenset(),
}


def is_terminal(status: CampaignStatus) -> bool:
    """True when ``status`` is an absorbing terminal state."""
    return status in _TERMINAL_STATUSES


def transition(
    from_status: CampaignStatus,
    to_status: CampaignStatus,
    *,
    occurred_at: str,
    reason: str = "",
) -> CampaignTransition:
    """Verify and record a single status transition.

    Raises ``ValueError`` for a backward move (RUNNING -> PENDING), an illegal
    jump (PENDING -> COMPLETED), or any move out of a terminal state.
    """
    if from_status is to_status:
        raise ValueError(f"no-op transition is not recorded: {from_status.value}")
    allowed = _ALLOWED_TRANSITIONS[from_status]
    if to_status not in allowed:
        raise ValueError(f"illegal campaign transition {from_status.value} -> {to_status.value}")
    return CampaignTransition(
        from_status=from_status,
        to_status=to_status,
        occurred_at=occurred_at,
        reason=reason,
    )


def start(
    current: CampaignStatus,
    *,
    occurred_at: str,
) -> CampaignTransition | None:
    """Try to start a campaign, idempotently.

    - PENDING -> RUNNING returns a transition to persist.
    - RUNNING returns ``None`` (already started; a retried tick is a no-op).
    - a terminal status raises, because a finished campaign cannot restart.
    """
    if current is CampaignStatus.RUNNING:
        return None
    if current in _TERMINAL_STATUSES:
        raise ValueError(f"cannot start a terminal campaign ({current.value})")
    return transition(
        current,
        CampaignStatus.RUNNING,
        occurred_at=occurred_at,
        reason="campaign started",
    )


def cancel(
    current: CampaignStatus,
    *,
    occurred_at: str,
    reason: str = "operator request",
) -> CampaignTransition:
    """Cancel a campaign that has not yet reached a terminal verdict.

    Legal from PENDING (created but never started) and RUNNING. A terminal
    campaign raises: once finished, there is nothing to cancel.
    """
    if current in _TERMINAL_STATUSES:
        raise ValueError(f"cannot cancel a terminal campaign ({current.value})")
    return transition(
        current,
        CampaignStatus.CANCELLED,
        occurred_at=occurred_at,
        reason=reason,
    )


def finish(
    current: CampaignStatus,
    terminal: CampaignStatus,
    *,
    occurred_at: str,
    reason: str,
) -> CampaignTransition:
    """Move a RUNNING campaign to a terminal status."""
    if current is not CampaignStatus.RUNNING:
        raise ValueError(f"cannot finish a campaign from {current.value}; only RUNNING may finish")
    if terminal not in _TERMINAL_STATUSES:
        raise ValueError(f"{terminal.value} is not a terminal status for a campaign")
    return transition(
        current,
        terminal,
        occurred_at=occurred_at,
        reason=reason,
    )
