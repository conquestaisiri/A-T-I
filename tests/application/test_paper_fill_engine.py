"""Unit tests for the deterministic PaperFillEngine gateway."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.simulation.paper_fill_engine import OrderBook, PaperFillEngine
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderRequest, OrderSide, OrderStatus, OrderType


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


def make_book(
    bid: float = 99.0, ask: float = 101.0, bid_size: float = 1e9, ask_size: float = 1e9
) -> OrderBook:
    return OrderBook(best_bid=bid, best_ask=ask, bid_size=bid_size, ask_size=ask_size)


class TestOrderBook:
    def test_mid_price(self):
        book = make_book(bid=99.0, ask=101.0)
        assert book.mid == 100.0

    def test_spread(self):
        book = make_book(bid=99.0, ask=101.0)
        assert book.spread == 2.0


class TestMarkPrice:
    def test_mark_price_unset_raises(self):
        engine = PaperFillEngine()
        with pytest.raises(RuntimeError):
            engine.submit(make_order())

    def test_set_mark_price_rejects_non_positive(self):
        engine = PaperFillEngine()
        with pytest.raises(ValueError):
            engine.set_mark_price(0.0)

    def test_set_book_rejects_invalid(self):
        engine = PaperFillEngine()
        with pytest.raises(ValueError):
            engine.set_book(OrderBook(best_bid=101.0, best_ask=99.0))  # bid >= ask


class TestMarketFills:
    def test_market_buy_fills_at_ask(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report: ExecutionReport = engine.submit(make_order())
        assert report.status is OrderStatus.FILLED
        assert report.average_fill_price == 101.0  # fills at ask
        assert report.quantity == 1.0
        assert report.is_maker is False

    def test_market_sell_fills_at_bid(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=198.0, ask=202.0))
        report = engine.submit(make_order(side=OrderSide.SELL))
        assert report.is_filled
        assert report.average_fill_price == 198.0  # fills at bid

    def test_market_buy_with_mark_price(self):
        """Backward compat: set_mark_price creates synthetic book."""
        engine = PaperFillEngine()
        engine.set_mark_price(100.0)
        report = engine.submit(make_order())
        assert report.is_filled
        # Fills at ask (mark * 1.0001)
        assert report.average_fill_price == pytest.approx(100.01, rel=1e-4)


class TestLimitFills:
    def test_buy_limit_fills_when_aggressive(self):
        """Buy limit above ask crosses spread → fills at ask."""
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(order_type=OrderType.LIMIT, limit_price=102.0),
        )
        assert report.is_filled
        assert report.average_fill_price == 101.0  # fills at ask

    def test_buy_limit_fills_at_exact_ask(self):
        """Buy limit at ask → fills."""
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(order_type=OrderType.LIMIT, limit_price=101.0),
        )
        assert report.is_filled

    def test_buy_limit_not_filled_when_passive(self):
        """Buy limit below bid → rests as NEW."""
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(order_type=OrderType.LIMIT, limit_price=98.0),
        )
        assert report.status is OrderStatus.NEW
        assert not report.is_filled
        assert report.quantity == 0.0

    def test_sell_limit_fills_when_aggressive(self):
        """Sell limit below bid crosses spread → fills at bid."""
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                limit_price=98.0,
            ),
        )
        assert report.is_filled
        assert report.average_fill_price == 99.0  # fills at bid

    def test_sell_limit_not_filled_when_passive(self):
        """Sell limit above ask → rests as NEW."""
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(
                side=OrderSide.SELL,
                order_type=OrderType.LIMIT,
                limit_price=102.0,
            ),
        )
        assert not report.is_filled


class TestPostOnly:
    def test_post_only_buy_rejected_when_aggressive(self):
        """Post-only buy above ask → rejected."""
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(order_type=OrderType.LIMIT, limit_price=102.0, post_only=True),
        )
        assert report.status is OrderStatus.REJECTED

    def test_post_only_buy_rests_when_passive(self):
        """Post-only buy below bid → rests as NEW."""
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(
            make_order(order_type=OrderType.LIMIT, limit_price=98.0, post_only=True),
        )
        assert report.status is OrderStatus.NEW
        assert report.is_maker is True


class TestDeterminism:
    def test_same_inputs_same_fill(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        first = engine.submit(make_order())
        second = engine.submit(make_order())
        assert first.average_fill_price == second.average_fill_price == 101.0
        assert first == second

    def test_execution_timestamp_comes_from_replay_order(self):
        engine = PaperFillEngine()
        engine.set_book(make_book(bid=99.0, ask=101.0))
        report = engine.submit(make_order())
        assert report.executed_at == ts()
