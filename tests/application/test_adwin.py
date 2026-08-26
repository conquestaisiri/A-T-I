"""Unit tests for the ADWIN drift detector (integration #27 / #19)."""

from __future__ import annotations

import math

import pytest
from backend.application.validation.adwin import AdwinConfig, AdwinDetector


class TestAdwinConfig:
    def test_rejects_bad_delta(self) -> None:
        with pytest.raises(ValueError):
            AdwinConfig(delta=0.0)
        with pytest.raises(ValueError):
            AdwinConfig(delta=1.0)

    def test_rejects_zero_windows(self) -> None:
        with pytest.raises(ValueError):
            AdwinConfig(max_window=0)
        with pytest.raises(ValueError):
            AdwinConfig(min_window=0)

    def test_rejects_min_above_max(self) -> None:
        with pytest.raises(ValueError):
            AdwinConfig(min_window=50, max_window=10)


class TestAdwinDetector:
    def test_empty_state(self) -> None:
        detector = AdwinDetector(AdwinConfig())
        state = detector.state()
        assert state.observations == 0
        assert state.window_size == 0
        assert state.drifted is False
        assert state.mean == 0.0

    def test_stable_stream_does_not_drift(self) -> None:
        detector = AdwinDetector(AdwinConfig(delta=0.002, min_window=10))
        for _ in range(1_000):
            detector.record(1.0)
        state = detector.state()
        assert state.drifted is False
        assert state.cuts == 0
        assert state.mean == pytest.approx(1.0)

    def test_step_change_triggers_drift(self) -> None:
        detector = AdwinDetector(AdwinConfig(delta=0.002, min_window=10))
        for _ in range(500):
            detector.record(0.0)
        drifted = False
        for _ in range(500):
            detector.record(10.0)
            if detector.state().drifted:
                drifted = True
                break
        assert drifted is True

    def test_step_change_shrinks_window(self) -> None:
        detector = AdwinDetector(AdwinConfig(delta=0.002, min_window=10))
        for _ in range(500):
            detector.record(0.0)
        for _ in range(500):
            detector.record(10.0)
        state = detector.state()
        assert state.window_size < 1_000  # oldest half must have been cut away
        assert state.mean > 5.0  # surviving window reflects the new level

    def test_symmetric_noise_stays_stationary(self) -> None:
        detector = AdwinDetector(AdwinConfig(delta=0.002, min_window=50))
        for i in range(1_000):
            detector.record(50.0 if i % 2 == 0 else 50.2)
        # Mean is constant at ~50.1; the tiny symmetric wobble never fires a cut.
        assert detector.state().cuts == 0

    def test_reset_clears_state(self) -> None:
        detector = AdwinDetector(AdwinConfig(delta=0.002, min_window=10))
        for _ in range(200):
            detector.record(0.0)
        for _ in range(200):
            detector.record(10.0)
        detector.reset()
        state = detector.state()
        assert state.observations == 0
        assert state.cuts == 0

    def test_ignores_nan_and_inf(self) -> None:
        detector = AdwinDetector(AdwinConfig())
        detector.record(math.nan)
        detector.record(math.inf)
        assert detector.state().observations == 0

    def test_state_serialises(self) -> None:
        detector = AdwinDetector(AdwinConfig())
        detector.record(1.0)
        detector.record(2.0)
        data = detector.state().as_dict()
        assert set(data) == {
            "drifted",
            "observations",
            "window_size",
            "mean",
            "variance",
            "cuts",
        }
