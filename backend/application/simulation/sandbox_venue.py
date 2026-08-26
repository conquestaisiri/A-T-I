# backend/application/simulation/sandbox_venue.py
"""A deterministic, self-reporting sandbox venue.

The sandbox venue wraps a fill engine and owns the authoritative per-order
*lifecycle*: how each order was accepted, whether it rests, what filled, and
its terminal state. It adds the two things a real venue has and a fill engine
does not:

- **Expiry**: a resting order's deadline is ``created_at + resting_ttl``;
  :meth:`expire_due` moves due orders to ``EXPIRED`` (deterministic, driver
  clocks only).
- **Self-reporting**: it implements :class:`VenueStateSource`, so the rest of
  the system can reconcile *venue truth* against internal records without
  needing a live exchange (P2.1: order lifecycle, timeout, cancellation,
  rejection, reconciliation).

The venue is the source of truth for market-exposed state, exactly like a real
adapter. Internal records are the source of truth for intent; any disagreement
surfaces as a reconciliation discrepancy, never silent coercion.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta

from backend.application.interfaces.order_gateway import CancelableGateway, OrderGateway
from backend.application.interfaces.venue_state import VenueStateSource
from backend.application.simulation.paper_fill_engine import OrderBook, PaperFillEngine
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    signed_volume,
)
from backend.domain.execution.order_lifecycle import (
    VenueOrderState,
    ensure_aware_utc,
)
from backend.domain.execution.reconciliation import VenuePosition


class SandboxVenue(OrderGateway, CancelableGateway, VenueStateSource):
    """Self-reporting deterministic venue over a paper fill engine."""

    def __init__(self, engine: PaperFillEngine, resting_ttl_hours: float = 24.0) -> None:
        if resting_ttl_hours <= 0.0:
            raise ValueError("resting_ttl_hours must be positive")
        self._engine = engine
        self._resting_ttl = timedelta(hours=resting_ttl_hours)
        self._states: dict[str, VenueOrderState] = {}
        self._reports: dict[str, ExecutionReport] = {}

    @property
    def orders(self) -> dict[str, VenueOrderState]:
        """The venue's authoritative lifecycle records (read-only snapshot)."""
        return dict(self._states)

    def resting_count(self) -> int:
        """Number of orders currently resting in the venue's book."""
        return sum(1 for state in self._states.values() if state.resting)

    # ------------------------------------------------------------------
    # OrderGateway + CancelableGateway
    # ------------------------------------------------------------------

    def submit(self, order: OrderRequest) -> ExecutionReport:
        """Submit an order and record its lifecycle state."""
        report = self._engine.submit(order)
        self._record_submit(order, report)
        return report

    def advance(self, book: OrderBook) -> list[ExecutionReport]:
        """Set the book, sweep marketable resting orders, record fills."""
        reports = self._engine.advance(book)
        for report in reports:
            self._apply_engine_report(report)
        return reports

    def cancel(self, order_id: str) -> ExecutionReport:
        """Cancel a resting order (terminal)."""
        state = self._states.get(order_id)
        if state is None:
            raise ValueError(f"no order with id {order_id!r} at the sandbox venue")
        if not state.resting:
            raise ValueError(
                f"cannot cancel non-resting order {order_id!r} (status {state.status.value})"
            )
        report = self._engine.cancel(order_id)
        self._states[order_id] = state.as_cancelled()
        self._reports[order_id] = report
        return report

    def expire_due(self, now: datetime) -> list[ExecutionReport]:
        """Expire every resting order whose deadline has passed (deterministic).

        ``now`` is the driver-supplied venue clock (aware UTC never read from
        the wall). Orders expire in order-id order for reproducibility. Each
        is mechanically removed from the fill engine's queue and reported as
        ``EXPIRED`` at ``now``.
        """
        at = ensure_aware_utc(now, "now")
        due = sorted(
            (
                state
                for state in self._states.values()
                if state.resting and state.expires_at is not None and state.expires_at <= at
            ),
            key=lambda state: state.order_id,
        )
        reports: list[ExecutionReport] = []
        for state in due:
            # Mechanically remove the order from the engine's queue; the venue
            # truthfully reports the reason as EXPIRED, not CANCELLED. The
            # engine's FIFO never holds some resting remainders (a market
            # order's partial remainder is not enqueued), so removal is
            # best-effort: the venue's lifecycle is authoritative regardless.
            with suppress(ValueError):
                self._engine.cancel(state.order_id)
            expired = state.as_expired(at)
            self._states[state.order_id] = expired
            report = ExecutionReport(
                order_id=state.order_id,
                symbol=state.symbol,
                side=state.side,
                quantity=0.0,
                average_fill_price=0.0,
                status=OrderStatus.EXPIRED,
                executed_at=at,
                fee=None,
                venue="sandbox",
                remaining_quantity=state.remaining_quantity,
            )
            self._reports[state.order_id] = report
            reports.append(report)
        return reports

    # ------------------------------------------------------------------
    # VenueStateSource port (read-only venue truth)
    # ------------------------------------------------------------------

    def fetch_order_status(self, order_id: str) -> OrderStatus:
        """Current venue status for one order (explicit)."""
        state = self._states.get(order_id)
        return OrderStatus.UNKNOWN if state is None else state.status

    def fetch_open_positions(self) -> list[VenuePosition]:
        """Net positions derived from every fill the venue acknowledges.

        Net signed volume per symbol across the full fill history; a net zero
        (flat) symbol reports no position. Average entry is the volume-weighted
        fill price over the acknowledged fills.
        """
        net: dict[str, float] = {}
        notional: dict[str, float] = {}
        quantity: dict[str, float] = {}
        for state in self._states.values():
            if state.filled_quantity <= 0.0:
                continue
            signed = signed_volume(state.side, state.filled_quantity)
            net[state.symbol] = net.get(state.symbol, 0.0) + signed
            notional[state.symbol] = notional.get(state.symbol, 0.0) + (
                state.filled_quantity * (state.average_fill_price or 0.0)
            )
            quantity[state.symbol] = quantity.get(state.symbol, 0.0) + state.filled_quantity
        if not net:
            return []
        positions: list[VenuePosition] = []
        for symbol in sorted(net):
            signed = net[symbol]
            if abs(signed) <= 1e-9:
                continue
            total_quantity = quantity[symbol]
            average = notional[symbol] / total_quantity if total_quantity > 0.0 else None
            positions.append(
                VenuePosition(
                    symbol=symbol,
                    side=OrderSide.BUY if signed > 0.0 else OrderSide.SELL,
                    quantity=abs(signed),
                    average_entry_price=average,
                    reported_at=None,
                )
            )
        return positions

    # ------------------------------------------------------------------
    # Lifecycle recording
    # ------------------------------------------------------------------

    def _record_submit(self, order: OrderRequest, report: ExecutionReport) -> None:
        at = ensure_aware_utc(order.created_at, "order.created_at")
        state = VenueOrderState(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            created_at=at,
        )
        if report.status is OrderStatus.REJECTED:
            self._states[order.order_id] = state.as_rejected()
        elif report.status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            filled = state.with_fill(report.average_fill_price, report.quantity, report.executed_at)
            if report.status is OrderStatus.PARTIALLY_FILLED:
                filled = filled.as_rested(
                    resting_at=report.executed_at,
                    expires_at=at + self._resting_ttl,
                )
            self._states[order.order_id] = filled
        else:
            # NEW: resting immediately.
            self._states[order.order_id] = state.as_rested(
                resting_at=at,
                expires_at=at + self._resting_ttl,
            )
        self._reports[order.order_id] = report

    def _apply_engine_report(self, report: ExecutionReport) -> None:
        """Record a fill report generated later (e.g. by ``advance``)."""
        state = self._states.get(report.order_id)
        if state is None:
            raise ValueError(f"sandbox venue has no lifecycle record for {report.order_id!r}")
        if report.is_filled:
            updated = state.with_fill(
                report.average_fill_price, report.quantity, report.executed_at
            )
            if (
                updated.resting
                and report.remaining_quantity is not None
                and report.remaining_quantity > 0.0
            ):
                updated = updated.as_rested(
                    resting_at=report.executed_at,
                    expires_at=updated.expires_at or updated.created_at + self._resting_ttl,
                )
            self._states[report.order_id] = updated
        self._reports[report.order_id] = report
