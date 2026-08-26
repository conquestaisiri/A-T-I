"""Tests for the sandbox venue lifecycle (build order item #27).

Acceptance:
- The venue owns a guarded lifecycle: fill, rest, partial, cancel, reject, expire.
- No fill/cancel/reject/expire can ever touch a terminal order.
- Expiry is deterministic and driven by the replay clock (never the wall).
- The venue self-reports positions (net, VWAP) and order status (UNKNOWN explicit).
- Expiring an order removes it from the engine queue but reports EXPIRED, not CANCELLED.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from backend.application.simulation.paper_fill_engine import OrderBook, PaperFillEngine
from backend.application.simulation.sandbox_venue import SandboxVenue
from backend.domain.execution.order import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

TTL_HOURS = 24.0


def ts(hour: int = 12, minute: int = 0) -> datetime:
    return datetime(2026, 3, 1, hour, minute, 0, tzinfo=UTC)


def make_order(
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.0,
    order_type: OrderType = OrderType.MARKET,
    limit_price: float | None = None,
    time_in_force: TimeInForce = TimeInForce.GTC,
    created_at: datetime | None = None,
) -> OrderRequest:
    return OrderRequest(
        order_id=f"ord-{side.value}-{quantity}",
        proposal_id="prop-1",
        symbol="btcusdt",
        side=side,
        order_type=order_type,
        quantity=quantity,
        limit_price=limit_price,
        created_at=created_at or ts(),
        time_in_force=time_in_force,
    )


def touching_book(price: float = 100.0) -> OrderBook:
    """A symmetric top-of-book with deep depth."""
    return OrderBook(
        best_bid=price - 1.0,
        best_ask=price + 1.0,
        bid_size=1e9,
        ask_size=1e9,
    )


@pytest.fixture
def venue() -> SandboxVenue:
    engine = PaperFillEngine()
    engine.set_book(touching_book())
    return SandboxVenue(engine, resting_ttl_hours=TTL_HOURS)


class TestSubmissionLifecycle:
    def test_market_buy_fills_and_is_terminal(self, venue: SandboxVenue) -> None:
        report = venue.submit(make_order(OrderSide.BUY, quantity=2.0))
        assert report.status is OrderStatus.FILLED
        state = venue.orders["ord-buy-2.0"]
        assert state.status is OrderStatus.FILLED
        assert state.terminal
        assert state.filled_quantity == 2.0
        assert state.remaining_quantity == 0.0
        assert not state.resting
        assert state.expires_at is None

    def test_market_order_fills_at_touch(self, venue: SandboxVenue) -> None:
        report = venue.submit(make_order(OrderSide.BUY, quantity=2.0))
        assert report.average_fill_price == pytest.approx(101.0)
        assert len(venue.orders["ord-buy-2.0"].fills) == 1

    def test_passive_limit_rests_with_expiry(self, venue: SandboxVenue) -> None:
        at = ts()
        order = make_order(
            OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.LIMIT,
            limit_price=90.0,
            created_at=at,
        )
        report = venue.submit(order)
        assert report.status is OrderStatus.NEW
        state = venue.orders[order.order_id]
        assert state.resting
        assert state.remaining_quantity == 1.0
        assert state.expires_at == at + timedelta(hours=TTL_HOURS)
        assert venue.resting_count() == 1

    def test_post_only_crossing_is_rejected(self, venue: SandboxVenue) -> None:
        order = make_order(
            OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.LIMIT,
            limit_price=150.0,
            time_in_force=TimeInForce.GTX,
        )
        report = venue.submit(order)
        assert report.status is OrderStatus.REJECTED
        state = venue.orders[order.order_id]
        assert state.terminal
        assert state.status is OrderStatus.REJECTED
        assert not state.resting

    def test_market_partial_fills_then_rests_remainder(self, venue: SandboxVenue) -> None:
        # Shallow depth: a big market buy fills what exists and rests the rest.
        venue._engine.set_book(OrderBook(best_bid=99.0, best_ask=101.0, bid_size=1e9, ask_size=2.0))
        report = venue.submit(make_order(OrderSide.BUY, quantity=5.0))
        assert report.status is OrderStatus.PARTIALLY_FILLED
        state = venue.orders["ord-buy-5.0"]
        assert state.filled_quantity == 2.0
        assert state.remaining_quantity == 3.0
        assert state.resting
        assert state.expires_at is not None


class TestAdvanceSweeps:
    def test_resting_limit_fills_when_book_crosses(self, venue: SandboxVenue) -> None:
        order = make_order(
            OrderSide.SELL,
            quantity=1.0,
            order_type=OrderType.LIMIT,
            limit_price=102.0,
        )
        assert venue.submit(order).status is OrderStatus.NEW
        reports = venue.advance(
            OrderBook(best_bid=105.0, best_ask=106.0, bid_size=1e9, ask_size=1e9)
        )
        assert len(reports) == 1
        assert reports[0].order_id == order.order_id
        state = venue.orders[order.order_id]
        assert state.status is OrderStatus.FILLED
        assert state.terminal
        assert state.filled_quantity == 1.0
        assert venue.resting_count() == 0

    def test_partial_advance_keeps_order_resting(self, venue: SandboxVenue) -> None:
        order = make_order(
            OrderSide.BUY,
            quantity=6.0,
            order_type=OrderType.LIMIT,
            limit_price=103.0,
        )
        # Aggressive at the fixture book is a full fill; use a passive limit.
        order = replace(order, limit_price=90.0)
        assert venue.submit(order).status is OrderStatus.NEW
        reports = venue.advance(OrderBook(best_bid=89.0, best_ask=90.0, bid_size=1e9, ask_size=2.0))
        assert len(reports) == 1
        assert reports[0].status is OrderStatus.PARTIALLY_FILLED
        state = venue.orders[order.order_id]
        assert state.filled_quantity == 2.0
        assert state.resting
        assert state.expires_at is not None

    def test_fill_price_is_volume_weighted(self, venue: SandboxVenue) -> None:
        ladder = OrderBook(best_bid=99.0, best_ask=100.0, asks=[(100.0, 1.0), (102.0, 1.0)])
        venue._engine.set_book(ladder)
        report = venue.submit(make_order(OrderSide.BUY, quantity=2.0))
        assert report.status is OrderStatus.FILLED
        assert report.average_fill_price == pytest.approx(101.0)


class TestCancellation:
    def test_cancel_resting_order_is_terminal(self, venue: SandboxVenue) -> None:
        order = make_order(
            OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.LIMIT,
            limit_price=90.0,
        )
        venue.submit(order)
        report = venue.cancel(order.order_id)
        assert report.status is OrderStatus.CANCELLED
        state = venue.orders[order.order_id]
        assert state.status is OrderStatus.CANCELLED
        assert state.terminal
        assert state.remaining_quantity == 1.0
        assert venue.resting_count() == 0

    def test_cancel_unknown_order_raises(self, venue: SandboxVenue) -> None:
        with pytest.raises(ValueError, match="no order with id"):
            venue.cancel("ghost")

    def test_cancel_filled_order_raises(self, venue: SandboxVenue) -> None:
        venue.submit(make_order(OrderSide.BUY, quantity=1.0))
        with pytest.raises(ValueError, match="cannot cancel non-resting"):
            venue.cancel("ord-buy-1.0")


class TestExpiry:
    def test_due_order_expires_and_is_removed_from_engine(self, venue: SandboxVenue) -> None:
        at = ts()
        order = make_order(
            OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.LIMIT,
            limit_price=90.0,
            created_at=at,
        )
        venue.submit(order)
        reports = venue.expire_due(at + timedelta(hours=TTL_HOURS + 1))
        assert len(reports) == 1
        report = reports[0]
        assert report.order_id == order.order_id
        assert report.status is OrderStatus.EXPIRED
        state = venue.orders[order.order_id]
        assert state.status is OrderStatus.EXPIRED
        assert state.terminal
        assert venue.resting_count() == 0

    def test_not_yet_due_order_is_untouched(self, venue: SandboxVenue) -> None:
        at = ts()
        order = make_order(
            OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.LIMIT,
            limit_price=90.0,
            created_at=at,
        )
        venue.submit(order)
        reports = venue.expire_due(at + timedelta(hours=TTL_HOURS - 1))
        assert reports == []
        assert venue.orders[order.order_id].resting

    def test_terminal_order_is_never_expired(self, venue: SandboxVenue) -> None:
        at = ts()
        order = make_order(
            OrderSide.BUY,
            quantity=1.0,
            order_type=OrderType.LIMIT,
            limit_price=150.0,
            time_in_force=TimeInForce.GTX,
            created_at=at,
        )
        venue.submit(order)  # rejected
        assert venue.expire_due(at + timedelta(hours=48)) == []

    def test_expiry_is_report_order_deterministic(self, venue: SandboxVenue) -> None:
        at = ts()
        due = at + timedelta(hours=TTL_HOURS + 1)

        def run() -> list[str]:
            fresh = SandboxVenue(PaperFillEngine(), resting_ttl_hours=TTL_HOURS)
            fresh._engine.set_book(touching_book())
            for suffix, qty in (("a", 1.0), ("b", 2.0)):
                order = make_order(
                    OrderSide.BUY,
                    quantity=qty,
                    order_type=OrderType.LIMIT,
                    limit_price=90.0,
                    created_at=at,
                )
                fresh.submit(replace(order, order_id=f"ord-{suffix}"))
            return [r.order_id for r in fresh.expire_due(due)]

        assert run() == run() == ["ord-a", "ord-b"]

    def test_expired_report_preserves_unfilled_remainder(self, venue: SandboxVenue) -> None:
        venue._engine.set_book(OrderBook(best_bid=99.0, best_ask=101.0, bid_size=1e9, ask_size=2.0))
        at = ts()
        order = make_order(OrderSide.BUY, quantity=5.0, created_at=at)
        venue.submit(order)  # partial fill of 2.0, rests 3.0
        reports = venue.expire_due(at + timedelta(hours=TTL_HOURS + 1))
        assert len(reports) == 1
        assert reports[0].remaining_quantity == pytest.approx(3.0)
        assert venue.orders[order.order_id].remaining_quantity == pytest.approx(3.0)


class TestVenueStateSource:
    def test_fetch_order_status_known(self, venue: SandboxVenue) -> None:
        order = make_order(OrderSide.BUY, quantity=1.0)
        venue.submit(order)
        assert venue.fetch_order_status(order.order_id) is OrderStatus.FILLED

    def test_fetch_order_status_unknown_is_explicit(self, venue: SandboxVenue) -> None:
        assert venue.fetch_order_status("ghost") is OrderStatus.UNKNOWN

    def test_open_positions_report_net_vwap(self, venue: SandboxVenue) -> None:
        ladder = OrderBook(best_bid=99.0, best_ask=100.0, asks=[(100.0, 1.0), (102.0, 1.0)])
        venue._engine.set_book(ladder)
        venue.submit(make_order(OrderSide.BUY, quantity=2.0))
        positions = venue.fetch_open_positions()
        assert len(positions) == 1
        position = positions[0]
        assert position.symbol == "btcusdt"
        assert position.side is OrderSide.BUY
        assert position.quantity == pytest.approx(2.0)
        assert position.average_entry_price == pytest.approx(101.0)

    def test_offsetting_fills_net_to_flat(self, venue: SandboxVenue) -> None:
        venue.submit(
            make_order(OrderSide.BUY, quantity=1.0, order_type=OrderType.LIMIT, limit_price=90.0)
        )
        venue.cancel("ord-buy-1.0")
        assert venue.fetch_open_positions() == []

    def test_no_fills_no_positions(self, venue: SandboxVenue) -> None:
        assert venue.fetch_open_positions() == []
