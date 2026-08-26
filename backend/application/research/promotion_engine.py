# backend/application/research/promotion_engine.py
"""Controlled model-promotion engine (task P4-001).

A candidate model advances through ``research -> validation -> paper ->
canary -> production`` only by clearing the gate for each environment. The
gate is *cumulative*: promoting into a later environment re-checks every
earlier requirement against the candidate's current evidence, so stale or
leapfrogged evidence can never win a promotion.

Design rules
------------
- **Gates fail closed.** Every requirement is checked; ``allowed`` is True
  only when all hold. ``None`` evidence (a field not yet applicable) is a
  missing requirement, not a free pass.
- **Every gate in the chain is kept.** Promoting into PAPER validates the
  research→validation gate on top of the validation→paper gate; production
  in turn requires a healthy canary *and* everything before it. A candidate
  can never skip a stage.
- **Rollback is automatic.** ``rollback_required`` demotes a deployed
  candidate one environment as soon as it breaches drawdown, underperformance
  against expectation, or an operational-error limit. RESEARCH cannot be
  demoted further — the candidate is simply grounded there.
"""

from __future__ import annotations

import logging

from backend.domain.research.promotion import (
    CandidateEvidence,
    DeploymentMonitor,
    GateDecision,
    ModelEnvironment,
    PromotionConfig,
    PromotionRequest,
    RollbackDecision,
    previous_environment,
)

logger = logging.getLogger(__name__)


class PromotionEngine:
    """Deterministic judge of promotion applications and stay-or-demote checks."""

    def __init__(self, config: PromotionConfig | None = None) -> None:
        self._config = config or PromotionConfig()

    def evaluate(self, request: PromotionRequest) -> GateDecision:
        """Evaluate one promotion application against the target gate.

        The requirements applied are the *cumulative* chain from RESEARCH up to
        ``request.environment``: promoting into CANARY re-checks the
        validation and paper gates on top of canary's own, so evidence can
        neither go stale nor leapfrog an intermediate stage. Promoting into
        VALIDATION requires only that the candidate exists.
        """
        evidence = request.evidence
        checks = self._checks_for(request.environment, evidence)

        required: list[str] = []
        satisfied: list[str] = []
        for requirement, met in checks.items():
            required.append(requirement)
            if met:
                satisfied.append(requirement)

        target = ModelEnvironment(request.environment)
        allowed = len(required) == len(satisfied)
        if not allowed:
            logger.info(
                "Promotion of %s into %s denied (missing: %s)",
                request.candidate_id,
                target.value,
                ", ".join(r for r in required if r not in satisfied),
            )
        return GateDecision(
            candidate_id=request.candidate_id,
            environment=target,
            allowed=allowed,
            required=tuple(required),
            satisfied=tuple(satisfied),
        )

    def _checks_for(self, target: ModelEnvironment, evidence: CandidateEvidence) -> dict[str, bool]:
        """Cumulative gate requirements for promoting into ``target``."""
        checks: dict[str, bool] = {
            "research complete": True,
        }
        if target is ModelEnvironment.RESEARCH:
            # Nothing promotes out of research; the gate is only evaluated
            # for a candidate leaving it. RESEARCH as a target is degenerate.
            return checks

        if target in (ModelEnvironment.PAPER, ModelEnvironment.CANARY, ModelEnvironment.PRODUCTION):
            checks["validation sample count"] = self._at_least(
                evidence.validation_samples, self._config.validation_samples_min
            )
            checks["validation sharpe"] = self._at_least(
                evidence.validation_sharpe, self._config.validation_sharpe_min
            )
        if target in (ModelEnvironment.CANARY, ModelEnvironment.PRODUCTION):
            checks["paper deployment window"] = self._at_least(
                evidence.paper_days_deployed, self._config.paper_period_days_min
            )
            checks["paper sharpe"] = self._at_least(
                evidence.paper_sharpe, self._config.paper_sharpe_min
            )
        if target is ModelEnvironment.PRODUCTION:
            checks["canary deployment window"] = self._at_least(
                evidence.canary_days_deployed, self._config.canary_period_days_min
            )
        return checks

    def rollback_required(self, monitor: DeploymentMonitor) -> RollbackDecision:
        """Automatically demote a deployed candidate that breaches a stay limit.

        Breaches are evaluated in priority order: operational health, then
        drawdown, then underperformance. The candidate drops one environment
        (PRODUCTION→canary, CANARY→paper, PAPER→validation); a RESEARCH
        candidate that breaches is grounded with no lower environment.
        """
        environment = ModelEnvironment(monitor.environment)
        reasons: list[str] = []

        if monitor.failed_orders_pct > self._config.max_failed_orders_pct:
            reasons.append(
                f"failed orders {monitor.failed_orders_pct:.2f}% > "
                f"{self._config.max_failed_orders_pct:.2f}%"
            )
        if monitor.drawdown_pct > self._config.max_drawdown_pct:
            reasons.append(
                f"drawdown {monitor.drawdown_pct:.2f}% > {self._config.max_drawdown_pct:.2f}%"
            )
        if monitor.underperformance_bps > self._config.max_underperformance_bps:
            reasons.append(
                f"underperformance {monitor.underperformance_bps:.2f} bps > "
                f"{self._config.max_underperformance_bps:.2f} bps"
            )

        if not reasons:
            return RollbackDecision(
                candidate_id=monitor.candidate_id,
                rollback=False,
                to_environment=None,
            )

        target = previous_environment(environment)
        logger.warning(
            "Automatic rollback of %s from %s to %s: %s",
            monitor.candidate_id,
            environment.value,
            target.value if target else "research (grounded)",
            "; ".join(reasons),
        )
        return RollbackDecision(
            candidate_id=monitor.candidate_id,
            rollback=True,
            to_environment=target,
            reasons=tuple(reasons),
        )

    def _at_least(self, value: float | None, floor: float) -> bool:
        if value is None:
            return False
        return value >= floor


def promote(request: PromotionRequest, config: PromotionConfig | None = None) -> GateDecision:
    """Module-level convenience for a single gate evaluation."""
    return PromotionEngine(config).evaluate(request)


def rollback_required(
    monitor: DeploymentMonitor, config: PromotionConfig | None = None
) -> RollbackDecision:
    """Module-level convenience for a single stay-or-demote check."""
    return PromotionEngine(config).rollback_required(monitor)


def promotion_chain() -> tuple[str, ...]:
    """The promotion order as environment ids (audit / documentation aid)."""
    from backend.domain.research.promotion import ENVIRONMENT_CHAIN

    return tuple(environment.value for environment in ENVIRONMENT_CHAIN)
