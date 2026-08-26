"""Unit tests for baseline context features."""

from __future__ import annotations

import pytest
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features.liquidity import LiquidityFeature
from backend.domain.context.features.momentum import MomentumFeature
from backend.domain.context.features.trend import TrendFeature
from backend.domain.context.features.volatility import VolatilityFeature
from backend.domain.context.features.volume import VolumeFeature

from tests.conftest import build_price_series_events


class TestBaselineFeatures:
    def test_trend_up(self, make_trade_event):
        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = TrendFeature.compute(snapshot, {"lookback": 5, "flat_threshold_pct": 0.05})
        assert feature.value["direction"] == "up"

    def test_trend_flat(self, make_trade_event):
        events = build_price_series_events(make_trade_event, [100, 100.01, 100.0, 100.02, 100.01])
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = TrendFeature.compute(snapshot, {"lookback": 5, "flat_threshold_pct": 0.05})
        assert feature.value["direction"] == "flat"

    def test_momentum_positive(self, make_trade_event):
        events = build_price_series_events(make_trade_event, [100, 102, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = MomentumFeature.compute(snapshot, {"lookback": 3})
        assert feature.value["rate_of_change_pct"] > 0

    def test_volatility_requires_min_samples(self, make_trade_event):
        events = build_price_series_events(make_trade_event, [100, 101])
        snapshot = ContextSnapshot.from_events(tuple(events))
        with pytest.raises(ValueError):
            VolatilityFeature.compute(snapshot, {"lookback": 10, "min_samples": 3})

    def test_volatility_computes(self, make_trade_event):
        events = build_price_series_events(make_trade_event, [100, 101, 99, 102, 98, 103])
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = VolatilityFeature.compute(snapshot, {"lookback": 6, "min_samples": 3})
        assert feature.value["std_dev"] >= 0

    def test_volume_aggregation(self, make_trade_event):
        events = [
            make_trade_event(price=100, quantity=2.0, offset_seconds=0, trade_id=1),
            make_trade_event(price=101, quantity=4.0, offset_seconds=1, trade_id=2),
        ]
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = VolumeFeature.compute(snapshot, {"lookback": 5})
        assert feature.value["total_volume"] == 6.0
        assert feature.value["average_volume"] == 3.0

    def test_liquidity_from_order_book(self, make_order_book_event):
        snapshot = ContextSnapshot.from_events((make_order_book_event(),))
        feature = LiquidityFeature.compute(snapshot, {"depth_levels": 2, "lookback": 5})
        assert feature.value["source"] == "order_book"
        assert feature.value["total_depth"] == 18.0

    def test_liquidity_trade_proxy(self, make_trade_event):
        events = build_price_series_events(make_trade_event, [100, 101, 102])
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = LiquidityFeature.compute(snapshot, {"depth_levels": 2, "lookback": 5})
        assert feature.value["source"] == "trade_proxy"
