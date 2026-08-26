"""Unit tests for the VPIN toxicity estimator (integration #13)."""

from __future__ import annotations

import pytest
from backend.application.risk.vpin import VpinConfig, VpinTracker


class TestVpinConfig:
    def test_rejects_zero_bucket(self) -> None:
        with pytest.raises(ValueError):
            VpinConfig(bucket_volume=0.0)

    def test_rejects_nonpositive_history(self) -> None:
        with pytest.raises(ValueError):
            VpinConfig(history_size=0)

    def test_rejects_nonpositive_reference(self) -> None:
        with pytest.raises(ValueError):
            VpinConfig(reference_size=0)

    def test_rejects_floor_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            VpinConfig(severity_floor=1.5)

    def test_effective_reference_size_defaults(self) -> None:
        config = VpinConfig(history_size=20)
        assert config.effective_reference_size == 60

    def test_effective_reference_size_min_floor(self) -> None:
        config = VpinConfig(history_size=2)
        assert config.effective_reference_size == 16

    def test_effective_reference_size_explicit(self) -> None:
        config = VpinConfig(reference_size=7)
        assert config.effective_reference_size == 7


class TestVpinTracker:
    def test_empty_state_is_not_toxic(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=100.0, history_size=10))
        state = tracker.state()
        assert state.vpin == 0.0
        assert state.buckets == 0
        assert state.toxic is False
        assert state.toxicity_quartile is None

    def test_balanced_flow_gives_low_vpin(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=100.0, history_size=10))
        for i in range(1000):
            tracker.record(1.0 if i % 2 == 0 else -1.0)
        state = tracker.state()
        assert state.buckets >= 8
        assert state.vpin < 0.2  # buy/sell roughly balanced -> low toxicity

    def test_one_sided_flow_gives_high_vpin(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=100.0, history_size=10))
        for _ in range(1000):
            tracker.record(1.0)  # all buy -> max imbalance
        state = tracker.state()
        assert state.vpin > 0.8
        assert state.toxic is True

    def test_sustained_toxicity_detected(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=50.0, history_size=20))
        # Calm start
        for i in range(500):
            tracker.record(1.0 if i % 2 == 0 else -1.0)
        # Toxic burst: all one-sided
        for _ in range(2000):
            tracker.record(1.0)
        state = tracker.state()
        assert state.toxic is True
        assert state.toxicity_quartile is not None
        assert state.toxicity_quartile > 1.0

    def test_partial_bucket_does_not_create_fraction(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=100.0, history_size=10))
        tracker.record(50.0)
        assert tracker.state().buckets == 0
        tracker.record(50.0)
        assert tracker.state().buckets == 1

    def test_bucket_split_preserves_volume(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=100.0, history_size=10))
        tracker.record(150.0)  # one full bucket + 50 carried
        state = tracker.state()
        assert state.buckets == 1
        assert state.current_bucket_volume == pytest.approx(50.0)

    def test_history_is_bounded(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=10.0, history_size=5))
        for _ in range(1000):
            tracker.record(10.0)
        assert tracker.state().buckets == 5  # deque maxlen caps the window

    def test_state_serialises(self) -> None:
        tracker = VpinTracker(VpinConfig(bucket_volume=10.0, history_size=5))
        for _ in range(100):
            tracker.record(1.0)
        data = tracker.state().as_dict()
        assert set(data) == {
            "vpin",
            "toxicity_quartile",
            "toxic",
            "buckets",
            "current_bucket_volume",
        }
