# backend/application/research/death_system.py
"""Death system (tasks T3-26-1 / T3-28-1): degrade -> demote -> retire.

Library-only. This service decides *and applies* demotions: it takes the
evidence the system has already earned (the edge monitor's advisory trigger
and the campaign verdicts recorded on the passport by T3-24-1 / T3-25-1),
maps it onto the explicit ladder in
:mod:`backend.domain.research.death_system`, and applies the verdict as a
passport lifecycle transition through the evidence engine.

T3-28-1 (retirement operationalized): RETIRED is the terminal tombstone —
``evaluate`` never re-litigates a retired passport (STAY), ``apply`` never
moves one again, and the evidence engine refuses every transition,
re-evaluation or new campaign on a retired passport (only the rollback
record that closes the death audit remains appendable). Retirement is
enforced by the engine, not by convention.

Guardrail: nothing in the live path imports or calls this service. It exists
for the operator to run deliberately (or for a future, evidence-gated
autonomy layer to invoke through its own sanctioned channel).
"""

from __future__ import annotations

import logging

from backend.application.research.edge_monitor import (
    environment_for_status,
    status_for_environment,
)
from backend.application.research.evidence_engine import EvidenceEngine
from backend.domain.research.death_system import (
    DeathDecision,
    DemotionAction,
    harshest,
)
from backend.domain.research.edge_monitor import EdgeDemotionTrigger
from backend.domain.research.passport import PassportStatus, StrategyPassport
from backend.domain.research.promotion import (
    ModelEnvironment,
    RollbackDecision,
    previous_environment,
)

logger = logging.getLogger(__name__)

_CAMPAIGN_RETIRED_ACTION = "retired"


class DeathSystemService:
    """Decide and apply demotion verdicts for one passport.

    The service is stateless: every decision is recomputed from the
    passport's recorded evidence plus the caller-supplied edge trigger, so
    two calls with the same evidence agree (deterministic, auditable).
    """

    def evaluate(
        self,
        passport: StrategyPassport,
        trigger: EdgeDemotionTrigger | None = None,
    ) -> DeathDecision:
        """Compute the death-system verdict for one passport.

        Evidence sources (both advisory, both already on the passport or
        provided by the monitor):

        - the edge monitor's ``trigger`` — fires only on a DECAYED verdict
          and recommends the environment one step down the chain;
        - the passport's campaign sections — a RETIRED paper or canary
          campaign means the P4-001 stay limits already rolled it back
          automatically.

        Ladder (explicit risk precedence, see the domain module): a fired
        edge trigger DEGRADEs one step; a retired campaign DEMOTEs two
        steps; anything with no lower step RETIREs; nothing fires -> STAY.
        When sources disagree, the harshest action wins.

        T3-28-1 (terminality): a retired passport is never re-litigated —
        ``evaluate`` returns STAY immediately (a corpse cannot be
        double-dead, and nothing may resurrect it). The death verdict is
        final; a revised hypothesis starts as a new passport.
        """
        if passport.status is PassportStatus.RETIRED:
            return DeathDecision(
                passport_id=passport.passport_id,
                action=DemotionAction.STAY,
                to_environment=None,
                reasons=("passport is retired (terminal): the death verdict is final",),
                from_status=passport.status,
            )
        candidates: list[tuple[DemotionAction, ModelEnvironment | None]] = []
        reasons: list[str] = []

        environment = environment_for_status(passport.status.value)

        if trigger is not None and trigger.triggered:
            target = (
                self._as_environment(trigger.recommended_environment)
                if trigger.recommended_environment
                else None
            )
            if target is None and environment is not None:
                # Edge decay at the bottom of the chain: nothing lower to
                # fall back to, the passport dies.
                candidates.append((DemotionAction.RETIRE, None))
            else:
                candidates.append((DemotionAction.DEGRADE, target))
            reasons.append(f"edge monitor: {trigger.reason}")

        campaign_reasons = self._campaign_breach_reasons(passport)
        if campaign_reasons:
            target = previous_environment(environment) if environment else None
            if target is None:
                candidates.append((DemotionAction.RETIRE, None))
            else:
                target = previous_environment(target)  # two steps down
                candidates.append(
                    (DemotionAction.DEMOTE, target if target is not None else environment)
                )
            reasons.extend(campaign_reasons)

        if not candidates:
            action, target = DemotionAction.STAY, None
        else:
            action = harshest(*(c for c, _ in candidates))
            target = next((t for a, t in candidates if a is action), None)

        return DeathDecision(
            passport_id=passport.passport_id,
            action=action,
            to_environment=target,
            reasons=tuple(reasons),
            from_status=passport.status,
        )

    def apply(
        self,
        engine: EvidenceEngine,
        decision: DeathDecision,
        *,
        reason: str | None = None,
    ) -> StrategyPassport:
        """Apply a death-system verdict as a passport lifecycle transition.

        STAY is a no-op (returns the passport unchanged, nothing recorded).
        RETIRE moves the passport to the terminal ``RETIRED`` status.
        DEGRADE/DEMOTE move it to the status of the decision's target
        environment. The transition itself is recorded as an append-only
        lifecycle event by the evidence engine — the death is on the audit
        trail, never silent.
        """
        if decision.action is DemotionAction.STAY:
            return engine.passport(decision.passport_id)  # type: ignore[return-value]

        target_status = self._status_for_decision(decision)
        reason = reason or " | ".join(decision.reasons) or decision.action.value
        updated = engine.transition(
            decision.passport_id,
            to_status=target_status,
            reason=reason,
        )
        engine.record_rollback(
            decision.passport_id,
            decision=self._rollback_record(decision),
            reason=reason,
        )
        return updated

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _rollback_record(decision: DeathDecision) -> RollbackDecision:
        """Shape the death verdict into a P4-001 rollback record.

        RETIRE is a terminal rollback: ``to_environment`` is None (there
        is nowhere to fall back to). DEGRADE/DEMOTE name the demotion
        target explicitly so the audit trail shows exactly where the
        strategy was rolled back to.
        """
        return RollbackDecision(
            candidate_id=decision.passport_id,
            rollback=True,
            to_environment=(
                decision.to_environment
                if decision.action in (DemotionAction.DEGRADE, DemotionAction.DEMOTE)
                else None
            ),
            reasons=decision.reasons,
        )

    @staticmethod
    def _as_environment(value: str | None) -> ModelEnvironment | None:
        if value is None:
            return None
        try:
            return ModelEnvironment(value)
        except ValueError:
            return None

    @staticmethod
    def _status_for_decision(decision: DeathDecision) -> PassportStatus:
        if decision.action is DemotionAction.RETIRE:
            return PassportStatus.RETIRED
        status = (
            status_for_environment(decision.to_environment) if decision.to_environment else None
        )
        try:
            return PassportStatus(status) if status else PassportStatus.RETIRED
        except ValueError:
            return PassportStatus.RETIRED

    @staticmethod
    def _campaign_breach_reasons(passport: StrategyPassport) -> list[str]:
        """Read the recorded campaign verdicts and name the breaches.

        The campaigns were appended verbatim by T3-24-1 / T3-25-1; a
        RETIRED action is the P4-001 stay-limit rollback and is treated as
        an automatic demotion source.
        """
        reasons: list[str] = []
        paper = passport.paper_evidence.get("paper_campaign") or {}
        if paper.get("action") == _CAMPAIGN_RETIRED_ACTION:
            reasons.append(f"paper campaign retired: {paper.get('reason') or 'stay-limit breach'}")
        canary = passport.live_evidence.get("canary") or {}
        if canary.get("action") == _CAMPAIGN_RETIRED_ACTION:
            reasons.append(
                f"canary campaign retired: {canary.get('reason') or 'stay-limit breach'}"
            )
        return reasons
