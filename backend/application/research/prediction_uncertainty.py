# backend/application/research/prediction_uncertainty.py
"""General uncertainty layer: per-signal uncertainty from calibration (T2-20-1).

Builds on the T2-19-1 calibration check: for a signal whose confidence is
``c``, the uncertainty is the calibration gap of the confidence bin ``c``
falls in — the measured deviation between claimed confidence and observed
hit rate at that operating point. The honest band is ``c ± gap`` clamped
to [0, 1].

Honesty rules
-------------
- An INSUFFICIENT calibration report yields no uncertainty claim (None).
- A confidence whose bin the report never exercised yields None: the
  range was never measured, so nothing can be claimed about it.
- Inputs are validated hard: confidence outside [0, 1] raises ValueError.
"""

from __future__ import annotations

from backend.domain.research.prediction_uncertainty import SignalUncertainty
from backend.domain.research.signal_calibration import CalibrationStatus, SignalCalibrationReport


def uncertainty_for_confidence(
    report: SignalCalibrationReport,
    confidence: float,
) -> SignalUncertainty | None:
    """Per-signal uncertainty for one confidence claim, from a calibration report.

    Returns None when the report is INSUFFICIENT or the confidence falls
    in an unexercised bin — never a fabricated claim.
    """
    if not 0.0 <= confidence <= 1.0:
        raise ValueError(f"confidence out of range: {confidence!r}")
    if report.status is CalibrationStatus.INSUFFICIENT:
        return None

    index = min(int(confidence * report.n_bins), report.n_bins - 1)
    for bin_ in report.bins:
        if bin_.index == index:
            uncertainty = bin_.gap
            return SignalUncertainty(
                confidence=confidence,
                uncertainty=uncertainty,
                band_lower=max(0.0, confidence - uncertainty),
                band_upper=min(1.0, confidence + uncertainty),
                bin_index=bin_.index,
                bin_n=bin_.n,
            )
    return None


__all__ = ["uncertainty_for_confidence"]
