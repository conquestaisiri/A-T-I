# backend/application/research/outcome_aggregation.py
"""Outcome aggregation over the autonomy corpus (WS2.4).

The store persists immutable records; this module turns those records into the
operator-observable picture: what a candidate actually earned, how many paper
days it survived, how many retirements/cancellations it accumulated, and
whether the corpus for a finished campaign is *complete* (every run day has a
stored outcome — the store's own durability contract).

Rules
-----
- **Read-only and pure.** No writes, no time sources, no storage beyond the
  injected ``AutonomyStore``. Everything is a plain function over records.
- **Terminal-only for verdicts.** PENDING/RUNNING campaigns are *not* counted
  as outcomes (nothing has been decided); they are surfaced separately as
  ``in_flight`` so the operator can see what is still running.
- **Completeness is structural.** A finished campaign must have exactly
  ``days_run`` stored day outcomes; anything else is a corpus gap and is
  reported, never silently assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from backend.application.interfaces.autonomy_store import AutonomyStore
from backend.domain.research.records import CampaignRunRecord, CampaignStatus


@dataclass(frozen=True, slots=True)
class CampaignSummary:
    """Aggregated view of one finished campaign."""

    campaign_id: str
    status: CampaignStatus
    target_days: int
    days_run: int
    sharpe: float | None
    drawdown_pct: float | None
    stored_days: int
    complete: bool  # stored_days == days_run


@dataclass(frozen=True, slots=True)
class CandidateOutcomes:
    """What one candidate earned across its terminal campaigns."""

    candidate_id: str
    campaigns: tuple[CampaignSummary, ...] = ()
    completed: int = 0
    retired: int = 0
    cancelled: int = 0
    in_flight: int = 0  # PENDING or RUNNING, not yet a verdict
    total_days_run: int = 0
    total_target_days: int = 0
    best_sharpe: float | None = None
    complete_campaigns: int = 0
    gaps: tuple[str, ...] = field(default_factory=tuple)  # campaign_ids with missing days

    @property
    def days_run_ratio(self) -> float:
        """Fraction of target window actually executed (0.0 on no targets)."""
        if self.total_target_days == 0:
            return 0.0
        return round(self.total_days_run / self.total_target_days, 4)


@dataclass(frozen=True, slots=True)
class CorpusOutcomes:
    """Aggregate across every candidate in the corpus."""

    candidates: int = 0
    campaigns: int = 0
    completed: int = 0
    retired: int = 0
    cancelled: int = 0
    in_flight: int = 0
    total_days_run: int = 0


def _summarise(
    record: CampaignRunRecord,
    stored_days: int,
) -> CampaignSummary:
    return CampaignSummary(
        campaign_id=record.campaign_id,
        status=record.status,
        target_days=record.target_days,
        days_run=record.days_run,
        sharpe=record.sharpe,
        drawdown_pct=record.drawdown_pct,
        stored_days=stored_days,
        complete=record.days_run == stored_days,
    )


def campaign_summary(
    store: AutonomyStore,
    campaign_id: str,
) -> CampaignSummary | None:
    """Summarise one campaign (stored day count checked against days_run)."""
    record = store.get_campaign(campaign_id)
    if record is None:
        return None
    stored_days = len(store.list_day_outcomes(campaign_id=campaign_id))
    return _summarise(record, stored_days)


def candidate_outcomes(
    store: AutonomyStore,
    candidate_id: str,
) -> CandidateOutcomes:
    """Aggregate all of a candidate's campaigns, terminal as verdicts."""
    campaigns = store.list_campaigns(candidate_id=candidate_id)
    summaries: list[CampaignSummary] = []
    completed = retired = cancelled = in_flight = 0
    total_days_run = total_target_days = 0
    best_sharpe: float | None = None
    complete_campaigns = 0
    gaps: list[str] = []

    for record in campaigns:
        stored_days = len(
            store.list_day_outcomes(candidate_id=candidate_id, campaign_id=record.campaign_id)
        )
        summary = _summarise(record, stored_days)
        summaries.append(summary)

        total_target_days += record.target_days
        if record.status is CampaignStatus.PENDING or record.status is CampaignStatus.RUNNING:
            in_flight += 1
            continue

        total_days_run += record.days_run
        if record.status is CampaignStatus.COMPLETED:
            completed += 1
        elif record.status is CampaignStatus.RETIRED:
            retired += 1
        elif record.status is CampaignStatus.CANCELLED:
            cancelled += 1

        if record.sharpe is not None and (best_sharpe is None or record.sharpe > best_sharpe):
            best_sharpe = record.sharpe
        if summary.complete:
            complete_campaigns += 1
        else:
            gaps.append(record.campaign_id)

    return CandidateOutcomes(
        candidate_id=candidate_id,
        campaigns=tuple(summaries),
        completed=completed,
        retired=retired,
        cancelled=cancelled,
        in_flight=in_flight,
        total_days_run=total_days_run,
        total_target_days=total_target_days,
        best_sharpe=best_sharpe,
        complete_campaigns=complete_campaigns,
        gaps=tuple(gaps),
    )


def corpus_outcomes(store: AutonomyStore) -> CorpusOutcomes:
    """Aggregate across the whole corpus (one row per candidate)."""
    candidates = {c.candidate_id for c in store.list_campaigns()}
    campaigns = completed = retired = cancelled = in_flight = total_days_run = 0
    for candidate_id in candidates:
        outcomes = candidate_outcomes(store, candidate_id)
        campaigns += len(outcomes.campaigns)
        completed += outcomes.completed
        retired += outcomes.retired
        cancelled += outcomes.cancelled
        in_flight += outcomes.in_flight
        total_days_run += outcomes.total_days_run
    return CorpusOutcomes(
        candidates=len(candidates),
        campaigns=campaigns,
        completed=completed,
        retired=retired,
        cancelled=cancelled,
        in_flight=in_flight,
        total_days_run=total_days_run,
    )
