# backend/application/simulation/paper_fill_engine.py
"""Deterministic paper order gateway.

Fills orders against a current order book (bid/ask/size). This is
the paper implementation of the ``OrderGateway`` port: it decides *how* an
order fills on a hypothetical venue, never *whether* to trade. Purely
deterministic given the same inputs, so replays reproduce identical results.

Microstructure models (P2-001):
- Spread: buys fill at (or better than) ask, sells fill at (or better than) bid
- Depth: a book can carry a multi-level ladder; orders are filled across the
  ladder (VWAP) until satisfied, which is itself the temporary-impact model:
  the larger the order relative to depth, the worse the average fill price
- Partial fills: if an order exceeds available depth it fills what it can and
  the remainder is reported as ``remaining_quantity``
- Queue: resting limit orders sit in a FIFO price-time queue; ``advance``
  sweeps them when the book moves through their price; ``queue_position`` is
  reported at entry and refreshed at fill time
- Cancellation: ``cancel`` removes a resting order (``CancelableGateway``)
- Latency: a constant modeled ``latency_ms`` is attached to every report
- Impact: optional participation cost (``impact_bps``), default zero
- Post-only: rejected if it would cross the spread
- Fees: configurable taker/maker rate assumptions (default: zero)

All knobs default to the legacy behavior (touch-fill at full depth, no
latency, no impact, fees zero) so existing callers and replays are unchanged
until an operator configures otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.application.interfaces.order_gateway import CancelableGateway, OrderGateway
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)

# A level is (price, size). Bids are ordered best-first (descending price),
# asks best-first (ascending price).
Level = tuple[float, float]
Ladder = list[Level]
# Mutable working ladder (list of [price, size]) used to consume shared depth.
WorkingLadder = list[list[float]]


@dataclass(frozen=True, slots=True)
class PaperFeeConfig:
    """Deterministic simulation tuning knobs.

    Rates are fractions of order notional (price x quantity), consistent
    with how venues quote percentage fees. Defaults are zero so the paper
    simulator stays a deterministic, fee-free baseline out of the box;
    realistic assumptions are configured by the operator (e.g. taker 0.1%).

    ``latency_ms`` models constant venue latency on every report (default 0).
    ``impact_bps`` models participation cost as basis points of arrival
    notional applied against the fill direction (default 0 = off).

    Funding (overnight/perpetual) is deliberately **not** modeled here:
    it is deferred to a separate ``funding_cost`` field on reports and
    trade records rather than being folded into this fee.
    """

    taker_fee_rate: float = 0.0
    maker_fee_rate: float = 0.0
    latency_ms: float = 0.0
    impact_bps: float = 0.0

    def __post_init__(self) -> None:
        if self.taker_fee_rate < 0.0 or self.maker_fee_rate < 0.0:
            raise ValueError("fee rates must be non-negative")
        if self.latency_ms < 0.0:
            raise ValueError("latency_ms must be non-negative")
        if self.impact_bps < 0.0:
            raise ValueError("impact_bps must be non-negative")


@dataclass(frozen=True, slots=True)
class OrderBook:
    """Top-of-book state for paper trading.

    ``best_bid``/``best_ask`` remain authoritative for the touch. Optional
    ``bids``/``asks`` ladders carry multi-level depth; when absent the book is
    treated as a single level at the touch with ``bid_size``/``ask_size``
    liquidity (the legacy top-of-book model).
    """

    best_bid: float
    best_ask: float
    bid_size: float = 0.0
    ask_size: float = 0.0
    bids: Ladder | None = None
    asks: Ladder | None = None

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread(self) -> float:
        return self.best_ask - self.best_bid


@dataclass(slots=True)
class _RestingOrder:
    """A limit order resting in the paper book queue."""

    order: OrderRequest
    limit_price: float
    remaining_quantity: float
    queue_position: int = 0


class PaperFillEngine(OrderGateway, CancelableGateway):
    """Executes orders against a current order book.

    Market orders sweep the ask/bid ladder (VWAP). Limit orders fill when they
    cross the spread, otherwise rest in a FIFO queue. Post-only orders are
    rejected if they would cross the spread. ``advance`` sweeps resting orders
    when the book moves through their price.
    """

    def __init__(self, fee_config: PaperFeeConfig | None = None) -> None:
        self._book: OrderBook | None = None
        self._fee_config = fee_config or PaperFeeConfig()
        self._taker_fee_rate = self._fee_config.taker_fee_rate
        self._maker_fee_rate = self._fee_config.maker_fee_rate
        self._latency_ms = self._fee_config.latency_ms
        self._impact_bps = self._fee_config.impact_bps
        # FIFO queue per (side, price); insertion order within a level is
        # arrival order, giving price-time priority.
        self._queue: dict[tuple[OrderSide, float], list[_RestingOrder]] = {}

    @property
    def book(self) -> OrderBook:
        """Current order book used for fills."""
        if self._book is None:
            raise RuntimeError("PaperFillEngine has no order book; set one first")
        return self._book

    @property
    def resting_count(self) -> int:
        """Total number of orders currently resting in the queue."""
        return sum(len(orders) for orders in self._queue.values())

    def set_book(self, book: OrderBook) -> None:
        """Set the current order book used for subsequent fills."""
        if book.best_bid <= 0 or book.best_ask <= 0:
            raise ValueError("book prices must be positive")
        if book.best_bid >= book.best_ask:
            raise ValueError("best_bid must be less than best_ask")
        self._validate_ladder(book.bids, ascending=False)
        self._validate_ladder(book.asks, ascending=True)
        self._book = book

    @staticmethod
    def _validate_ladder(ladder: Ladder | None, *, ascending: bool) -> None:
        """Validate an optional depth ladder (best-first ordering, positive)."""
        if ladder is None:
            return
        if not ladder:
            raise ValueError("depth ladder must not be empty")
        prices = [price for price, _size in ladder]
        if any(price <= 0 for price in prices) or any(size < 0 for _, size in ladder):
            raise ValueError("depth ladder prices must be positive and sizes non-negative")
        ordered = prices == sorted(prices, reverse=not ascending)
        if not ordered:
            raise ValueError("depth ladder must be best-first sorted")

    @property
    def mark_price(self) -> float:
        """Current mid-price (backward compat)."""
        return self.book.mid

    def set_mark_price(self, mark_price: float) -> None:
        """Set a simple mark price (creates a synthetic book with zero spread)."""
        if mark_price <= 0.0:
            raise ValueError("mark_price must be positive")
        self._book = OrderBook(
            best_bid=mark_price * 0.9999,
            best_ask=mark_price * 1.0001,
            bid_size=1e9,
            ask_size=1e9,
        )

    def _ladder(self, *, side: OrderSide) -> Ladder:
        """Effective depth ladder for a side (ladder or synthetic touch level)."""
        book = self.book
        if side is OrderSide.BUY:
            return book.asks if book.asks is not None else [(book.best_ask, book.ask_size)]
        return book.bids if book.bids is not None else [(book.best_bid, book.bid_size)]

    def submit(self, order: OrderRequest) -> ExecutionReport:
        """Return the deterministic fill for ``order`` at the current book."""
        is_post_only = order.post_only or order.time_in_force is TimeInForce.GTX

        if order.order_type is OrderType.MARKET:
            return self._fill_market(order, is_post_only)

        assert order.limit_price is not None
        return self._fill_limit(order, is_post_only)

    # ------------------------------------------------------------------
    # Fill construction
    # ------------------------------------------------------------------

    def _fill_market(self, order: OrderRequest, is_post_only: bool) -> ExecutionReport:
        """Sweep the touch ladder for a market order (VWAP, possibly partial)."""
        if is_post_only:
            # Post-only market orders don't make sense — reject
            return self._rejected_report(order, "post-only market order rejected")

        filled, vwap = self._sweep(order.side, order.quantity, cap=None)
        status = OrderStatus.FILLED if filled >= order.quantity else OrderStatus.PARTIALLY_FILLED
        return self._filled_report(
            order,
            quantity=filled,
            vwap=vwap,
            status=status,
            is_maker=False,
            remaining_quantity=None if status is OrderStatus.FILLED else order.quantity - filled,
        )

    def _fill_limit(self, order: OrderRequest, is_post_only: bool) -> ExecutionReport:
        """Fill a limit order based on book state, resting it when passive."""
        assert order.limit_price is not None
        limit: float = order.limit_price
        book = self.book

        if order.side is OrderSide.BUY:
            crosses = limit >= book.best_ask
            maker = limit < book.best_bid
        else:
            crosses = limit <= book.best_bid
            maker = limit > book.best_ask

        if is_post_only and crosses:
            # Would cross spread → reject
            return self._rejected_report(order, "post-only order would cross spread")

        if crosses:
            # Aggressive: sweep the ladder up to the limit price (cap for buys,
            # floor for sells — the two bounds are not interchangeable).
            if order.side is OrderSide.BUY:
                filled, vwap = self._sweep(order.side, order.quantity, cap=limit, floor=None)
            else:
                filled, vwap = self._sweep(order.side, order.quantity, cap=None, floor=limit)
            if filled >= order.quantity:
                return self._filled_report(
                    order,
                    quantity=order.quantity,
                    vwap=vwap,
                    status=OrderStatus.FILLED,
                    is_maker=False,
                )
            return self._fill_limit_partial(order, filled, vwap, maker)

        # Passive — rest in the queue (unless the time-in-force never rests).
        if order.time_in_force in (TimeInForce.IOC, TimeInForce.FOK):
            return self._new_report(order, is_maker=maker)
        return self._rest_order(order, maker=maker)

    def _fill_limit_partial(
        self, order: OrderRequest, filled: float, vwap: float, maker: bool
    ) -> ExecutionReport:
        """Handle a limit that crossed but could not fully fill.

        GTC/GTX remainders rest in the queue; IOC/FOK remainders die. FOK is
        all-or-nothing, so a partial kill rejects the whole order.
        """
        if order.time_in_force is TimeInForce.FOK:
            return self._rejected_report(order, "FOK order could not fill in full")
        if order.time_in_force is TimeInForce.IOC:
            return self._filled_report(
                order,
                quantity=filled,
                vwap=vwap,
                status=OrderStatus.PARTIALLY_FILLED,
                is_maker=False,
            )
        remainder = order.quantity - filled
        resting = _RestingOrder(
            order=order, limit_price=order.limit_price or 0.0, remaining_quantity=remainder
        )
        self._enqueue(resting)
        return self._filled_report(
            order,
            quantity=filled,
            vwap=vwap,
            status=OrderStatus.PARTIALLY_FILLED,
            is_maker=False,
            remaining_quantity=remainder,
        )

    # ------------------------------------------------------------------
    # Queue
    # ------------------------------------------------------------------

    def _enqueue(self, resting: _RestingOrder) -> None:
        key = (resting.order.side, resting.limit_price)
        bucket = self._queue.setdefault(key, [])
        resting.queue_position = len(bucket) + 1
        bucket.append(resting)

    def _dequeue(self, key: tuple[OrderSide, float], order_id: str) -> _RestingOrder | None:
        bucket = self._queue.get(key)
        if bucket is None:
            return None
        for index, resting in enumerate(bucket):
            if resting.order.order_id == order_id:
                return bucket.pop(index)
        return None

    def _rest_order(self, order: OrderRequest, *, maker: bool) -> ExecutionReport:
        """Place a passive limit in the FIFO queue."""
        resting = _RestingOrder(
            order=order,
            limit_price=order.limit_price or 0.0,
            remaining_quantity=order.quantity,
        )
        self._enqueue(resting)
        return self._new_report(
            order,
            is_maker=maker,
            queue_position=resting.queue_position,
            remaining_quantity=order.quantity,
        )

    def advance(self, book: OrderBook) -> list[ExecutionReport]:
        """Set ``book`` and sweep resting orders that became marketable.

        Returns the reports for the resting orders that (partially) filled.
        Price-time priority: the most aggressive price level first, FIFO
        within a level. Remaining quantities stay queued.
        """
        self.set_book(book)
        reports: list[ExecutionReport] = []
        # Shared working depth for the sweep: resting orders consume the same
        # liquidity, so a level depleted by one order is gone for the next
        # (no double-filling against the same size).
        working_asks: WorkingLadder = [
            [price, size] for price, size in self._ladder(side=OrderSide.BUY)
        ]
        working_bids: WorkingLadder = [
            [price, size] for price, size in self._ladder(side=OrderSide.SELL)
        ]
        # Buyers are marketable when their limit >= best ask: most aggressive
        # (highest) price first. Sellers mirror (lowest price first).
        buy_keys = sorted((k for k in self._queue if k[0] is OrderSide.BUY), reverse=True)
        sell_keys = sorted((k for k in self._queue if k[0] is OrderSide.SELL), reverse=False)
        for key in buy_keys + sell_keys:
            self._reindex(key)
            bucket = self._queue.get(key)
            if not bucket:
                continue
            side, price = key
            cap = price if side is OrderSide.BUY else None
            floor = None if side is OrderSide.BUY else price
            working = working_asks if side is OrderSide.BUY else working_bids
            for resting in list(bucket):
                filled, vwap = self._sweep(
                    side, resting.remaining_quantity, cap=cap, floor=floor, ladder=working
                )
                if filled <= 0:
                    continue
                resting.remaining_quantity -= filled
                reports.append(
                    self._filled_report(
                        resting.order,
                        quantity=filled,
                        vwap=vwap,
                        status=OrderStatus.FILLED
                        if resting.remaining_quantity <= 0
                        else OrderStatus.PARTIALLY_FILLED,
                        is_maker=True,
                        queue_position=resting.queue_position,
                        remaining_quantity=max(resting.remaining_quantity, 0.0),
                    )
                )
                if resting.remaining_quantity <= 0:
                    bucket.remove(resting)
            self._reindex(key)
        return reports

    def cancel(self, order_id: str) -> ExecutionReport:
        """Cancel a resting order (``CancelableGateway`` capability).

        Removes the order from the FIFO queue and returns a CANCELLED report.
        Raises ValueError if the order is unknown or no longer resting — a
        cancel of a non-existent order is an application bug, not a report.
        """
        for key in list(self._queue):
            resting = self._dequeue(key, order_id)
            if resting is None:
                continue
            self._reindex(key)
            return ExecutionReport(
                order_id=order_id,
                symbol=resting.order.symbol,
                side=resting.order.side,
                quantity=0.0,
                average_fill_price=0.0,
                status=OrderStatus.CANCELLED,
                executed_at=resting.order.created_at,
                fee=0.0,
                venue="paper",
                is_maker=True,
                arrival_price=self._book.mid if self._book is not None else None,
                latency_ms=self._latency_ms,
                remaining_quantity=resting.remaining_quantity,
                queue_position=resting.queue_position,
            )
        raise ValueError(f"no resting order with id {order_id!r}")

    def _reindex(self, key: tuple[OrderSide, float]) -> None:
        """Refresh 1-based queue positions for a price level."""
        for position, resting in enumerate(self._queue.get(key, []), start=1):
            resting.queue_position = position

    # ------------------------------------------------------------------
    # Book sweeps
    # ------------------------------------------------------------------

    def _sweep(
        self,
        side: OrderSide,
        quantity: float,
        *,
        cap: float | None,
        floor: float | None = None,
        ladder: WorkingLadder | None = None,
    ) -> tuple[float, float]:
        """Consume ladder levels for ``quantity``, returning (filled, vwap).

        ``cap`` (buy) / ``floor`` (sell) bound the price the sweep may cross;
        ``None`` means no bound (market). Levels are consumed best-first and
        priced at their level; the result is a VWAP across consumed liquidity.

        ``ladder``, when given, is consumed in place (list of [price, size])
        so successive orders share the same depth. Otherwise a fresh copy of
        the current book ladder is used.
        """
        remaining = quantity
        consumed_notional = 0.0
        working: WorkingLadder = (
            ladder
            if ladder is not None
            else [[price, size] for price, size in self._ladder(side=side)]
        )

        for level in working:
            if remaining <= 0:
                break
            price, size = level[0], level[1]
            if side is OrderSide.BUY and cap is not None and price > cap:
                break
            if side is OrderSide.SELL and floor is not None and price < floor:
                break
            take = min(remaining, size) if size > 0 else 0.0
            if take <= 0:
                continue
            consumed_notional += price * take
            level[1] -= take
            remaining -= take

        filled = quantity - remaining
        if filled <= 0:
            return 0.0, 0.0
        vwap = consumed_notional / filled
        return filled, self._apply_impact(side, vwap)

    def _apply_impact(self, side: OrderSide, vwap: float) -> float:
        """Apply configured participation impact against the fill direction."""
        if self._impact_bps <= 0.0:
            return vwap
        factor = self._impact_bps / 10_000.0
        return vwap * (1.0 + factor) if side is OrderSide.BUY else vwap * (1.0 - factor)

    # ------------------------------------------------------------------
    # Report helpers
    # ------------------------------------------------------------------

    def _fee_for(self, fill_price: float, quantity: float, *, is_maker: bool) -> float:
        """Compute the fee for a filled notional (price x quantity)."""
        rate = self._maker_fee_rate if is_maker else self._taker_fee_rate
        notional = fill_price * quantity
        if rate == 0.0 or notional == 0.0:
            return 0.0
        return notional * rate

    def _filled_report(
        self,
        order: OrderRequest,
        *,
        quantity: float,
        vwap: float,
        status: OrderStatus,
        is_maker: bool,
        queue_position: int | None = None,
        remaining_quantity: float | None = None,
    ) -> ExecutionReport:
        if remaining_quantity is None:
            remaining_quantity = None if status is OrderStatus.FILLED else 0.0
        return ExecutionReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=quantity,
            average_fill_price=vwap,
            status=status,
            executed_at=order.created_at,
            fee=self._fee_for(vwap, quantity, is_maker=is_maker),
            venue="paper",
            is_maker=is_maker,
            arrival_price=self.book.mid,
            latency_ms=self._latency_ms,
            remaining_quantity=remaining_quantity,
            queue_position=queue_position,
        )

    def _new_report(
        self,
        order: OrderRequest,
        *,
        is_maker: bool,
        queue_position: int | None = None,
        remaining_quantity: float | None = None,
    ) -> ExecutionReport:
        return ExecutionReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=0.0,
            average_fill_price=0.0,
            status=OrderStatus.NEW,
            executed_at=order.created_at,
            fee=0.0,
            venue="paper",
            is_maker=is_maker,
            arrival_price=self.book.mid,
            latency_ms=self._latency_ms,
            remaining_quantity=remaining_quantity,
            queue_position=queue_position,
        )

    @staticmethod
    def _rejected_report(order: OrderRequest, reason: str) -> ExecutionReport:
        return ExecutionReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=0.0,
            average_fill_price=0.0,
            status=OrderStatus.REJECTED,
            executed_at=order.created_at,
            fee=0.0,
            venue="paper",
            is_maker=False,
            arrival_price=None,
        )
