# backend/application/research/record_adapters.py
"""Adapters from rich harness results to durable autonomy records (WS1.4).

The research harnesses produce immutable, well-typed results
(``AutonomyProgramResult``, ``PaperCampaignResult``, ``GateDecision``,
``RollbackDecision``). The outcome corpus stores a simpler persistence view —
:mod:`backend.domain.research.records`. These pure functions translate the
rich domain results into records so the store layer stays simple and the
adapter logic is unit-testable without a database.

All adapters are deterministic: the same result always produces the same
record, so a replay of a stored program run reconstructs the exact result.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.research.autonomy_program import AutonomyProgramResult
from backend.domain.research.paper_campaign import (
    PaperCampaignAction,
    PaperCampaignResult,
    PaperDayOutcome,
)
from backend.domain.research.promotion import (
    GateDecision,
    ModelEnvironment,
    RollbackDecision,
)
from backend.domain.research.records import (
    CampaignRunRecord,
    CampaignStatus,
    DayOutcomeRecord,
    ProgramRunRecord,
    PromotionAction,
    PromotionDecisionRecord,
    RollbackRecord,
    StageSnapshot,
)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def campaign_record_from_result(
    result: PaperCampaignResult,
    *,
    campaign_id: str,
    target_days: int,
    started_at: str,
    completed_at: str = "",
) -> CampaignRunRecord:
    """Lifecycle record for a finished paper campaign.

    The terminal status follows the verdict: ``COMPLETED_ADVANCED`` and
    ``COMPLETED_HOLD`` complete the campaign; ``RETIRED`` retires it.
    ``target_days`` is the configured window; ``days_run`` is what actually
    happened before the verdict.
    """
    status = (
        CampaignStatus.RETIRED
        if result.action is PaperCampaignAction.RETIRED
        else CampaignStatus.COMPLETED
    )
    return CampaignRunRecord(
        candidate_id=result.candidate_id,
        campaign_id=campaign_id,
        status=status,
        action=result.action,
        target_days=target_days,
        days_run=result.days_run,
        sharpe=result.sharpe,
        drawdown_pct=result.drawdown_pct,
        reason=result.reason,
        started_at=started_at,
        completed_at=completed_at,
    )


def day_outcome_record(
    candidate_id: str,
    campaign_id: str,
    outcome: PaperDayOutcome,
    *,
    recorded_at: str = "",
) -> DayOutcomeRecord:
    """One day outcome as produced live by the campaign runner."""
    return DayOutcomeRecord(
        candidate_id=candidate_id,
        campaign_id=campaign_id,
        day=outcome.day,
        return_pct=outcome.return_pct,
        expected_return_pct=outcome.expected_return_pct,
        failed_orders=outcome.failed_orders,
        total_orders=outcome.total_orders,
        recorded_at=recorded_at or _now_iso(),
    )


def program_run_record(
    result: AutonomyProgramResult,
    *,
    started_at: str,
    completed_at: str = "",
) -> ProgramRunRecord:
    """Durable copy of one composed autonomy-program run."""
    return ProgramRunRecord(
        program_id=result.program_id,
        candidate_id=result.candidate_id,
        final_environment=result.final_environment,
        earned=result.earned,
        stages=tuple(
            StageSnapshot(
                stage=s.stage.value,
                verdict=s.verdict.value,
                reason=s.reason,
                evidence=s.evidence.as_dict() if s.evidence else None,
            )
            for s in result.stages
        ),
        notes=result.notes,
        started_at=started_at,
        completed_at=completed_at,
    )


def promotion_decision_record(
    decision: GateDecision,
    *,
    action: PromotionAction = PromotionAction.EVALUATE,
    occurred_at: str = "",
) -> PromotionDecisionRecord:
    """Audit record for one promotion gate evaluation."""
    return PromotionDecisionRecord(
        candidate_id=decision.candidate_id,
        action=action,
        environment=decision.environment,
        allowed=decision.allowed,
        required=decision.required,
        satisfied=decision.satisfied,
        reasons=decision.reasons,
        occurred_at=occurred_at or _now_iso(),
    )


def rollback_record(
    decision: RollbackDecision,
    *,
    from_environment: ModelEnvironment,
    occurred_at: str = "",
) -> RollbackRecord:
    """Audit record for one automatic rollback decision.

    ``RollbackDecision`` names the demotion *target* but not the environment
    the candidate was demoted *from*, so the caller supplies ``from_environment``.
    """
    return RollbackRecord(
        candidate_id=decision.candidate_id,
        from_environment=from_environment,
        to_environment=decision.to_environment,
        reasons=decision.reasons,
        occurred_at=occurred_at or _now_iso(),
    )
