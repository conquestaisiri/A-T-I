"""Tests for calibrated signal-quality scoring (T2-19-1).

The calibration check must measure Brier/ECE honestly: perfectly
calibrated streams score as calibrated, over/underconfident streams are
caught, and no verdict is fabricated from too few samples.
"""

from __future__ import annotations

import pytest
from backend.application.research.signal_calibration import evaluate_calibration
from backend.domain.research.signal_calibration import CalibrationStatus


def calibrated_pairs() -> tuple[list[float], list[float]]:
    """200 pairs whose mean outcome per decile equals the confidence exactly."""
    confidences: list[float] = []
    outcomes: list[float] = []
    for center in (0.05, 0.15, 0.25, 0.35, 0.45, 0.55, 0.65, 0.75, 0.85, 0.95):
        hits = round(center * 20)
        confidences += [center] * 20
        outcomes += [1.0] * hits + [0.0] * (20 - hits)
    return confidences, outcomes


class TestBrierAndEce:
    def test_perfectly_calibrated_stream_is_calibrated(self) -> None:
        confidences, outcomes = calibrated_pairs()
        report = evaluate_calibration(confidences, outcomes)
        assert report.status is CalibrationStatus.CALIBRATED
        assert report.ece == pytest.approx(0.0)
        assert report.brier == pytest.approx(0.1675)
        assert report.n == 200
        assert len(report.bins) == 10
        assert sum(bin_.n for bin_ in report.bins) == 200

    def test_overconfident_stream_is_miscalibrated(self) -> None:
        report = evaluate_calibration([0.9] * 200, [1.0] * 100 + [0.0] * 100)
        assert report.status is CalibrationStatus.MISCALIBRATED
        assert report.ece == pytest.approx(0.4)
        assert report.brier == pytest.approx(0.41)
        assert "tolerance" in report.reason

    def test_underconfident_stream_is_miscalibrated(self) -> None:
        report = evaluate_calibration([0.4] * 200, [1.0] * 200)
        assert report.status is CalibrationStatus.MISCALIBRATED
        assert report.ece == pytest.approx(0.6)

    def test_status_honours_explicit_tolerance(self) -> None:
        report = evaluate_calibration(
            [0.9] * 200,
            [1.0] * 100 + [0.0] * 100,
            ece_tolerance=0.4,
        )
        assert report.status is CalibrationStatus.CALIBRATED
        assert report.ece_tolerance == 0.4

    def test_bins_are_the_audit_trail(self) -> None:
        report = evaluate_calibration([0.9] * 200, [1.0] * 100 + [0.0] * 100)
        assert report.bins[0].index == 9  # only the 0.9-1.0 bin was exercised
        assert report.bins[0].n == 200
        assert report.bins[0].mean_confidence == pytest.approx(0.9)
        assert report.bins[0].mean_outcome == pytest.approx(0.5)
        assert report.bins[0].gap == pytest.approx(0.4)
        assert "unexercised confidence ranges" in report.reason


class TestHonestyGuards:
    def test_too_few_samples_yields_no_verdict(self) -> None:
        report = evaluate_calibration([0.9, 0.9, 0.9], [1.0, 1.0, 1.0])
        assert report.status is CalibrationStatus.INSUFFICIENT
        assert report.reason == "only 3 samples; at least 20 required to judge calibration"
        assert report.bins == ()

    def test_exactly_min_samples_is_judged(self) -> None:
        report = evaluate_calibration([0.5] * 20, [1.0] * 20)
        assert report.status is CalibrationStatus.MISCALIBRATED
        assert report.n == 20

    def test_mismatched_lengths_rejected(self) -> None:
        with pytest.raises(ValueError):
            evaluate_calibration([0.5, 0.5], [1.0])

    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            evaluate_calibration([1.5], [1.0])
        with pytest.raises(ValueError):
            evaluate_calibration([-0.1], [1.0])

    def test_non_binary_outcome_rejected(self) -> None:
        with pytest.raises(ValueError):
            evaluate_calibration([0.5], [0.5])

    def test_bad_parameters_rejected(self) -> None:
        with pytest.raises(ValueError):
            evaluate_calibration([0.5] * 25, [1.0] * 25, n_bins=0)
        with pytest.raises(ValueError):
            evaluate_calibration([0.5] * 25, [1.0] * 25, min_samples=0)
        with pytest.raises(ValueError):
            evaluate_calibration([0.5] * 25, [1.0] * 25, ece_tolerance=-0.01)


class TestBinning:
    def test_zero_confidence_lands_in_first_bin(self) -> None:
        report = evaluate_calibration([0.0], [1.0], min_samples=1)
        assert report.bins[0].index == 0
        assert report.bins[0].lower == 0.0
        assert report.bins[0].upper == 0.1

    def test_full_confidence_lands_in_last_bin(self) -> None:
        report = evaluate_calibration([1.0], [1.0], min_samples=1)
        assert report.bins[0].index == 9
        assert report.bins[0].upper == 1.0

    def test_constant_stream_keeps_single_bin(self) -> None:
        report = evaluate_calibration([0.8] * 30, [1.0] * 30)
        assert len(report.bins) == 1
        assert report.bins[0].index == 8

    def test_serialisation_roundtrip(self) -> None:
        confidences, outcomes = calibrated_pairs()
        payload = evaluate_calibration(confidences, outcomes).as_dict()
        assert payload["status"] == "calibrated"
        assert payload["ece"] == pytest.approx(0.0)
        assert payload["brier"] == pytest.approx(0.1675)
        assert len(payload["bins"]) == 10
        assert set(payload) == {
            "n",
            "n_bins",
            "brier",
            "ece",
            "ece_tolerance",
            "status",
            "reason",
            "bins",
        }
