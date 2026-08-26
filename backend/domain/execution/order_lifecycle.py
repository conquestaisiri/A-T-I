# backend/domain/execution/order_lifecycle.py
"""Venue-side order lifecycle state and guarded transitions.

A venue keeps its own record of every order: how it was accepted, whether it
rests, what has filled, and its terminal state. The paper engine fills orders
but does not own this record; the sandbox venue does. Transitions are pure and
guarded — a terminal order can never be filled, cancelled or expired again
because that is an application bug, not a venue event.

All timestamps come from the replay driver (the venue is never asked what time
it is on the wall clock), so a replayed sequence reproduces the identical
lifecycle.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from .order import OrderSide, OrderStatus

# A fill is (price, quantity, timestamp).
Fill = tuple[float, float, datetime]

_FILL_EPS = 1e-9

TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {
        OrderStatus.FILLED,
        OrderStatus.CANCELLED,
        OrderStatus.REJECTED,
        OrderStatus.EXPIRED,
    }
)


@dataclass(frozen=True, slots=True)
class VenueOrderState:
    """A venue's authoritative record of one order.

    Attributes
    ----------
    order_id: str
        Unique order identifier.
    symbol: str
        Market symbol.
    side: OrderSide
        Buy or sell.
    quantity: float
        Requested quantity.
    created_at: datetime
        When the venue accepted the order (aware UTC).
    status: OrderStatus
        Current lifecycle state (never ``UNKNOWN`` here — unknown is reserved
        for unrecognised *venue-reported* states, see order.py).
    filled_quantity: float
        Cumulative quantity filled so far.
    average_fill_price: float | None
        Volume-weighted average fill price, None until the first fill.
    resting_at: datetime | None
        When the order began resting in the book, None until then.
    expires_at: datetime | None
        When a resting order expires (deadline for ``expire_due``). Only set
        once the order rests.
    fills: tuple[Fill, ...]
        Every fill in arrival order (immutable append-only).
    """

    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    created_at: datetime
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: float = 0.0
    average_fill_price: float | None = None
    resting_at: datetime | None = None
    expires_at: datetime | None = None
    fills: tuple[Fill, ...] = ()

    @property
    def remaining_quantity(self) -> float:
        """Quantity still unfilled (exactly the quantity at risk)."""
        return max(self.quantity - self.filled_quantity, 0.0)

    @property
    def resting(self) -> bool:
        """True when the order is live in the book with quantity outstanding."""
        return (
            self.status in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)
            and self.remaining_quantity > 0
        )

    @property
    def terminal(self) -> bool:
        """True once the order leaves the book permanently."""
        return self.status in TERMINAL_STATUSES

    def with_fill(self, price: float, fill_quantity: float, at: datetime) -> VenueOrderState:
        """Return the state after ``fill_quantity`` fills at ``price``.

        Raises
        ------
        ValueError
            If the order is terminal, the fill is non-positive, or it exceeds
            the remaining quantity.
        """
        if self.terminal:
            raise ValueError(f"cannot fill terminal order {self.order_id!r} ({self.status.value})")
        if fill_quantity <= 0.0:
            raise ValueError("fill quantity must be positive")
        if fill_quantity > self.remaining_quantity + _FILL_EPS:
            raise ValueError("fill quantity exceeds remaining quantity")
        if at.tzinfo is None:
            raise ValueError("fill timestamp must be timezone-aware")

        fills = self.fills + ((price, fill_quantity, at),)
        filled = self.filled_quantity + fill_quantity
        average = _vwap(self.average_fill_price, self.filled_quantity, price, fill_quantity)
        status = (
            OrderStatus.FILLED
            if math.isclose(filled, self.quantity, rel_tol=0.0, abs_tol=_FILL_EPS)
            else OrderStatus.PARTIALLY_FILLED
        )
        return type(self)(
            order_id=self.order_id,
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            created_at=self.created_at,
            status=status,
            filled_quantity=filled,
            average_fill_price=average,
            resting_at=self.resting_at,
            expires_at=self.expires_at,
            fills=fills,
        )

    def as_rested(self, resting_at: datetime, expires_at: datetime) -> VenueOrderState:
        """Return the state after the order begins resting in the book."""
        if self.terminal:
            raise ValueError(f"cannot rest terminal order {self.order_id!r} ({self.status.value})")
        if resting_at.tzinfo is None or expires_at.tzinfo is None:
            raise ValueError("resting timestamps must be timezone-aware")
        return type(self)(
            order_id=self.order_id,
            symbol=self.symbol,
            side=self.side,
            quantity=self.quantity,
            created_at=self.created_at,
            status=self.status,
            filled_quantity=self.filled_quantity,
            average_fill_price=self.average_fill_price,
            resting_at=resting_at,
            expires_at=expires_at,
            fills=self.fills,
        )

    def as_rejected(self) -> VenueOrderState:
        """Return the state after the venue rejects the order."""
        return self._terminal(OrderStatus.REJECTED)

    def as_cancelled(self) -> VenueOrderState:
        """Return the state after the venue cancels the order."""
        if not self.resting:
            raise ValueError(
                f"cannot cancel non-resting order {self.order_id!r} ({self.status.value})"
            )
        return self._terminal(OrderStatus.CANCELLED)

    def as_expired(self, at: datetime) -> VenueOrderState:
        """Return the state after a resting order's deadline passes."""
        if not self.resting:
            raise ValueError(
                f"cannot expire non-resting order {self.order_id!r} ({self.status.value})"
            )
        return self._terminal(OrderStatus.EXPIRED)

    def _terminal(self, status: OrderStatus) -> VenueOrderState:
        return replace_terminal(self, status)

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for observability."""
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "remaining_quantity": self.remaining_quantity,
            "average_fill_price": self.average_fill_price,
            "resting_at": self.resting_at.isoformat(timespec="milliseconds")
            if self.resting_at is not None
            else None,
            "expires_at": self.expires_at.isoformat(timespec="milliseconds")
            if self.expires_at is not None
            else None,
            "fills": [
                {"price": price, "quantity": qty, "at": at.isoformat(timespec="milliseconds")}
                for price, qty, at in self.fills
            ],
        }


def replace_terminal(state: VenueOrderState, status: OrderStatus) -> VenueOrderState:
    """Return ``state`` moved to a terminal status (guarded)."""
    if state.terminal:
        raise ValueError(f"cannot move terminal order {state.order_id!r} to {status.value}")
    if status not in TERMINAL_STATUSES:
        raise ValueError(f"{status.value!r} is not a terminal order status")
    return VenueOrderState(
        order_id=state.order_id,
        symbol=state.symbol,
        side=state.side,
        quantity=state.quantity,
        created_at=state.created_at,
        status=status,
        filled_quantity=state.filled_quantity,
        average_fill_price=state.average_fill_price,
        resting_at=state.resting_at,
        expires_at=state.expires_at,
        fills=state.fills,
    )


def _vwap(
    current: float | None,
    current_quantity: float,
    price: float,
    fill_quantity: float,
) -> float:
    """Incremental volume-weighted average fill price."""
    if current is None or current_quantity <= 0.0:
        return price
    total_quantity = current_quantity + fill_quantity
    if total_quantity <= 0.0:
        raise ValueError("cannot compute vwap with zero quantity")
    return (current * current_quantity + price * fill_quantity) / total_quantity


def ensure_aware_utc(value: datetime, name: str) -> datetime:
    """Coerce an unaware datetime to UTC or reject it."""
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
