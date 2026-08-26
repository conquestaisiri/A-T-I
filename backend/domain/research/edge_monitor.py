# backend/domain/research/edge_monitor.py
"""Strategy edge-monitoring contracts (task T2-15-1).

A strategy's *edge* is the positive net excess it earns over its costed
baseline. This module is the per-passport monitoring contract: a rolling
return stream (OOS fold returns, paper-day returns, later canary/live
returns) is fed to an ADWIN drift detector, and the monitor converts
"change detected" into a verdict an operator (and later the death system)
can act on.

Honesty invariants
------------------
- **Drift is not decay.** ADWIN fires on *any* distributional change; the
  monitor only reports ``DECAYED`` when a cut left the surviving window's
  mean below the decay threshold (default 0.0 — the edge stopped being
  positive). A cut that lands on a still-positive level is ``WATCHING``
  (change, edge intact), never a death sentence.
- **No verdict from nothing.** Below ``min_observations`` the monitor is
  ``INSUFFICIENT``: a two-point window proves nothing and is never
  reported as healthy.
- **Decay is a state, not an event.** ``DECAYED`` persists while the
  surviving window's mean stays below the decay threshold; only a recovery
  (mean back at/above threshold) returns the monitor to ``HEALTHY``.
  ``last_decayed_at`` records the last *event* (first declaration or fresh
  cut while below threshold), which is also what re-arms demotion
  advisories.
- **Triggers are advisory.** ``EdgeDemotionTrigger`` *recommends* a demotion
  (one environment down the promotion chain); it is library-only and never
  applies itself. The death system (Tier-3) decides whether to act.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any

from backend.domain.validation.adwin_config import AdwinConfig

_DEFAULT_MIN_OBSERVATIONS = 30


class EdgeVerdict(enum.StrEnum):
    """The monitor's judgement on the latest observation.

    INSUFFICIENT: too few observations to judge anything.
    HEALTHY: the surviving window's mean is at/above the decay threshold
        and no cut fired on this observation.
    WATCHING: either the window mean fell below the decay threshold without
        a cut (edge thinning), or a cut fired but the surviving window is
        still at/above threshold (change, edge intact).
    DECAYED: a cut fired AND the surviving window's mean is below the decay
        threshold — the edge, as observed, stopped paying.
    """

    INSUFFICIENT = "insufficient"
    HEALTHY = "healthy"
    WATCHING = "watching"
    DECAYED = "decayed"


@dataclass(frozen=True, slots=True)
class EdgeMonitorConfig:
    """Per-monitor hyper-parameters.

    Attributes
    ----------
    adwin: AdwinConfig
        Underlying drift detector settings (delta, memory bounds).
    min_observations: int
        Minimum observations before any verdict beyond INSUFFICIENT.
    decay_mean_pct: float
        Surviving-window mean below this is judged decayed (default 0.0:
        the edge stopped being positive, in the same frame as the verdict
        gates' excess-return language).
    cooldown_observations: int
        After a DECAYED advisory fires, suppress further demotion
        advisories until this many new observations have been recorded
        (prevents a single noisy cut from hammering the log). Does not
        affect the verdict itself: DECAYED persists while the window stays
        below threshold.
    """

    adwin: AdwinConfig = AdwinConfig()
    min_observations: int = _DEFAULT_MIN_OBSERVATIONS
    decay_mean_pct: float = 0.0
    cooldown_observations: int = 0

    def __post_init__(self) -> None:
        if self.min_observations < 1:
            raise ValueError("min_observations must be >= 1")
        if self.cooldown_observations < 0:
            raise ValueError("cooldown_observations must be >= 0")


@dataclass(frozen=True, slots=True)
class EdgeMonitorState:
    """Serialisable snapshot of one passport's monitor.

    Attributes
    ----------
    passport_id: str
        Which passport this stream belongs to.
    observations: int
        Total observations recorded (monotonic).
    window_size: int
        Observations the ADWIN detector currently trusts.
    mean: float
        Surviving-window mean return (percent), the edge estimate.
    variance: float
        Surviving-window variance.
    cuts: int
        Cumulative ADWIN cuts on this stream.
    drifted: bool
        Whether the latest observation triggered a cut.
    verdict: EdgeVerdict
        The monitor's judgement on the latest observation.
    last_cut_at: int | None
        Observation index of the last cut (1-based).
    last_decayed_at: int | None
        Observation index of the last DECAYED verdict (1-based).
    """

    passport_id: str
    observations: int
    window_size: int
    mean: float
    variance: float
    cuts: int
    drifted: bool
    verdict: EdgeVerdict
    last_cut_at: int | None = None
    last_decayed_at: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the state to a plain dictionary."""
        return {
            "passport_id": self.passport_id,
            "observations": self.observations,
            "window_size": self.window_size,
            "mean": round(self.mean, 8),
            "variance": round(self.variance, 8),
            "cuts": self.cuts,
            "drifted": self.drifted,
            "verdict": self.verdict.value,
            "last_cut_at": self.last_cut_at,
            "last_decayed_at": self.last_decayed_at,
        }


@dataclass(frozen=True, slots=True)
class EdgeDemotionTrigger:
    """Advisory demotion recommendation from a DECAYED verdict.

    Library-only by design: this record recommends; it never demotes.
    ``recommended_environment`` is the promotion chain's previous
    environment (None when the passport cannot be mapped or is already at
    the chain's bottom — grounding stays a Tier-3 decision).
    """

    passport_id: str
    triggered: bool
    reason: str
    recommended_environment: str | None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the trigger to a plain dictionary."""
        return {
            "passport_id": self.passport_id,
            "triggered": self.triggered,
            "reason": self.reason,
            "recommended_environment": self.recommended_environment,
        }


__all__ = [
    "EdgeDemotionTrigger",
    "EdgeMonitorConfig",
    "EdgeMonitorState",
    "EdgeVerdict",
]
