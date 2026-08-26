# backend/domain/research/signal_calibration.py
"""Signal calibration contracts (task T2-19-1).

A proposal's ``confidence`` is a probability claim: "this signal is right
with probability p". A probability claim is only worth its calibration —
does a signal rated 80% actually resolve correctly 80% of the time? This
module is the contract for measuring that claim against realized OOS
outcomes (Brier score + Expected Calibration Error over confidence bins).

Honesty invariants
------------------
- **No verdict from nothing.** Below ``min_samples`` the report is
  ``INSUFFICIENT`` with a reason: a calibration claim over a handful of
  signals would be fabricated.
- **Bins are the audit trail.** ECE is computed bin by bin; every bin
  records its sample count, mean confidence and mean outcome, so the
  aggregate never hides where the miscalibration lives.
- **Outcomes are binary and explicit.** An outcome is 1.0 when the signal
  resolved correctly (direction right / target hit), 0.0 otherwise. What
  "correct" means is the caller's definition, recorded at the seam, not
  re-decided here.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class CalibrationStatus(enum.StrEnum):
    """Overall judgement of one calibration report.

    CALIBRATED: ECE within tolerance; the confidence claims hold.
    MISCALIBRATED: ECE exceeds tolerance; confidence claims over- or
        understate hit rates and should not be taken at face value.
    INSUFFICIENT: too few samples to judge anything.
    """

    CALIBRATED = "calibrated"
    MISCALIBRATED = "miscalibrated"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True, slots=True)
class CalibrationBin:
    """One ECE bin: confidence interval, sample count, mean confidence/outcome.

    Attributes
    ----------
    index: int
        Zero-based bin index (deciles when 10 bins).
    lower, upper: float
        Confidence interval of the bin, half-open [lower, upper) except
        the last bin, which is closed.
    n: int
        Samples in this bin.
    mean_confidence: float
        Mean of the confidences in the bin.
    mean_outcome: float
        Mean of the realized outcomes in the bin (the observed hit rate).
    """

    index: int
    lower: float
    upper: float
    n: int
    mean_confidence: float
    mean_outcome: float

    @property
    def gap(self) -> float:
        """|mean_confidence - mean_outcome|, this bin's calibration error."""
        return abs(self.mean_confidence - self.mean_outcome)

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "lower": round(self.lower, 6),
            "upper": round(self.upper, 6),
            "n": self.n,
            "mean_confidence": round(self.mean_confidence, 6),
            "mean_outcome": round(self.mean_outcome, 6),
            "gap": round(self.gap, 6),
        }


@dataclass(frozen=True, slots=True)
class SignalCalibrationReport:
    """Brier/ECE measurement of one confidence stream against its outcomes.

    Attributes
    ----------
    n: int
        Number of (confidence, outcome) pairs measured.
    n_bins: int
        The bin count the report was measured over (the report records
        its own binning so consumers never guess it).
    brier: float
        Mean squared error of the confidence predictions (lower is better;
        0 = perfect).
    ece: float
        Expected Calibration Error: sample-weighted mean absolute gap
        between mean confidence and observed hit rate per bin.
    ece_tolerance: float
        The tolerance the status judgement used.
    status: CalibrationStatus
        Overall judgement (INSUFFICIENT below ``min_samples``).
    reason: str
        Why the status is what it is (empty when CALIBRATED).
    bins: tuple[CalibrationBin, ...]
        The per-bin audit trail (empty when INSUFFICIENT).
    """

    n: int
    n_bins: int
    brier: float
    ece: float
    ece_tolerance: float
    status: CalibrationStatus
    reason: str
    bins: tuple[CalibrationBin, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "n_bins": self.n_bins,
            "brier": round(self.brier, 6),
            "ece": round(self.ece, 6),
            "ece_tolerance": self.ece_tolerance,
            "status": self.status.value,
            "reason": self.reason,
            "bins": [bin_.as_dict() for bin_ in self.bins],
        }


__all__ = [
    "CalibrationBin",
    "CalibrationStatus",
    "SignalCalibrationReport",
]
