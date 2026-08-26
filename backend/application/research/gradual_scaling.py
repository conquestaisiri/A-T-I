# backend/application/research/gradual_scaling.py
"""Gradual post-canary capital-scaling harness (build-order #40).

Once a candidate has earned production through the P4-001 ladder (and the
canary, P4-003), it is still not granted full allocation at once. It ramps:
exposure starts at ``floor_fraction`` of the operator-bounded maximum and
climbs by ``step_fraction`` after every ``hold_periods`` clean periods, until
it reaches ``max_fraction``. All of this happens under the same P4-001 stay
discipline that governed paper and canary: a breach cuts exposure back to the
floor and ends the ramp.

Design rules
------------
- **Operator owns the ceiling, the harness owns the path.** ``max_fraction``
  is a hard upper bound never exceeded; the harness only decides whether each
  period earns an increment.
- **Earning an increment is a clean-stay decision.** No breach during the
  current tier's holding period -> ADVANCE after ``hold_periods`` clean
  periods; a breach -> CUT with the exposure snapped to the floor.
- **Exposure never exceeds max_fraction.** Reaching the ceiling is a separate
  terminal state (``CAPPED``), distinct from an early exit (``RAMPING``).
- **Monitors are injected.** The harness itself never touches a venue; the
  caller's ``monitor_fn(period)`` returns the P4-001 ``DeploymentMonitor``
  for that scaling period (live or paper, per the operator's wiring).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from backend.application.research.promotion_engine import PromotionEngine
from backend.domain.research.promotion import DeploymentMonitor, PromotionConfig
from backend.domain.research.scaling import (
    ScaleTier,
    ScalingAction,
    ScalingBoundary,
    ScalingProgramResult,
)

logger = logging.getLogger(__name__)

# monitor_fn(period) -> the P4-001 monitor for that scaling period.
ScaleMonitorFn = Callable[[int], DeploymentMonitor]


@dataclass(frozen=True, slots=True)
class ScalingConfig:
    """Bounds and rate of one gradual-scaling ramp.

    Attributes
    ----------
    max_fraction:
        Operator-bounded ceiling: the fraction of the maximum allocation the
        ramp may ever reach (hard cap, never exceeded).
    floor_fraction:
        Exposure at which the ramp starts and to which a breach cuts back.
    step_fraction:
        How much exposure grows per earned increment.
    hold_periods:
        How many clean periods must pass before one increment is granted.
    promotion_config:
        The P4-001 stay limits applied at every tier.
    """

    max_fraction: float = 0.5
    floor_fraction: float = 0.05
    step_fraction: float = 0.05
    hold_periods: int = 3
    promotion_config: PromotionConfig | None = None


class GradualScalingRunner:
    """Run one gradual-scaling program for a production-enabled candidate."""

    def __init__(self, config: ScalingConfig | None = None) -> None:
        self._config = config or ScalingConfig()
        self._promotion = PromotionEngine(self._config.promotion_config)

    def run(
        self,
        candidate_id: str,
        monitor_fn: ScaleMonitorFn,
        *,
        max_periods: int = 365,
    ) -> ScalingProgramResult:
        """Run the ramp until capped, cut, or ``max_periods`` are reached.

        Parameters
        ----------
        candidate_id:
            The candidate being ramped into live exposure.
        monitor_fn:
            ``monitor_fn(period)`` returns the period's DeploymentMonitor.
        max_periods:
            Safety bound on the number of periods; prevents an unbounded
            loop on wiring mistakes.
        """
        if max_periods <= 0:
            max_periods = 1
        fraction = self._config.floor_fraction
        clean_streak = 0
        tiers: list[ScaleTier] = []
        last_action = ScalingAction.HOLD
        last_reason = "program started at floor exposure"

        for period in range(1, max_periods + 1):
            monitor = monitor_fn(period)
            rollback = self._promotion.rollback_required(monitor)
            if rollback.rollback:
                reason = "; ".join(rollback.reasons)
                tiers.append(
                    ScaleTier(
                        tier=period,
                        capital_fraction=self._config.floor_fraction,
                        action=ScalingAction.CUT,
                        reason=reason,
                    )
                )
                logger.warning(
                    "Scaling for %s cut to floor on period %s: %s",
                    candidate_id,
                    period,
                    reason,
                )
                return ScalingProgramResult(
                    candidate_id=candidate_id,
                    boundary=ScalingBoundary.CUT,
                    action=ScalingAction.CUT,
                    current_fraction=self._config.floor_fraction,
                    tiers=tuple(tiers),
                    reason=reason,
                )

            clean_streak += 1
            if fraction >= self._config.max_fraction:
                # At the ceiling: nothing more to grant, terminal and clean.
                tiers.append(
                    ScaleTier(
                        tier=period,
                        capital_fraction=fraction,
                        action=ScalingAction.CAPPED,
                        reason="reached the operator-bounded ceiling",
                    )
                )
                return ScalingProgramResult(
                    candidate_id=candidate_id,
                    boundary=ScalingBoundary.CAPPED,
                    action=ScalingAction.CAPPED,
                    current_fraction=fraction,
                    tiers=tuple(tiers),
                    reason="gradual scaling capped at max_fraction",
                )

            if clean_streak >= self._config.hold_periods:
                fraction = round(
                    min(fraction + self._config.step_fraction, self._config.max_fraction), 6
                )
                clean_streak = 0
                tiers.append(
                    ScaleTier(
                        tier=period,
                        capital_fraction=fraction,
                        action=ScalingAction.ADVANCE,
                        reason=f"clean {self._config.hold_periods}-period stay at the floor; "
                        f"exposure advanced to {fraction:.4f}",
                    )
                )
                last_action = ScalingAction.ADVANCE
                last_reason = f"earned tier {fraction:.4f}"
            else:
                tiers.append(
                    ScaleTier(
                        tier=period,
                        capital_fraction=fraction,
                        action=ScalingAction.HOLD,
                        reason=f"holding {clean_streak}/{self._config.hold_periods} clean periods",
                    )
                )
                last_action = ScalingAction.HOLD
                last_reason = "clean stay, increment not yet due"

        # Loop exited on max_periods before capping or cutting.
        return ScalingProgramResult(
            candidate_id=candidate_id,
            boundary=ScalingBoundary.RAMPING,
            action=last_action,
            current_fraction=fraction,
            tiers=tuple(tiers),
            reason=f"still ramping after {max_periods} periods: {last_reason}",
        )


def run_gradual_scaling(
    candidate_id: str,
    monitor_fn: ScaleMonitorFn,
    *,
    config: ScalingConfig | None = None,
    max_periods: int = 365,
) -> ScalingProgramResult:
    """Module-level convenience: run one gradual-scaling program."""
    return GradualScalingRunner(config).run(candidate_id, monitor_fn, max_periods=max_periods)
