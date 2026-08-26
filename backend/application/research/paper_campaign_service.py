# backend/application/research/paper_campaign_service.py
"""PaperCampaignService: run and persist one autonomous paper campaign (WS2.3).

The autonomy harnesses produce in-memory results; the outcome corpus requires
them persisted. WS2.1 gave us the campaign lifecycle authority (state machine);
WS2.2 gave us a live day-function; this service composes them into the durable
lifecycle the operator can watch:

1. **create** — a PENDING campaign record with its target window.
2. **start** — idempotent PENDING -> RUNNING (a retried tick is a no-op).
3. **run** — executes the day-function for every campaign day, persisting each
   day outcome as it happens and finishing the record with the harness verdict
   (COMPLETED_* or RETIRED) plus its summary metrics.
4. **cancel** — PENDING or RUNNING -> CANCELLED, cooperative: a running day
   observes the cancel at the next day boundary and stands down.

Safety posture (Constitution: keep the operator in charge):
- The service never executes anything itself. The day-function is injected
  (live path from WS2.2, or a deterministic fake in tests).
- Cancellation is recorded first, then honoured: no race allows a cancelled
  campaign to finish.
- A terminal campaign is never reopened; running a finished campaign raises.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from backend.application.interfaces.autonomy_store import AutonomyStore
from backend.application.research.paper_autonomy import (
    PaperCampaignConfig,
    PaperDayFn,
    run_paper_campaign,
)
from backend.application.research.record_adapters import day_outcome_record
from backend.domain.research.campaign_state import cancel, is_terminal, start
from backend.domain.research.paper_campaign import (
    PaperCampaignAction,
    PaperCampaignResult,
    PaperDayOutcome,
)
from backend.domain.research.promotion import CandidateEvidence, PromotionConfig
from backend.domain.research.records import CampaignRunRecord, CampaignStatus

logger = logging.getLogger(__name__)

# clock() -> ISO-8601 UTC timestamp, injectable for deterministic tests.
Clock = Callable[[], str]


class CampaignAlreadyFinished(ValueError):
    """Raised when a terminal campaign is asked to start or run again."""


class CampaignCancelled(RuntimeError):
    """Raised when a running campaign observes an operator cancel."""


def _default_clock() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class PaperCampaignService:
    """Durable owner of one paper campaign's lifecycle."""

    def __init__(
        self,
        *,
        store: AutonomyStore,
        promotion_config: PromotionConfig | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._store = store
        self._promotion_config = promotion_config
        self._clock = clock or _default_clock

    # -- lifecycle ----------------------------------------------------------

    def create_campaign(
        self,
        *,
        candidate_id: str,
        campaign_id: str,
        target_days: int,
        initial_evidence: CandidateEvidence,
    ) -> CampaignRunRecord:
        """Register a PENDING campaign with its target window.

        ``initial_evidence`` is stored only in memory at this stage (the
        outcome corpus records what happened, not derived state); the caller
        supplies it again at :meth:`run_campaign`.
        """
        if target_days <= 0:
            raise ValueError("target_days must be positive")
        record = CampaignRunRecord(
            candidate_id=candidate_id,
            campaign_id=campaign_id,
            status=CampaignStatus.PENDING,
            target_days=target_days,
            started_at=self._clock(),
        )
        self._store.save_campaign(record)
        return record

    def start_campaign(self, campaign_id: str) -> CampaignRunRecord:
        """Move a campaign PENDING -> RUNNING, idempotently."""
        current = self._require_campaign(campaign_id)
        transition = start(current.status, occurred_at=self._clock())
        if transition is None:
            return current  # already RUNNING; a retried tick is a no-op
        return self._store.set_campaign_status(
            campaign_id,
            transition.to_status,
        )

    def cancel_campaign(
        self, campaign_id: str, *, reason: str = "operator request"
    ) -> CampaignRunRecord:
        """Cancel a PENDING or RUNNING campaign (terminal stays terminal).

        For a RUNNING campaign, a concurrent :meth:`run_campaign` observes the
        cancel at the next day boundary and stops; it never finishes a
        cancelled record.
        """
        current = self._require_campaign(campaign_id)
        transition = cancel(current.status, occurred_at=self._clock(), reason=reason)
        return self._store.set_campaign_status(
            campaign_id,
            transition.to_status,
            reason=transition.reason,
            completed_at=transition.occurred_at,
        )

    # -- execution ----------------------------------------------------------

    def run_campaign(
        self,
        *,
        campaign_id: str,
        candidate_id: str,
        initial_evidence: CandidateEvidence,
        day_fn: PaperDayFn,
    ) -> PaperCampaignResult:
        """Run the campaign day-function to its verdict, persisting as it goes.

        Parameters
        ----------
        campaign_id:
            The campaign to execute. Must not be terminal.
        candidate_id:
            The candidate under apprenticeship.
        initial_evidence:
            Evidence before this campaign (validation results; paper counters
            are the base to accumulate from).
        day_fn:
            ``day_fn(day)`` runs the decision loop for that campaign day.

        Returns the harness verdict with accumulated evidence.
        """
        record = self._require_campaign(campaign_id)
        if is_terminal(record.status):
            raise CampaignAlreadyFinished(
                f"campaign {campaign_id} is {record.status.value}; nothing left to run"
            )
        if record.status is not CampaignStatus.RUNNING:
            self.start_campaign(campaign_id)

        # Persist each day outcome as it is produced; honour an operator
        # cancel at the next day boundary.
        def persisting_day(day: int) -> PaperDayOutcome:
            latest = self._store.get_campaign(campaign_id)
            if latest is not None and latest.status is CampaignStatus.CANCELLED:
                raise CampaignCancelled(
                    f"campaign {campaign_id} cancelled at day {day - 1} boundary"
                )
            outcome = day_fn(day)
            self._store.save_day_outcome(
                day_outcome_record(
                    candidate_id,
                    campaign_id,
                    outcome,
                    recorded_at=self._clock(),
                )
            )
            return outcome

        result = run_paper_campaign(
            candidate_id,
            initial_evidence,
            persisting_day,
            config=PaperCampaignConfig(
                campaign_days=record.target_days,
                promotion_config=self._promotion_config,
            ),
        )

        # If the operator cancelled mid-run, the record is already CANCELLED;
        # never finish a cancelled record (that would reopen it).
        latest = self._store.get_campaign(campaign_id)
        if latest is not None and latest.status is CampaignStatus.CANCELLED:
            raise CampaignCancelled(
                f"campaign {campaign_id} cancelled; verdict {result.action.value} not persisted"
            )

        terminal = _terminal_status(result.action)
        self._store.set_campaign_status(
            campaign_id,
            terminal,
            days_run=result.days_run,
            sharpe=result.sharpe,
            drawdown_pct=result.drawdown_pct,
            action=result.action.value,
            reason=result.reason,
            completed_at=self._clock(),
        )
        logger.info(
            "Campaign %s finished %s after %s days (sharpe=%s)",
            campaign_id,
            terminal.value,
            result.days_run,
            result.sharpe,
        )
        return result

    # -- helpers ------------------------------------------------------------

    def _require_campaign(self, campaign_id: str) -> CampaignRunRecord:
        record = self._store.get_campaign(campaign_id)
        if record is None:
            raise ValueError(f"unknown campaign {campaign_id}")
        return record


def _terminal_status(action: PaperCampaignAction) -> CampaignStatus:
    """Map a harness verdict to the lifecycle terminal status."""
    if action is PaperCampaignAction.RETIRED:
        return CampaignStatus.RETIRED
    return CampaignStatus.COMPLETED
