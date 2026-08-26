# backend/application/validation/adwin.py
"""Adaptive Windowing (ADWIN) drift detection (integration #27 / #19).

ADWIN (Bifet & Gavalda 2007) keeps a variable-length window of recent
observations and automatically shrinks it whenever the mean in the oldest part
differs significantly from the mean in the newest part — a distributional
*change* in the stream. It requires no distributional assumption, no batch
re-training, and feeds two pipeline signals:

1. ``drifted``: the most recent observation triggered a cut; models built on
   the old regime should be retired or retrained.
2. ``window_size``: how many observations the detector currently trusts; a
   shrinking window is the earliest practical drift alarm.

Cut criterion (ADWIN0, Bifet & Gavalda 2007): a window of size ``n`` is split
into an oldest block of size ``m`` and a newest block of size ``n - m``; the
older block is dropped when

    |mean(old) - mean(new)| > eps

with

    eps = sqrt( 2*var*ln(2/delta) / m  +  2*range*ln(2/delta) / (3*m) )

using the *smaller* block size ``m`` for the bound (the conservative choice
used by reference implementations such as River). Memory is bounded: when the
window exceeds ``max_window``, the oldest half is dropped (a documented
truncation, not a drift signal). This module is stdlib-only.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from backend.domain.validation.adwin_config import AdwinConfig as DomainAdwinConfig


def _cut_epsilon(variance: float, delta_range: float, block_size: int, delta: float) -> float:
    """Threshold a mean difference must exceed to justify a cut."""
    if block_size <= 0:
        return math.inf
    ln_term = math.log(2.0 / delta)
    return math.sqrt(
        (2.0 * variance * ln_term) / block_size + (2.0 * delta_range * ln_term) / (3.0 * block_size)
    )


class AdwinConfig(DomainAdwinConfig):
    """Application alias of domain AdwinConfig (re-export for compatibility)."""

    pass


@dataclass(frozen=True, slots=True)
class AdwinState:
    """Drift-detector output for one stream."""

    drifted: bool
    observations: int
    window_size: int
    mean: float
    variance: float
    cuts: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "drifted": self.drifted,
            "observations": self.observations,
            "window_size": self.window_size,
            "mean": round(self.mean, 8),
            "variance": round(self.variance, 8),
            "cuts": self.cuts,
        }


class AdwinDetector:
    """Online ADWIN drift detector over a single value stream."""

    def __init__(self, config: DomainAdwinConfig | None = None) -> None:
        self._config: DomainAdwinConfig = config or AdwinConfig()
        self._window: deque[float] = deque()
        self._total = 0.0
        self._total_sq = 0.0
        self._cuts = 0
        self._seen = 0
        self._last_drifted = False

    def record(self, value: float) -> None:
        """Feed one observation and run the cut criterion."""
        self._last_drifted = False
        if not (math.isnan(value) or math.isinf(value)):
            self._window.append(value)
            self._total += value
            self._total_sq += value * value
            self._seen += 1
            self._enforce_memory_bound()
            self._last_drifted = self._detect_and_cut()

    def _enforce_memory_bound(self) -> None:
        """Bound memory by dropping the oldest half (documented truncation)."""
        while len(self._window) > self._config.max_window:
            keep = len(self._window) // 2
            for _ in range(len(self._window) - keep):
                dropped = self._window.popleft()
                self._total -= dropped
                self._total_sq -= dropped * dropped

    def _detect_and_cut(self) -> bool:
        """Cut the window if a split tests significant; return True on drift."""
        n = len(self._window)
        if n < 2 * self._config.min_window:
            return False
        variance = max(0.0, (self._total_sq - self._total * self._total / n) / n)
        delta_range = max(self._window) - min(self._window)

        # Scan split positions from the smallest old block upward; cut as
        # little as needed. The newest block must keep at least min_window.
        running = 0.0
        for m in range(1, n - self._config.min_window + 1):
            running += self._window[m - 1]
            old_mean = running / m
            new_mean = (self._total - running) / (n - m)
            if abs(old_mean - new_mean) <= _cut_epsilon(
                variance, delta_range, min(m, n - m), self._config.delta
            ):
                continue
            self._cut(m)
            return True
        return False

    def _cut(self, m: int) -> None:
        """Drop the oldest ``m`` observations and count the cut."""
        for _ in range(m):
            dropped = self._window.popleft()
            self._total -= dropped
            self._total_sq -= dropped * dropped
        self._cuts += 1

    def state(self) -> AdwinState:
        """Current detector state (drift flag, window, mean, variance)."""
        n = len(self._window)
        mean = self._total / n if n else 0.0
        variance = max(0.0, (self._total_sq - self._total * self._total / n) / n) if n > 1 else 0.0
        return AdwinState(
            drifted=self._last_drifted,
            observations=self._seen,
            window_size=n,
            mean=mean,
            variance=variance,
            cuts=self._cuts,
        )

    def reset(self) -> None:
        """Clear all state and re-learn the stream from scratch."""
        self._window.clear()
        self._total = 0.0
        self._total_sq = 0.0
        self._cuts = 0
        self._seen = 0
        self._last_drifted = False
