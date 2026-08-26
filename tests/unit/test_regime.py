# tests/unit/test_regime.py
"""Tests for regime detection price-source correctness (P0-002).

Confirms the regime detector consumes real prices (not timestamps), that the
feature degrades safely with no price data, and that replay is deterministic.
"""

from __future__ import annotations

import pytest
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features.regime import RegimeFeature
from backend.domain.context.regime_detector import (
    GaussianHMM,
    RegimeDetector,
    get_detector,
    reset_detectors,
)

from tests.conftest import build_price_series_events


@pytest.fixture(autouse=True)
def _fresh_detectors() -> None:
    """Each test starts with a clean detector registry."""
    reset_detectors()


class TestRegimeDetectorPriceReturns:
    def test_hand_calculated_returns(self) -> None:
        detector = RegimeDetector(window=60)
        # 5-min bars growing exactly 10% per bar.
        detector.update(100.0)
        detector.update(110.0)
        detector.update(121.0)
        assert detector._returns == pytest.approx([0.10, 0.10])  # noqa: SLF001

    def test_returns_depress_on_fall(self) -> None:
        detector = RegimeDetector(window=60)
        detector.update(200.0)
        detector.update(180.0)
        assert detector._returns == pytest.approx([-0.10])  # noqa: SLF001

    def test_first_price_does_not_produce_return(self) -> None:
        detector = RegimeDetector(window=60)
        detector.update(100.0)
        assert detector._returns == []

    def test_invalid_prices_ignored(self) -> None:
        detector = RegimeDetector(window=60)
        detector.update(100.0)
        detector.update(0.0)
        detector.update(-5.0)
        detector.update(110.0)
        # Only the valid 100 -> 110 transition produces a return.
        assert detector._returns == pytest.approx([0.10])


class TestRegimeFeaturePriceSource:
    def test_feature_feeds_real_price_not_timestamp(self, make_trade_event) -> None:  # noqa: ANN001
        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = RegimeFeature.compute(snapshot, {"symbol": "BTC"})

        # Feature must complete and expose a regime value.
        assert "regime" in feature.value
        # With only 4 returns (< 20), the detector reports insufficient data
        # rather than a fabricated regime.
        assert feature.value["regime_label"] == "insufficient_data"

    def test_timestamp_never_masquerades_as_price(self, make_trade_event) -> None:  # noqa: ANN001
        events = build_price_series_events(make_trade_event, [100, 101, 102])
        snapshot = ContextSnapshot.from_events(tuple(events))
        RegimeFeature.compute(snapshot, {"symbol": "BTC"})
        detector = get_detector("BTC")
        # The detector's last observed price must be the last real price (102),
        # not the snapshot end_timestamp in seconds (~1.7e9).
        assert detector._last_price == 102.0  # noqa: SLF001

    def test_no_price_events_returns_snapshot_state(self, make_order_book_event) -> None:  # noqa: ANN001
        snapshot = ContextSnapshot.from_events((make_order_book_event(),))
        feature = RegimeFeature.compute(snapshot, {"symbol": "BTC"})
        # No price data: feature reports the detector's last-known state
        # (insufficient_data for a fresh detector), never a fabricated price.
        assert feature.value["regime_label"] == "insufficient_data"


class TestRegimeReplayDeterminism:
    def test_same_prices_same_regime(self, make_trade_event) -> None:  # noqa: ANN001
        prices = [float(100 + i) for i in range(60)]  # 59 returns, enough to refit

        def run() -> dict:
            reset_detectors()
            events = build_price_series_events(make_trade_event, prices)
            snapshot = ContextSnapshot.from_events(tuple(events))
            feature = RegimeFeature.compute(snapshot, {"symbol": "BTC"})
            return {
                "regime": feature.value["regime"],
                "regime_label": feature.value["regime_label"],
                "probability": feature.value["probability"],
                "volatility": feature.value["volatility"],
                "trend": feature.value["trend"],
            }

        first = run()
        second = run()
        assert first == second


class TestGaussianHMMDeterminism:
    def test_fit_predict_deterministic(self) -> None:
        import numpy as np

        rng = np.random.default_rng(42)
        X = rng.normal(0.0, 1.0, size=200)

        results = []
        for _ in range(2):
            hmm = GaussianHMM(n_states=2)
            hmm.fit(X)
            regimes, probs = hmm.predict(X)
            results.append((regimes.tolist(), [float(p) for p in probs]))

        assert results[0] == results[1]
