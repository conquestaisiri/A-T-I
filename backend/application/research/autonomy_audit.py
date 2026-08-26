# backend/application/research/autonomy_audit.py
"""Promotion-engine audit adapter (WS1.6).

Every promotion gate decision and every automatic rollback produced by the
P4-001 engine is logged to the autonomy outcome corpus. These records give the
operator the full promotion/rollback history of a candidate: what was
requested, why it was allowed or refused (the exact gate requirements), and
every automatic demotion with its stay-limit breach reasons.
"""

from __future__ import annotations

from backend.application.interfaces.autonomy_store import AutonomyStore
from backend.application.research.record_adapters import (
    promotion_decision_record,
    rollback_record,
)
from backend.domain.research.promotion import (
    GateDecision,
    ModelEnvironment,
    RollbackDecision,
)
from backend.domain.research.records import PromotionAction


def audit_promotion_evaluation(
    store: AutonomyStore,
    decision: GateDecision,
    *,
    occurred_at: str = "",
) -> None:
    """Persist one gate evaluation (granted or denied) to the audit trail."""
    store.save_promotion_decision(
        promotion_decision_record(
            decision,
            action=PromotionAction.EVALUATE,
            occurred_at=occurred_at,
        )
    )


def audit_promotion_granted(
    store: AutonomyStore,
    decision: GateDecision,
    *,
    occurred_at: str = "",
) -> None:
    """Persist one granted promotion (only meaningful when ``allowed``)."""
    store.save_promotion_decision(
        promotion_decision_record(
            decision,
            action=PromotionAction.PROMOTE,
            occurred_at=occurred_at,
        )
    )


def audit_rollback(
    store: AutonomyStore,
    decision: RollbackDecision,
    *,
    from_environment: ModelEnvironment,
    occurred_at: str = "",
) -> None:
    """Persist one automatic rollback decision to the audit trail."""
    if not decision.rollback:
        return
    store.save_rollback(
        rollback_record(
            decision,
            from_environment=from_environment,
            occurred_at=occurred_at,
        )
    )
