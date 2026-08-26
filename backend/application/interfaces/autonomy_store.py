# backend/application/interfaces/autonomy_store.py
"""Port for the durable autonomy outcome corpus (workstream WS1).

The research/autonomy harnesses produce immutable results in memory, but the
audit's *integration + truth phase* requires the operator to be able to replay
what the system actually did. This store persists the outcome corpus:

- paper campaign lifecycle records + one row per campaign day,
- composed autonomy-program runs (full ladder snapshot),
- promotion gate decisions and automatic rollbacks (operator audit trail).

Implementations must guarantee:

- an append-only ledger: writing a record over an existing key (program id,
  campaign id, or natural day key) raises and never overwrites;
- campaign lifecycle transitions are forward-only: PENDING -> RUNNING ->
  terminal, and a terminal campaign is never reopened;
- day outcomes are complete per campaign when finished: the stored count must
  match the campaign's ``days_run``, so the corpus cannot silently lose a day;
- point-in-time queries are structural: records carry ISO-8601 UTC timestamps
  and can be filtered by candidate, campaign, environment, or terminal state.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.research.records import (
    CampaignRunRecord,
    CampaignStatus,
    DayOutcomeRecord,
    ProgramRunRecord,
    PromotionDecisionRecord,
    RollbackRecord,
)


class AutonomyStore(ABC):
    """Contract for persisting and querying the autonomy outcome corpus."""

    # -- paper campaign lifecycle -------------------------------------------

    @abstractmethod
    def save_campaign(self, record: CampaignRunRecord) -> None:
        """Persist a campaign lifecycle record.

        Raises ``ValueError`` if ``campaign_id`` already exists.
        """
        raise NotImplementedError

    @abstractmethod
    def get_campaign(self, campaign_id: str) -> CampaignRunRecord | None:
        """Return the campaign lifecycle record, or None."""
        raise NotImplementedError

    @abstractmethod
    def list_campaigns(
        self,
        *,
        candidate_id: str | None = None,
        status: CampaignStatus | None = None,
    ) -> list[CampaignRunRecord]:
        """Return matching campaigns, newest first."""
        raise NotImplementedError

    @abstractmethod
    def set_campaign_status(
        self,
        campaign_id: str,
        status: CampaignStatus,
        *,
        days_run: int | None = None,
        sharpe: float | None = None,
        drawdown_pct: float | None = None,
        action: str | None = None,
        reason: str = "",
        completed_at: str = "",
    ) -> CampaignRunRecord:
        """Advance a campaign to ``status``.

        Forward-only: PENDING -> RUNNING -> terminal, and a terminal campaign
        is never reopened. Raises ``ValueError`` for an unknown id or a
        backward/illegal transition.
        """
        raise NotImplementedError

    # -- paper day outcomes --------------------------------------------------

    @abstractmethod
    def save_day_outcome(self, record: DayOutcomeRecord) -> None:
        """Persist one campaign day.

        Raises ``ValueError`` if the (candidate, campaign, day) key already
        exists — a day is written once.
        """
        raise NotImplementedError

    @abstractmethod
    def list_day_outcomes(
        self,
        *,
        candidate_id: str | None = None,
        campaign_id: str | None = None,
    ) -> list[DayOutcomeRecord]:
        """Return matching day outcomes, ordered by day."""
        raise NotImplementedError

    # -- autonomy program runs ----------------------------------------------

    @abstractmethod
    def save_program_run(self, record: ProgramRunRecord) -> None:
        """Persist one composed autonomy-program run.

        Raises ``ValueError`` if ``program_id`` already exists.
        """
        raise NotImplementedError

    @abstractmethod
    def get_program_run(self, program_id: str) -> ProgramRunRecord | None:
        """Return a program run, or None."""
        raise NotImplementedError

    @abstractmethod
    def list_program_runs(
        self,
        *,
        candidate_id: str | None = None,
        final_environment: str | None = None,
    ) -> list[ProgramRunRecord]:
        """Return matching program runs, newest first."""
        raise NotImplementedError

    # -- promotion audit trail ----------------------------------------------

    @abstractmethod
    def save_promotion_decision(self, record: PromotionDecisionRecord) -> None:
        """Persist one promotion gate decision (append-only)."""
        raise NotImplementedError

    @abstractmethod
    def list_promotion_decisions(
        self,
        *,
        candidate_id: str | None = None,
    ) -> list[PromotionDecisionRecord]:
        """Return matching promotion decisions, newest first."""
        raise NotImplementedError

    @abstractmethod
    def save_rollback(self, record: RollbackRecord) -> None:
        """Persist one automatic rollback (append-only)."""
        raise NotImplementedError

    @abstractmethod
    def list_rollbacks(
        self,
        *,
        candidate_id: str | None = None,
    ) -> list[RollbackRecord]:
        """Return matching rollbacks, newest first."""
        raise NotImplementedError
