# backend/application/research/canary_harness.py
"""Live-canary campaign harness (task P4-003).

A bounded, monitored campaign for one promotion-approved candidate before it
may be recommended to production. The harness composes existing, tested
pieces — the promotion gates and automatic rollback (P4-001) — into a
day-by-day driver:

1. **Authorization is checked first.** A canary is live-touch: without
   ``authorized=True`` the campaign refuses to start
   (:class:`~backend.domain.research.canary.CanaryNotAuthorized`). Same
   fail-safe posture as the P0-014 live-trading guard.
2. **The harness monitors; it never executes.** Real orders still flow
   through the operator-wired gateway. The harness only reads each period's
   :class:`DeploymentMonitor` (injected via ``monitor_fn``) and asks
   ``PromotionEngine.rollback_required`` whether to stay or roll back.
3. **Evidence decides the exit.** A stay-limit breach retires the campaign
   early; running out the window with a production grant yields
   ``PRODUCTION_READY``; otherwise ``HOLD`` for more evidence.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from backend.application.research.promotion_engine import PromotionEngine
from backend.domain.research.canary import (
    CanaryAction,
    CanaryNotAuthorized,
    CanaryPeriod,
    CanaryProgramResult,
)
from backend.domain.research.promotion import (
    CandidateEvidence,
    DeploymentMonitor,
    ModelEnvironment,
    PromotionConfig,
    PromotionRequest,
)

logger = logging.getLogger(__name__)

# monitor_fn(day) -> the live performance snapshot for that canary period.
MonitorFn = Callable[[int], DeploymentMonitor]


@dataclass(frozen=True, slots=True)
class CanaryHarnessConfig:
    """Bounds and judging rules for one canary campaign.

    ``campaign_days`` is the maximum number of monitored periods. The
    production gate the harness consults is the P4-001 chain evaluated at
    ``environment_target`` (default ``production``); a candidate must also
    carry the upstream evidence (validation/paper/canary) the chain demands.
    """

    campaign_days: int = 7
    environment_target: ModelEnvironment = ModelEnvironment.PRODUCTION
    promotion_config: PromotionConfig | None = None


class CanaryHarness:
    """Run one bounded canary campaign and judge its exit."""

    def __init__(self, config: CanaryHarnessConfig | None = None) -> None:
        self._config = config or CanaryHarnessConfig()
        self._promotion = PromotionEngine(self._config.promotion_config)

    def run(
        self,
        candidate_id: str,
        evidence: CandidateEvidence,
        monitor_fn: MonitorFn,
        *,
        authorized: bool = False,
    ) -> CanaryProgramResult:
        """Run the campaign for up to ``campaign_days`` monitored periods.

        Parameters
        ----------
        candidate_id:
            The candidate under canary.
        evidence:
            The candidate's accumulated evidence (validation/paper/canary).
        monitor_fn:
            ``monitor_fn(day)`` returns that period's DeploymentMonitor.
        authorized:
            Explicit operator authorization; False refuses to start.
        """
        if not authorized:
            logger.warning("Canary for %s refused: not authorized", candidate_id)
            raise CanaryNotAuthorized(
                f"canary for {candidate_id} requires explicit operator authorization"
            )

        periods: list[CanaryPeriod] = []
        for day in range(1, self._config.campaign_days + 1):
            monitor = monitor_fn(day)
            rollback = self._promotion.rollback_required(monitor)
            if rollback.rollback:
                breach_reason = "; ".join(rollback.reasons)
                periods.append(
                    CanaryPeriod(day=day, action=CanaryAction.RETIRED, reason=breach_reason)
                )
                logger.warning(
                    "Canary for %s retired on day %s: %s",
                    candidate_id,
                    day,
                    breach_reason,
                )
                return CanaryProgramResult(
                    candidate_id=candidate_id,
                    authorized=True,
                    days_run=day,
                    action=CanaryAction.RETIRED,
                    periods=tuple(periods),
                    reason=breach_reason,
                )
            periods.append(CanaryPeriod(day=day, action=CanaryAction.CONTINUE))

        # Campaign window complete: consult the promotion gate for the target.
        request = PromotionRequest(
            candidate_id=candidate_id,
            environment=self._config.environment_target,
            evidence=evidence,
        )
        decision = self._promotion.evaluate(request)
        if decision.allowed:
            return CanaryProgramResult(
                candidate_id=candidate_id,
                authorized=True,
                days_run=self._config.campaign_days,
                action=CanaryAction.PRODUCTION_READY,
                periods=tuple(periods),
                reason="promotion gate granted the target environment",
            )
        return CanaryProgramResult(
            candidate_id=candidate_id,
            authorized=True,
            days_run=self._config.campaign_days,
            action=CanaryAction.HOLD,
            periods=tuple(periods),
            reason="campaign window complete but promotion evidence is insufficient",
        )


def run_canary_campaign(
    candidate_id: str,
    evidence: CandidateEvidence,
    monitor_fn: MonitorFn,
    *,
    authorized: bool = False,
    config: CanaryHarnessConfig | None = None,
) -> CanaryProgramResult:
    """Module-level convenience: run one bounded canary campaign."""
    return CanaryHarness(config).run(
        candidate_id,
        evidence,
        monitor_fn,
        authorized=authorized,
    )
