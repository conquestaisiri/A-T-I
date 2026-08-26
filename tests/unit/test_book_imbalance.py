"""Unit tests for the depth-weighted OBI + book slope feature (integration #11)."""

from __future__ import annotations

import pytest
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features.book_imbalance import (
    BookImbalanceFeature,
    book_slope,
    order_book_imbalance,
    spread_and_mid,
)


class TestOrderBookImbalance:
    def test_symmetric_book_is_neutral(self) -> None:
        bids = [(100.0, 5.0), (99.5, 3.0)]
        asks = [(100.5, 5.0), (101.0, 3.0)]
        result = order_book_imbalance(bids, asks)
        assert result["obi"] == pytest.approx(0.0, abs=1e-9)

    def test_deeper_bid_side_is_positive(self) -> None:
        bids = [(100.0, 10.0)]
        asks = [(100.5, 1.0)]
        result = order_book_imbalance(bids, asks)
        assert result["obi"] > 0.5

    def test_deeper_ask_side_is_negative(self) -> None:
        bids = [(100.0, 1.0)]
        asks = [(100.5, 10.0)]
        result = order_book_imbalance(bids, asks)
        assert result["obi"] < -0.5

    def test_depth_weighting_prefers_best_level(self) -> None:
        # Same total size, different distribution: best-level weight dominates
        bids_near = [(100.0, 6.0), (99.5, 2.0)]
        bids_far = [(100.0, 2.0), (99.5, 6.0)]
        asks = [(100.5, 4.0), (101.0, 4.0)]
        near = order_book_imbalance(bids_near, asks)["obi"]
        far = order_book_imbalance(bids_far, asks)["obi"]
        assert near > far

    def test_empty_book_is_neutral(self) -> None:
        result = order_book_imbalance([], [])
        assert result["obi"] == 0.0

    def test_respects_depth_levels(self) -> None:
        bids = [(100.0, 1.0), (99.0, 1000.0)]
        asks = [(100.5, 1.0), (101.0, 1000.0)]
        one_level = order_book_imbalance(bids, asks, depth_levels=1)
        assert one_level["levels_used"] == 1


class TestBookSlope:
    def test_two_levels_produce_slope(self) -> None:
        # Mid at 100; size tapers from 16 @ distance 2 to 4 @ distance 6
        bids = [(100.0, 16.0), (98.0, 8.0), (94.0, 4.0)]
        asks = [(102.0, 0.0), (106.0, 0.0), (110.0, 0.0)]
        slope = book_slope(bids, asks, mid_price=100.0)
        assert slope is not None
        assert slope < 0  # size decays with distance from mid

    def test_flat_depth_gives_zero_slope(self) -> None:
        # Equal aggregated size at every distance -> log-log slope ~ 0
        bids = [(100.0, 10.0), (99.0, 10.0), (98.0, 10.0)]
        asks = [(101.0, 10.0), (102.0, 10.0)]
        slope = book_slope(bids, asks, mid_price=100.0)
        assert slope is not None
        assert abs(slope) < 1e-6

    def test_single_distinct_distance_has_no_slope(self) -> None:
        bids = [(100.0, 5.0)]
        asks = [(100.5, 5.0)]
        assert book_slope(bids, asks, mid_price=100.0) is None

    def test_crossed_book_has_no_slope(self) -> None:
        bids = [(101.0, 5.0)]
        asks = [(100.0, 5.0)]
        assert book_slope(bids, asks, mid_price=100.5) is None

    def test_missing_side_has_no_slope(self) -> None:
        assert book_slope([], [(100.5, 5.0)], mid_price=100.0) is None


class TestSpreadAndMid:
    def test_computes_spread_and_mid(self) -> None:
        spread, mid = spread_and_mid([(100.0, 1.0)], [(100.5, 1.0)])
        assert spread == pytest.approx(0.5)
        assert mid == pytest.approx(100.25)

    def test_crossed_book_returns_none(self) -> None:
        assert spread_and_mid([(101.0, 1.0)], [(100.0, 1.0)]) == (None, None)


class TestBookImbalanceFeature:
    def test_computes_from_order_book_event(self, make_order_book_event) -> None:
        # Fixture book: bids 5@100, 3@99.5, 2@99 | asks 4@100.5, 6@101, 1@101.5
        snapshot = ContextSnapshot.from_events((make_order_book_event(),))
        feature = BookImbalanceFeature.compute(snapshot, {"depth_levels": 3})
        value = feature.value
        assert value["mid_price"] == pytest.approx(100.25)
        assert value["spread"] == pytest.approx(0.5)
        assert -1.0 < value["obi"] < 1.0
        assert value["obi"] == pytest.approx(-0.0114943, abs=1e-5)
        assert value["book_slope"] is not None
        assert value["levels_used"] == 3

    def test_requires_order_book_event(self, make_trade_event) -> None:
        snapshot = ContextSnapshot.from_events((make_trade_event(),))
        with pytest.raises(ValueError):
            BookImbalanceFeature.compute(snapshot, {})

    def test_latest_book_wins(self, base_time, make_order_book_event) -> None:
        from datetime import timedelta

        from backend.domain.observation.event import ObservationEvent, ObservationEventType

        first = make_order_book_event()
        later = ObservationEvent(
            source_id="binance",
            source_name="Binance",
            event_type=ObservationEventType.ORDER_BOOK,
            timestamp=first.timestamp + timedelta(seconds=5),
            payload={
                "symbol": "BTCUSDT",
                "bids": [[100.0, 1.0], [99.0, 1.0]],
                "asks": [[100.5, 10.0], [101.0, 10.0]],
            },
        )
        snapshot = ContextSnapshot.from_events((first, later))
        feature = BookImbalanceFeature.compute(snapshot, {})
        assert feature.value["obi"] < -0.5  # ask-heavy latest book
