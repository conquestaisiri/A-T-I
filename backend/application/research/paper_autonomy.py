# backend/application/research/paper_autonomy.py
"""Long autonomous paper-campaign harness (task P4-004, build-order #37).

A candidate earns canary eligibility by surviving a long, unattended paper
campaign in the sandbox. No operator can be at the switch for weeks, so the
campaign must be mechanically safe by construction: performance is measured,
stay-limit breaches auto-retire, and nothing here ever touches a live venue.

The harness composes the existing, tested pieces:

1. **Measurement.** Each day an injected ``day_fn`` runs the decision loop
   against the paper simulator and returns that day's outcome. The runner
   tracks running drawdown, a simple Sharpe from daily returns, and
   underperformance against the day-level expected return.
2. **Automatic stay discipline.** Every day the P4-001 rollback engine judges
   the accumulated ``DeploymentMonitor``; a breach retires the campaign with
   the specific reasons preserved.
3. **Earned evidence.** Days survived accumulate into ``paper_days_deployed``,
   and the realised Sharpe becomes ``paper_sharpe``. The candidate is then
   submitted to the P4-001 gate for CANARY: granted -> COMPLETED_ADVANCED,
   not granted -> COMPLETED_HOLD, breach -> RETIRED.
4. **No live path.** ``eligible_for_canary`` is eligibility, not a start.
   The canary (P4-003) still demands explicit operator ``authorized=True``.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.application.research.promotion_engine import PromotionEngine
from backend.domain.research.paper_campaign import (
    PaperCampaignAction,
    PaperCampaignResult,
    PaperDay,
    PaperDayAction,
    PaperDayOutcome,
)
from backend.domain.research.promotion import (
    CandidateEvidence,
    DeploymentMonitor,
    ModelEnvironment,
    PromotionConfig,
    PromotionRequest,
)

logger = logging.getLogger(__name__)

# day_fn(day) -> one day of decision-loop execution in paper.
PaperDayFn = Callable[[int], PaperDayOutcome]

_TRADING_DAYS_PER_YEAR = 252.0


@dataclass(frozen=True, slots=True)
class PaperCampaignConfig:
    """Bounds and judging rules for one autonomous paper campaign."""

    campaign_days: int = 30
    promotion_config: PromotionConfig | None = None


class PaperAutonomyRunner:
    """Run one long paper campaign and judge its exit."""

    def __init__(self, config: PaperCampaignConfig | None = None) -> None:
        self._config = config or PaperCampaignConfig()
        self._promotion_config = self._config.promotion_config
        self._promotion = PromotionEngine(self._promotion_config)

    def run(
        self,
        candidate_id: str,
        initial_evidence: CandidateEvidence,
        day_fn: PaperDayFn,
    ) -> PaperCampaignResult:
        """Run up to ``campaign_days`` of decision-loop execution in paper.

        Parameters
        ----------
        candidate_id:
            The candidate under apprenticeship.
        initial_evidence:
            The candidate's evidence before this paper campaign (validation
            results; paper counters are taken as the base to accumulate from).
        day_fn:
            ``day_fn(day)`` runs the decision loop against the paper
            simulator for that campaign day and returns its outcome.
        """
        days_run = 0
        periods: list[PaperDay] = []
        daily_returns: list[float] = []
        expected_returns: list[float] = []
        failed_orders_total = 0
        total_orders = 0
        equity = 1.0
        peak_equity = 1.0
        max_drawdown_pct = 0.0

        for day in range(1, self._config.campaign_days + 1):
            outcome = day_fn(day)
            days_run = day
            daily_returns.append(outcome.return_pct)
            expected_returns.append(outcome.expected_return_pct)
            failed_orders_total += outcome.failed_orders
            total_orders += outcome.total_orders

            equity *= 1.0 + outcome.return_pct / 100.0
            peak_equity = max(peak_equity, equity)
            drawdown_pct = (peak_equity - equity) / peak_equity * 100.0
            max_drawdown_pct = max(max_drawdown_pct, drawdown_pct)

            monitor = self._monitor(
                candidate_id,
                max_drawdown_pct,
                daily_returns,
                expected_returns,
                failed_orders_total,
                total_orders,
            )
            rollback = self._promotion.rollback_required(monitor)
            if rollback.rollback:
                breach_reason = "; ".join(rollback.reasons)
                periods.append(
                    PaperDay(
                        day=day,
                        action=PaperDayAction.RETIRED,
                        return_pct=outcome.return_pct,
                        drawdown_pct=drawdown_pct,
                        reason=breach_reason,
                    )
                )
                logger.warning(
                    "Paper campaign %s retired on day %s: %s",
                    candidate_id,
                    day,
                    breach_reason,
                )
                evidence = self._demote(initial_evidence)
                return PaperCampaignResult(
                    candidate_id=candidate_id,
                    days_run=days_run,
                    action=PaperCampaignAction.RETIRED,
                    sharpe=self._sharpe(daily_returns),
                    drawdown_pct=max_drawdown_pct,
                    evidence=evidence,
                    periods=tuple(periods),
                    reason=breach_reason,
                )
            periods.append(
                PaperDay(
                    day=day,
                    action=PaperDayAction.CONTINUE,
                    return_pct=outcome.return_pct,
                    drawdown_pct=drawdown_pct,
                )
            )

        # Full window survived: submit to the P4-001 canary gate with earned
        # paper evidence. Granted -> COMPLETED_ADVANCED, otherwise HOLD.
        evidence = self._accumulate(initial_evidence, days_run, daily_returns)
        decision = self._promotion.evaluate(
            PromotionRequest(
                candidate_id=candidate_id,
                environment=ModelEnvironment.CANARY,
                evidence=evidence,
            )
        )
        if decision.allowed:
            return PaperCampaignResult(
                candidate_id=candidate_id,
                days_run=days_run,
                action=PaperCampaignAction.COMPLETED_ADVANCED,
                sharpe=self._sharpe(daily_returns),
                drawdown_pct=max_drawdown_pct,
                evidence=evidence,
                periods=tuple(periods),
                reason="full paper window run; canary gate granted",
            )
        return PaperCampaignResult(
            candidate_id=candidate_id,
            days_run=days_run,
            action=PaperCampaignAction.COMPLETED_HOLD,
            sharpe=self._sharpe(daily_returns),
            drawdown_pct=max_drawdown_pct,
            evidence=evidence,
            periods=tuple(periods),
            reason="full paper window run but canary evidence is insufficient",
        )

    def _monitor(
        self,
        candidate_id: str,
        drawdown_pct: float,
        daily_returns: list[float],
        expected_returns: list[float],
        failed_orders_total: int,
        total_orders: int,
    ) -> DeploymentMonitor:
        """Build the P4-001 stay monitor from the campaign-so-far metrics."""
        actual_cum_bps = sum(daily_returns) * 100.0
        expected_cum_bps = sum(expected_returns) * 100.0
        underperformance_bps = max(0.0, expected_cum_bps - actual_cum_bps)
        failed_pct = 0.0
        if total_orders > 0:
            failed_pct = failed_orders_total / total_orders * 100.0
        return DeploymentMonitor(
            candidate_id=candidate_id,
            environment=ModelEnvironment.PAPER,
            drawdown_pct=drawdown_pct,
            underperformance_bps=underperformance_bps,
            failed_orders_pct=failed_pct,
        )

    def _accumulate(
        self,
        initial_evidence: CandidateEvidence,
        days_run: int,
        daily_returns: list[float],
    ) -> CandidateEvidence:
        """Extend the candidate's evidence with paper days and realised Sharpe."""
        base_days = initial_evidence.paper_days_deployed or 0
        return CandidateEvidence(
            candidate_id=initial_evidence.candidate_id,
            validation_samples=initial_evidence.validation_samples,
            validation_sharpe=initial_evidence.validation_sharpe,
            paper_days_deployed=base_days + days_run,
            paper_sharpe=self._sharpe(daily_returns),
            canary_days_deployed=initial_evidence.canary_days_deployed,
        )

    @staticmethod
    def _demote(initial_evidence: CandidateEvidence) -> CandidateEvidence:
        """Reset paper deployment evidence after an automatic rollback.

        A rollback demotes the candidate one environment: its paper
        deployment window and sharpe no longer stand, so they are cleared.
        Validation evidence survives (it predates the failed paper window).
        """
        return CandidateEvidence(
            candidate_id=initial_evidence.candidate_id,
            validation_samples=initial_evidence.validation_samples,
            validation_sharpe=initial_evidence.validation_sharpe,
            paper_days_deployed=None,
            paper_sharpe=None,
            canary_days_deployed=None,
        )

    @staticmethod
    def _sharpe(daily_returns: Sequence[float]) -> float:
        """Annualised Sharpe from daily returns (0.0 on insufficient data)."""
        if len(daily_returns) < 2:
            return 0.0
        mean = sum(daily_returns) / len(daily_returns)
        variance = sum((r - mean) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        if variance <= 0.0:
            return 0.0
        std: float = math.sqrt(variance)
        if std <= 0.0:
            return 0.0
        annualised: float = mean / std * math.sqrt(_TRADING_DAYS_PER_YEAR)
        return round(annualised, 6)


def run_paper_campaign(
    candidate_id: str,
    initial_evidence: CandidateEvidence,
    day_fn: PaperDayFn,
    *,
    config: PaperCampaignConfig | None = None,
) -> PaperCampaignResult:
    """Module-level convenience: run one autonomous paper campaign."""
    return PaperAutonomyRunner(config).run(candidate_id, initial_evidence, day_fn)
