# backend/application/risk/vpin.py
"""Volume-synchronized Probability of Informed Trading (VPIN).

VPIN segments trades into fixed-volume buckets and estimates the probability
that the flow imbalance within a bucket is informed (Easley-Lopez de Prado-
O'Hara). It is used here as a *risk* signal, not alpha (integration #13):
when toxicity enters the top quartile of its recent distribution, the risk
gate stands the strategy aside instead of trading into a toxic book.

Implementation is deliberately self-contained (stdlib + statistics only) so
the estimator stays deterministic and dependency-free: the research stream
explicitly says to build VPIN, not to trust a vendor implementation.
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class VpinConfig:
    """Configuration for the VPIN estimator.

    Attributes
    ----------
    bucket_volume: float
        The fixed volume each bucket accumulates before its imbalance is
        recorded (in base units of the feed, e.g. contracts/shares).
    history_size: int
        How many of the most recent complete buckets form the short "now"
        window whose mean is the reported VPIN.
    reference_size: int | None
        How many complete buckets form the longer reference window used as
        the calm baseline. Defaults to ``3 * history_size`` (but never below
        16 buckets).
    severity_floor: float
        Absolute minimum VPIN needed to call the book toxic, even when the
        reference baseline is itself high. Prevents a uniformly toxic regime
        from being read as "normal".
    """

    bucket_volume: float = 1000.0
    history_size: int = 60
    reference_size: int | None = None
    severity_floor: float = 0.6

    @property
    def effective_reference_size(self) -> int:
        """Resolved reference window size (explicit or derived)."""
        if self.reference_size is not None:
            return self.reference_size
        return max(3 * self.history_size, 16)

    def __post_init__(self) -> None:
        if self.bucket_volume <= 0.0:
            raise ValueError("bucket_volume must be positive")
        if self.history_size < 1:
            raise ValueError("history_size must be >= 1")
        if self.reference_size is not None and self.reference_size < 1:
            raise ValueError("reference_size must be >= 1")
        if not 0.0 <= self.severity_floor <= 1.0:
            raise ValueError("severity_floor must be within [0, 1]")


@dataclass(frozen=True, slots=True)
class VpinState:
    """Current VPIN output for one symbol."""

    vpin: float
    toxicity_quartile: float | None
    toxic: bool
    buckets: int
    current_bucket_volume: float

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for reports."""
        return {
            "vpin": round(self.vpin, 6),
            "toxicity_quartile": (
                round(self.toxicity_quartile, 4) if self.toxicity_quartile is not None else None
            ),
            "toxic": self.toxic,
            "buckets": self.buckets,
            "current_bucket_volume": round(self.current_bucket_volume, 6),
        }


class VpinTracker:
    """Online VPIN estimator for a single symbol.

    Feed each unit of executed volume with its aggressor side via
    :meth:`record` (signed flow). Positive flow advances the buy-side volume
    of the current bucket, negative flow the sell side; a bucket flips when
    its accumulated volume reaches ``bucket_volume`` and the imbalance
    ``abs(buy - sell) / bucket_volume`` is recorded as that bucket's VPIN
    sample. The estimator is adaptive: ``vpin`` is the rolling mean over the
    retained bucket history.
    """

    def __init__(self, config: VpinConfig | None = None) -> None:
        self._config = config or VpinConfig()
        self._bucket: deque[float] = deque(maxlen=self._config.history_size)
        self._reference: deque[float] = deque(maxlen=self._config.effective_reference_size)
        self._buy = 0.0
        self._sell = 0.0
        self._observed = 0.0

    def record(self, signed_flow: float) -> None:
        """Record one unit of signed flow (positive = buyer-initiated)."""
        if signed_flow > 0.0:
            self._buy += signed_flow
        else:
            self._sell += -signed_flow
        self._observed += abs(signed_flow)
        self._roll_bucket()

    def _roll_bucket(self) -> None:
        """Close complete fixed-volume buckets as volume accumulates.

        A bucket takes exactly ``bucket_volume`` of the running buy+sell mix,
        scaled proportionally to the observed buy/sell split, so a partial
        bucket at a split boundary stays unbiased. Excess volume carries into
        the next bucket.
        """
        bucket_volume = self._config.bucket_volume
        while (self._buy + self._sell) >= bucket_volume:
            total = self._buy + self._sell
            buy_take = self._buy * (bucket_volume / total)
            sell_take = bucket_volume - buy_take

            imbalance = abs(buy_take - sell_take) / bucket_volume
            self._bucket.append(imbalance)
            self._reference.append(imbalance)

            self._buy -= buy_take
            self._sell -= sell_take
            self._observed -= bucket_volume

    def state(self) -> VpinState:
        """Current VPIN and toxicity judgement for the symbol.

        Toxicity compares the short-window VPIN against the longer reference
        baseline; a book is toxic when the short mean breaches the baseline
        (or the absolute severity floor, whichever binds). The reported
        ``toxicity_quartile`` is the short VPIN expressed relative to that
        threshold, so a value above ``1.0`` means the book is more toxic than
        the recent norm.
        """
        if not self._bucket:
            return VpinState(
                vpin=0.0,
                toxicity_quartile=None,
                toxic=False,
                buckets=0,
                current_bucket_volume=self._observed,
            )
        vpin = statistics.fmean(self._bucket)
        baseline = statistics.fmean(self._reference) if self._reference else 0.0
        threshold = max(baseline, self._config.severity_floor)
        toxic = len(self._bucket) >= 4 and vpin >= threshold
        quartile = vpin / threshold if threshold > 0.0 else None
        if len(self._bucket) < 4:
            quartile = None
        return VpinState(
            vpin=vpin,
            toxicity_quartile=quartile,
            toxic=toxic,
            buckets=len(self._bucket),
            current_bucket_volume=self._observed,
        )
