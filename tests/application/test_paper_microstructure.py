"""Tests for P2-001 paper microstructure: depth, partial fills, queue, cancel,
latency and impact modelling in PaperFillEngine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.interfaces.order_gateway import CancelableGateway
from backend.application.simulation.paper_fill_engine import (
    OrderBook,
    PaperFeeConfig,
    PaperFillEngine,
)
from backend.domain.execution.order import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_order(**overrides: Any) -> OrderRequest:
    params: dict[str, Any] = dict(
        order_id="ord-1",
        proposal_id="prop-1",
        symbol="btcusdt",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=1.0,
        limit_price=None,
        created_at=ts(),
    )
    params.update(overrides)
    return OrderRequest(**params)


def make_book(bid: float = 99.0, ask: float = 101.0) -> OrderBook:
    return OrderBook(best_bid=bid, best_ask=ask, bid_size=1e9, ask_size=1e9)


class TestDepthAndPartialFills:
    def test_market_sweeps_multi_level_ladder_vwap(self):
        book = OrderBook(
            best_bid=99.0,
            best_ask=100.0,
            asks=[(100.0, 1.0), (102.0, 1.0), (104.0, 1.0)],
        )
        engine = PaperFillEngine()
        engine.set_book(book)
        report = engine.submit(make_order(quantity=2.0))
        assert report.status is OrderStatus.FILLED
        assert report.quantity == 2.0
        # VWAP of (100 * 1 + 102 * 1) / 2
        assert report.average_fill_price == 101.0

    def test_market_partial_fill_when_depth_insufficient(self):
        book = OrderBook(
            best_bid=99.0,
            best_ask=100.0,
            asks=[(100.0, 1.0)],
        )
        engine = PaperFillEngine()
        engine.set_book(book)
        report = engine.submit(make_order(quantity=3.0))
        assert report.status is OrderStatus.PARTIALLY_FILLED
        assert report.quantity == 1.0
        assert report.average_fill_price == 100.0
        assert report.remaining_quantity == 2.0

    def test_sell_partial_fill_walks_bid_ladder(self):
        book = OrderBook(
            best_bid=100.0,
            best_ask=102.0,
            bids=[(100.0, 1.0), (98.0, 1.0)],
        )
        engine = PaperFillEngine()
        engine.set_book(book)
        report = engine.submit(make_order(side=OrderSide.SELL, quantity=1.5))
        assert report.status is OrderStatus.FILLED
        assert report.average_fill_price == pytest.approx(149.0 / 1.5)  # (100*1 + 98*0.5)/1.5

    def test_aggressive_limit_capped_at_limit_price(self):
        book = OrderBook(
            best_bid=99.0,
            best_ask=100.0,
            asks=[(100.0, 1.0), (104.0, 1.0)],
        )
        engine = PaperFillEngine()
        engine.set_book(book)
        report = engine.submit(
            make_order(order_type=OrderType.LIMIT, limit_price=102.0, quantity=2.0)
        )
        # Only the level within the limit fills; the rest never rests (IOC-like
        # would die, but GTC remainder rests) — here GTC remainder stays queued.
        assert report.status is OrderStatus.PARTIALLY_FILLED
        assert report.quantity == 1.0
        assert report.average_fill_price == 100.0
        assert report.remaining_quantity == 1.0
        assert engine.resting_count == 1


class TestQueue:
    def test_passive_limit_rests_with_queue_position(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(make_order(order_type=OrderType.LIMIT, limit_price=98.0))
        assert report.status is OrderStatus.NEW
        assert report.queue_position == 1
        assert report.remaining_quantity == 1.0
        assert engine.resting_count == 1

    def test_fifo_within_price_level(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        first = engine.submit(
            make_order(order_id="ord-1", order_type=OrderType.LIMIT, limit_price=98.0)
        )
        second = engine.submit(
            make_order(order_id="ord-2", order_type=OrderType.LIMIT, limit_price=98.0)
        )
        assert first.queue_position == 1
        assert second.queue_position == 2

    def test_advance_sweeps_marketable_resting_order(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        resting = engine.submit(
            make_order(order_id="ord-1", order_type=OrderType.LIMIT, limit_price=98.0)
        )
        assert resting.status is OrderStatus.NEW
        # Ask drops to 98 -> resting buy becomes marketable.
        fills = engine.advance(OrderBook(best_bid=97.0, best_ask=98.0, bid_size=1e9, ask_size=1e9))
        assert len(fills) == 1
        fill = fills[0]
        assert fill.order_id == "ord-1"
        assert fill.status is OrderStatus.FILLED
        assert fill.is_maker is True
        assert engine.resting_count == 0

    def test_advance_fifo_priority(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        engine.submit(make_order(order_id="first", order_type=OrderType.LIMIT, limit_price=98.0))
        engine.submit(make_order(order_id="second", order_type=OrderType.LIMIT, limit_price=98.0))
        # Only enough depth at 98 for one order.
        fills = engine.advance(OrderBook(best_bid=97.0, best_ask=98.0, bid_size=1e9, ask_size=1.0))
        assert [f.order_id for f in fills] == ["first"]
        assert fills[0].status is OrderStatus.FILLED
        # Second is now at position 1 and still resting.
        assert engine.resting_count == 1

    def test_advance_price_time_priority_across_levels(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        # Two resting buy orders: 98.5 is more aggressive than 98.0.
        engine.submit(make_order(order_id="low", order_type=OrderType.LIMIT, limit_price=98.0))
        engine.submit(make_order(order_id="high", order_type=OrderType.LIMIT, limit_price=98.5))
        # Ask at 98.4 marketable for the 98.5 buyer only.
        fills = engine.advance(OrderBook(best_bid=97.0, best_ask=98.4, bid_size=1e9, ask_size=1e9))
        assert [f.order_id for f in fills] == ["high"]
        assert engine.resting_count == 1

    def test_advance_respects_book_ladder_depth(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        engine.submit(
            make_order(order_id="ord-1", quantity=3.0, order_type=OrderType.LIMIT, limit_price=98.0)
        )
        # Depth at 98 is only 1.0 -> partial fill, remainder rests.
        fills = engine.advance(
            OrderBook(best_bid=97.0, best_ask=98.0, bid_size=1e9, asks=[(98.0, 1.0)])
        )
        assert len(fills) == 1
        assert fills[0].status is OrderStatus.PARTIALLY_FILLED
        assert fills[0].quantity == 1.0
        assert fills[0].remaining_quantity == 2.0
        assert engine.resting_count == 1


class TestCancellation:
    def test_cancel_resting_order(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        engine.submit(make_order(order_id="ord-1", order_type=OrderType.LIMIT, limit_price=98.0))
        report = engine.cancel("ord-1")
        assert report.status is OrderStatus.CANCELLED
        assert report.remaining_quantity == 1.0
        assert engine.resting_count == 0

    def test_cancel_updates_queue_positions(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        engine.submit(make_order(order_id="ord-1", order_type=OrderType.LIMIT, limit_price=98.0))
        engine.submit(make_order(order_id="ord-2", order_type=OrderType.LIMIT, limit_price=98.0))
        engine.cancel("ord-1")
        fills = engine.advance(OrderBook(best_bid=97.0, best_ask=98.0, bid_size=1e9, ask_size=1e9))
        assert [f.order_id for f in fills] == ["ord-2"]

    def test_cancel_unknown_order_raises(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        with pytest.raises(ValueError):
            engine.cancel("does-not-exist")

    def test_cancel_filled_order_raises(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        engine.submit(make_order(order_id="ord-1"))  # market, fills immediately
        with pytest.raises(ValueError):
            engine.cancel("ord-1")


class TestCapabilitiesAndConfig:
    def test_engine_satisfies_cancelable_gateway_protocol(self):
        engine = PaperFillEngine()
        assert isinstance(engine, CancelableGateway)

    def test_modeled_latency_appears_on_reports(self):
        engine = PaperFillEngine(fee_config=PaperFeeConfig(latency_ms=5.0))
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(make_order())
        assert report.latency_ms == 5.0

    def test_impact_bps_worsens_fills_directionally(self):
        engine = PaperFillEngine(fee_config=PaperFeeConfig(impact_bps=100.0))
        engine.set_book(make_book(bid=99.0, ask=101.0))
        buy = engine.submit(make_order(side=OrderSide.BUY))
        sell = engine.submit(make_order(side=OrderSide.SELL))
        assert buy.average_fill_price == pytest.approx(101.0 * 1.01)
        assert sell.average_fill_price == pytest.approx(99.0 * 0.99)

    def test_zero_impact_preserves_legacy_touch_price(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(make_order())
        assert report.average_fill_price == 101.0

    def test_config_rejects_negative_knobs(self):
        with pytest.raises(ValueError):
            PaperFeeConfig(latency_ms=-1.0)
        with pytest.raises(ValueError):
            PaperFeeConfig(impact_bps=-1.0)

    def test_fok_partial_kills_whole_order(self):
        book = OrderBook(best_bid=99.0, best_ask=100.0, asks=[(100.0, 1.0)])
        engine = PaperFillEngine()
        engine.set_book(book)
        report = engine.submit(
            make_order(
                order_type=OrderType.LIMIT,
                limit_price=100.0,
                quantity=2.0,
                time_in_force=TimeInForce.FOK,
            )
        )
        assert report.status is OrderStatus.REJECTED

    def test_ioc_partial_fills_without_resting(self):
        book = OrderBook(best_bid=99.0, best_ask=100.0, asks=[(100.0, 1.0)])
        engine = PaperFillEngine()
        engine.set_book(book)
        report = engine.submit(
            make_order(
                order_type=OrderType.LIMIT,
                limit_price=100.0,
                quantity=2.0,
                time_in_force=TimeInForce.IOC,
            )
        )
        assert report.status is OrderStatus.PARTIALLY_FILLED
        assert report.quantity == 1.0
        assert engine.resting_count == 0

    def test_ioc_passive_never_rests(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(
                order_type=OrderType.LIMIT,
                limit_price=98.0,
                time_in_force=TimeInForce.IOC,
            )
        )
        assert report.status is OrderStatus.NEW
        assert engine.resting_count == 0
