# backend/application/research/edge_monitor.py
"""Strategy edge-monitoring service (task T2-15-1).

Wraps one ADWIN detector per passport and converts "distributional change"
into an actionable verdict on the strategy's *edge*:

- ``INSUFFICIENT`` until enough observations exist to judge anything;
- ``HEALTHY`` while the surviving window's mean return is at/above the
  decay threshold and no cut fired;
- ``WATCHING`` while the mean is thinning below threshold (edge fading) or
  a cut fired but the surviving window still pays (regime change, edge
  intact);
- ``DECAYED`` when a cut fired and the surviving window's mean is below the
  decay threshold. ``DECAYED`` is a *state*, not an event: it persists while
  the window stays below threshold and clears only when the window pays
  again (mean at/above threshold).

The demotion trigger is *advisory and library-only*: ``demotion_trigger``
maps the passport's environment down one promotion-chain step and records
the recommendation; nothing in this module (or the live path) applies it.
The death system (Tier-3) decides whether a DECAYED verdict actually
grounds a strategy.
"""

from __future__ import annotations

import logging

from backend.application.validation.adwin import AdwinDetector
from backend.domain.research.edge_monitor import (
    EdgeDemotionTrigger,
    EdgeMonitorConfig,
    EdgeMonitorState,
    EdgeVerdict,
)
from backend.domain.research.promotion import ModelEnvironment, previous_environment

logger = logging.getLogger(__name__)

# Mapping from the operator's current passport statuses onto the promotion
# chain, so a decay trigger can name a concrete demotion target. Advisory
# only — nothing consumes this automatically (library-only until Tier-3).
_STATUS_TO_ENVIRONMENT: dict[str, ModelEnvironment] = {
    "research": ModelEnvironment.RESEARCH,
    "candidate": ModelEnvironment.VALIDATION,
    "paper": ModelEnvironment.PAPER,
    "canary": ModelEnvironment.CANARY,
    "live": ModelEnvironment.PRODUCTION,
}


def environment_for_status(status: str | None) -> ModelEnvironment | None:
    """Map a passport status id onto the promotion chain (advisory)."""
    if status is None:
        return None
    return _STATUS_TO_ENVIRONMENT.get(status.lower())


_ENVIRONMENT_TO_STATUS: dict[ModelEnvironment, str] = {
    env: status for status, env in _STATUS_TO_ENVIRONMENT.items()
}


def status_for_environment(environment: ModelEnvironment) -> str | None:
    """Map a promotion-chain environment back onto the passport status id.

    Single source of truth with :func:`environment_for_status`: the death
    system (T3-26-1) uses this to turn a demotion target environment into
    the passport status to transition to. The mapping is a bijection — live
    allocations sit on the PRODUCTION rung of the chain.
    """
    return _ENVIRONMENT_TO_STATUS.get(environment)


class EdgeMonitorService:
    """One ADWIN edge monitor per passport, with advisory demotion triggers.

    The service is stateful by design: it owns the detectors and their
    rolling windows. Feed it one return observation per period (OOS fold
    returns, paper-day returns, ...) via ``record`` and read the verdict
    from the returned state. The verdict ladder never fabricates evidence:
    below ``min_observations`` everything is ``INSUFFICIENT``.
    """

    def __init__(self, config: EdgeMonitorConfig | None = None) -> None:
        self._config = config or EdgeMonitorConfig()
        self._detectors: dict[str, AdwinDetector] = {}
        self._observations: dict[str, int] = {}
        self._last_cut_at: dict[str, int | None] = {}
        self._last_decayed_at: dict[str, int | None] = {}
        self._last_triggered_at: dict[str, int | None] = {}

    def record(self, passport_id: str, return_pct: float) -> EdgeMonitorState:
        """Feed one period return for a passport and return the new state.

        The observation counter is monotonic per passport: the first call is
        observation 1, so ``last_cut_at`` / ``last_decayed_at`` are 1-based
        indices a caller can correlate with its own stream.
        """
        if not isinstance(return_pct, (int, float)):
            raise TypeError("return_pct must be a real number")
        detector = self._detectors.get(passport_id)
        if detector is None:
            detector = AdwinDetector(self._config.adwin)
            self._detectors[passport_id] = detector
            self._observations[passport_id] = 0
            self._last_cut_at[passport_id] = None
            self._last_decayed_at[passport_id] = None
            self._last_triggered_at[passport_id] = None

        detector.record(return_pct)
        self._observations[passport_id] += 1
        adwin = detector.state()

        if adwin.drifted:
            self._last_cut_at[passport_id] = self._observations[passport_id]

        verdict = self._verdict(passport_id, adwin.drifted, adwin.mean)
        if verdict is EdgeVerdict.DECAYED:
            previous = self._last_decayed_at[passport_id]
            if previous is None or adwin.drifted:
                # New decay event: first declaration, or fresh ADWIN-cut
                # evidence that the edge is still gone.
                self._last_decayed_at[passport_id] = self._observations[passport_id]

        return self._state_for(passport_id, adwin.drifted, adwin.mean, adwin.variance, verdict)

    def _verdict(self, passport_id: str, drifted: bool, window_mean: float) -> EdgeVerdict:
        """Classify the latest observation into the verdict ladder.

        DECAYED is a state, not an event: once the edge stopped paying, the
        monitor stays DECAYED until the surviving window pays again. A
        recovery only happens when the window mean is back at/above the
        decay threshold.
        """
        if self._observations[passport_id] < self._config.min_observations:
            return EdgeVerdict.INSUFFICIENT

        edge_paying = window_mean >= self._config.decay_mean_pct
        if drifted:
            if edge_paying:
                return EdgeVerdict.WATCHING  # change detected, edge intact
            return EdgeVerdict.DECAYED
        if edge_paying:
            return EdgeVerdict.HEALTHY
        if self._last_decayed_at[passport_id] is not None:
            return EdgeVerdict.DECAYED  # still below threshold, not recovered
        return EdgeVerdict.WATCHING  # thinning, no cut yet

    def state(self, passport_id: str) -> EdgeMonitorState | None:
        """Current state for a passport, or None if it was never recorded."""
        detector = self._detectors.get(passport_id)
        if detector is None:
            return None
        adwin = detector.state()
        return self._state_for(
            passport_id,
            adwin.drifted,
            adwin.mean,
            adwin.variance,
            self._verdict(passport_id, adwin.drifted, adwin.mean),
        )

    def _state_for(
        self,
        passport_id: str,
        drifted: bool,
        mean: float,
        variance: float,
        verdict: EdgeVerdict,
    ) -> EdgeMonitorState:
        adwin = self._detectors[passport_id].state()
        return EdgeMonitorState(
            passport_id=passport_id,
            observations=self._observations[passport_id],
            window_size=adwin.window_size,
            mean=mean,
            variance=variance,
            cuts=adwin.cuts,
            drifted=drifted,
            verdict=verdict,
            last_cut_at=self._last_cut_at[passport_id],
            last_decayed_at=self._last_decayed_at[passport_id],
        )

    def demotion_trigger(self, passport_id: str, status: str | None = None) -> EdgeDemotionTrigger:
        """Advisory demotion recommendation for a passport (library-only).

        A trigger fires only when the latest verdict is DECAYED. With a
        non-zero ``cooldown_observations``, the advisory re-fires at most
        once per cooldown window (measured in observations since the last
        advisory), so a persistent decay is re-flagged periodically without
        nagging on every observation. The recommended environment is one
        step down the promotion chain from the passport's mapped
        environment; unknown/unmapped statuses yield no recommendation (the
        Tier-3 death system decides then).
        """
        state = self.state(passport_id)
        if state is None or state.verdict is not EdgeVerdict.DECAYED:
            return EdgeDemotionTrigger(
                passport_id=passport_id,
                triggered=False,
                reason="no decay verdict",
                recommended_environment=None,
            )

        last_triggered = self._last_triggered_at[passport_id]
        if (
            last_triggered is not None
            and state.observations < last_triggered + self._config.cooldown_observations
        ):
            return EdgeDemotionTrigger(
                passport_id=passport_id,
                triggered=False,
                reason=f"decay advisory in cooldown until observation "
                f"{last_triggered + self._config.cooldown_observations}",
                recommended_environment=None,
            )

        self._last_triggered_at[passport_id] = state.observations
        environment = environment_for_status(status)
        target = previous_environment(environment) if environment else None
        reason = (
            f"ADWIN cut at observation {state.last_cut_at} left window mean "
            f"{state.mean:.4f}% below decay threshold {self._config.decay_mean_pct:.4f}%"
        )
        logger.warning(
            "Edge decay advisory for %s: %s (recommended: %s)",
            passport_id,
            reason,
            target.value if target else "none",
        )
        return EdgeDemotionTrigger(
            passport_id=passport_id,
            triggered=True,
            reason=reason,
            recommended_environment=target.value if target else None,
        )

    def reset(self, passport_id: str) -> None:
        """Forget a passport's monitor entirely (fresh start)."""
        self._detectors.pop(passport_id, None)
        self._observations.pop(passport_id, None)
        self._last_cut_at.pop(passport_id, None)
        self._last_decayed_at.pop(passport_id, None)
        self._last_triggered_at.pop(passport_id, None)


def build_edge_monitor_service(config: EdgeMonitorConfig | None = None) -> EdgeMonitorService:
    """Bootstrap seam: construct an edge-monitoring service."""
    return EdgeMonitorService(config)
