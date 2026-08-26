# backend/application/research/signal_calibration.py
"""Calibrated signal-quality scoring: Brier/ECE on OOS fold outcomes (T2-19-1).

A proposal's confidence is a probability claim. This module measures that
claim: given the confidences a signal stream emitted and the binary
outcomes of those signals (1.0 = resolved correctly), it returns a
``SignalCalibrationReport`` with the Brier score and Expected Calibration
Error over confidence bins, plus an honest status judgement.

Honesty rules
-------------
- Below ``min_samples`` (default 20) the report is INSUFFICIENT: a
  calibration claim over a handful of signals would be fabricated.
- Bins are an audit trail. Every bin records n, mean confidence and mean
  outcome; unexercised confidence ranges are noted in the reason, never
  silently assumed calibrated.
- Inputs are validated hard: confidences outside [0, 1] or outcomes other
  than 0.0/1.0 raise ValueError. Garbage in, no report out.
- The ECE tolerance is explicit, recorded on the report, and the status
  judgement is derived from it (never hardcoded twice).
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.domain.research.signal_calibration import (
    CalibrationBin,
    CalibrationStatus,
    SignalCalibrationReport,
)

DEFAULT_N_BINS = 10
DEFAULT_MIN_SAMPLES = 20
DEFAULT_ECE_TOLERANCE = 0.10


def _validate_pairs(
    confidences: Sequence[float],
    outcomes: Sequence[float],
) -> None:
    if len(confidences) != len(outcomes):
        raise ValueError("confidences and outcomes must have equal length")
    for value in confidences:
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"confidence out of range: {value!r}")
    for value in outcomes:
        if value not in (0.0, 1.0):
            raise ValueError(f"outcome must be 0.0 or 1.0, got {value!r}")


def evaluate_calibration(
    confidences: Sequence[float],
    outcomes: Sequence[float],
    *,
    n_bins: int = DEFAULT_N_BINS,
    min_samples: int = DEFAULT_MIN_SAMPLES,
    ece_tolerance: float = DEFAULT_ECE_TOLERANCE,
) -> SignalCalibrationReport:
    """Measure a confidence stream against its realized outcomes.

    Returns an INSUFFICIENT report (with reason) when there are fewer than
    ``min_samples`` pairs; otherwise a full Brier/ECE report judged against
    ``ece_tolerance``.
    """
    if n_bins < 1:
        raise ValueError("n_bins must be at least 1")
    if min_samples < 1:
        raise ValueError("min_samples must be at least 1")
    if ece_tolerance < 0.0:
        raise ValueError("ece_tolerance must be non-negative")
    _validate_pairs(confidences, outcomes)

    n = len(confidences)
    if n < min_samples:
        return SignalCalibrationReport(
            n=n,
            n_bins=n_bins,
            brier=0.0,
            ece=0.0,
            ece_tolerance=ece_tolerance,
            status=CalibrationStatus.INSUFFICIENT,
            reason=f"only {n} samples; at least {min_samples} required to judge calibration",
            bins=(),
        )

    bin_confidence: list[list[float]] = [[] for _ in range(n_bins)]
    bin_outcome: list[list[float]] = [[] for _ in range(n_bins)]
    for confidence, outcome in zip(confidences, outcomes, strict=True):
        index = min(int(confidence * n_bins), n_bins - 1)
        bin_confidence[index].append(confidence)
        bin_outcome[index].append(outcome)

    bins: list[CalibrationBin] = []
    exercised = 0
    for index in range(n_bins):
        if not bin_confidence[index]:
            continue
        exercised += 1
        lower = index / n_bins
        upper = (index + 1) / n_bins
        mean_confidence = sum(bin_confidence[index]) / len(bin_confidence[index])
        mean_outcome = sum(bin_outcome[index]) / len(bin_outcome[index])
        bins.append(
            CalibrationBin(
                index=index,
                lower=lower,
                upper=upper,
                n=len(bin_confidence[index]),
                mean_confidence=mean_confidence,
                mean_outcome=mean_outcome,
            )
        )

    brier = (
        sum(
            (confidence - outcome) ** 2
            for confidence, outcome in zip(confidences, outcomes, strict=True)
        )
        / n
    )
    ece = sum(bin_.n * bin_.gap for bin_ in bins) / n

    if ece <= ece_tolerance:
        status = CalibrationStatus.CALIBRATED
        reason = ""
    else:
        status = CalibrationStatus.MISCALIBRATED
        reason = f"ece {ece:.4f} exceeds tolerance {ece_tolerance}"

    if exercised < n_bins:
        missing = ", ".join(
            f"[{index / n_bins:.2f}, {(index + 1) / n_bins:.2f})"
            for index in range(n_bins)
            if not bin_confidence[index]
        )
        reason = (reason + "; " if reason else "") + f"unexercised confidence ranges: {missing}"

    return SignalCalibrationReport(
        n=n,
        n_bins=n_bins,
        brier=brier,
        ece=ece,
        ece_tolerance=ece_tolerance,
        status=status,
        reason=reason,
        bins=tuple(bins),
    )


__all__ = ["evaluate_calibration"]
