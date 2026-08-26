# backend/domain/research/prediction_uncertainty.py
"""Per-signal prediction uncertainty contracts (task T2-20-1).

A confidence is a point claim; the uncertainty layer turns measured
calibration (T2-19-1) into a *per-signal* statement: at this signal's
confidence, how far can the claim be off, and what is the honest band
around it?

``SignalUncertainty`` is only produced from a measured calibration
report. No report (or a confidence range the report never exercised)
means no uncertainty claim — uncertainty from nothing would be
fabricated, and fabrication is the one thing this layer must never do.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class SignalUncertainty:
    """Calibration-derived uncertainty for one signal's confidence.

    Attributes
    ----------
    confidence: float
        The signal's claimed confidence in [0, 1].
    uncertainty: float
        The calibration gap at this operating point: how far the claimed
        confidence deviates from the measured hit rate, in probability
        units. 0 = perfectly calibrated here.
    band_lower, band_upper: float
        The honest band around the claim, ``confidence ± uncertainty``
        clamped to [0, 1].
    bin_index: int
        The confidence bin of the calibration report this came from.
    bin_n: int
        Samples the bin's gap is measured over (small bin n = weak claim).
    """

    confidence: float
    uncertainty: float
    band_lower: float
    band_upper: float
    bin_index: int
    bin_n: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "confidence": round(self.confidence, 6),
            "uncertainty": round(self.uncertainty, 6),
            "band_lower": round(self.band_lower, 6),
            "band_upper": round(self.band_upper, 6),
            "bin_index": self.bin_index,
            "bin_n": self.bin_n,
        }


__all__ = ["SignalUncertainty"]
