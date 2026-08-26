"""Tests for the general uncertainty layer (T2-20-1).

Per-signal uncertainty must be derived from measured calibration and
validated out-of-sample: a report built on training folds must produce
honest per-signal bands on unseen folds, and must refuse to claim
anything for confidence ranges it never measured.
"""

from __future__ import annotations

import numpy as np
import pytest
from backend.application.research.prediction_uncertainty import uncertainty_for_confidence
from backend.application.research.signal_calibration import evaluate_calibration
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.research.signal_calibration import SignalCalibrationReport


def overconfident_report() -> SignalCalibrationReport:
    return evaluate_calibration([0.9] * 200, [1.0] * 100 + [0.0] * 100)


class TestUncertaintyFromCalibration:
    def test_calibrated_stream_yields_tiny_uncertainty(self) -> None:
        confidences = [0.5] * 100 + [0.9] * 100
        outcomes = [1.0] * 50 + [0.0] * 50 + [1.0] * 90 + [0.0] * 10
        report = evaluate_calibration(confidences, outcomes)
        for confidence in (0.5, 0.9):
            result = uncertainty_for_confidence(report, confidence)
            assert result is not None
            assert result.uncertainty == pytest.approx(0.0, abs=1e-9)

    def test_overconfident_signal_gets_gap_band(self) -> None:
        report = overconfident_report()
        result = uncertainty_for_confidence(report, 0.9)
        assert result is not None
        assert result.uncertainty == pytest.approx(0.4)
        assert result.band_lower == pytest.approx(0.5)
        assert result.band_upper == pytest.approx(1.0)
        assert result.bin_index == 9
        assert result.bin_n == 200

    def test_band_clamps_to_unit_interval(self) -> None:
        report = evaluate_calibration([0.4] * 200, [1.0] * 200)
        result = uncertainty_for_confidence(report, 0.4)
        assert result is not None
        assert result.band_lower == 0.0
        assert result.uncertainty == pytest.approx(0.6)


class TestHonestyGuards:
    def test_unexercised_range_yields_nothing(self) -> None:
        report = overconfident_report()  # only the 0.9-1.0 bin was exercised
        assert uncertainty_for_confidence(report, 0.3) is None

    def test_insufficient_report_yields_nothing(self) -> None:
        report = evaluate_calibration([0.9, 0.9, 0.9], [1.0, 1.0, 1.0])
        assert uncertainty_for_confidence(report, 0.9) is None

    def test_binned_lookup_honours_report_bin_count(self) -> None:
        report = evaluate_calibration([0.05, 0.15, 0.25], [1.0, 1.0, 1.0], n_bins=5, min_samples=3)
        result = uncertainty_for_confidence(report, 0.05)
        assert result is not None
        assert result.bin_index == 0

    def test_confidence_out_of_range_rejected(self) -> None:
        report = overconfident_report()
        with pytest.raises(ValueError):
            uncertainty_for_confidence(report, 1.1)
        with pytest.raises(ValueError):
            uncertainty_for_confidence(report, -0.1)


class TestOutOfSampleFolds:
    def test_per_signal_uncertainty_on_walk_forward_test_folds(self) -> None:
        """Report trained on train folds must stay honest on unseen folds.

        The stream cycles 20-signal blocks of 0.25/0.5/0.75/0.95, so every
        40+ signal window exercises all four confidence ranges: each
        held-out fold is measurable from its train fold, and (as a
        negative control) a confidence no train fold ever emitted must be
        refused.
        """
        centers = (0.25, 0.5, 0.75, 0.95)
        confidences: list[float] = []
        outcomes: list[float] = []
        for _ in range(2):
            for center in centers:
                hits = round(center * 20)
                confidences += [center] * 20
                outcomes += [1.0] * hits + [0.0] * (20 - hits)
        for center in (0.25, 0.5):
            hits = round(center * 20)
            confidences += [center] * 20
            outcomes += [1.0] * hits + [0.0] * (20 - hits)
        assert len(confidences) == len(outcomes) == 200

        cv = WalkForwardCV(train_size=120, test_size=40)
        folds = cv.split(np.arange(200))
        assert len(folds) == 2

        for train_idx, test_idx in folds:
            train_centers = {confidences[i] for i in train_idx}
            assert {confidences[i] for i in test_idx} <= train_centers
            report = evaluate_calibration(
                [confidences[i] for i in train_idx],
                [outcomes[i] for i in train_idx],
            )
            for i in test_idx:
                result = uncertainty_for_confidence(report, confidences[i])
                assert result is not None, f"fold signal {i} at {confidences[i]} denied"
                assert result.uncertainty < 0.02

        # a confidence no training fold ever emitted is refused
        report = evaluate_calibration(
            [confidences[i] for i in folds[0][0]],
            [outcomes[i] for i in folds[0][0]],
        )
        assert uncertainty_for_confidence(report, 0.6) is None
