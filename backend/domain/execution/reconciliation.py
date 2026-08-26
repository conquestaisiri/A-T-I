# backend/domain/execution/reconciliation.py
"""Order and position reconciliation contracts.

Reconciliation is the boundary between *what the venue reports* and *what the
system believes*. The venue is always the source of truth for state that has
been exposed to the market; the internal ledger is the source of truth for
intent. Any disagreement is a discrepancy to be surfaced to an operator — never
silently coerced (P0-012).

Sign convention: signed volume is positive for longs (BUY) and negative for
shorts (SELL), so a position flip short→long shows up as a gross mismatch
rather than two equal-and-opposite quantities.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .order import OrderSide
from .order import signed_volume as signed_volume
from .position import Position


class DiscrepancyKind(enum.StrEnum):
    """Why a venue position and the internal position disagree."""

    QUANTITY = "quantity"
    SIDE = "side"
    VENUE_ONLY = "venue_only"
    INTERNAL_ONLY = "internal_only"


@dataclass(frozen=True, slots=True)
class VenuePosition:
    """A position as reported by the venue.

    Attributes
    ----------
    symbol: str
        Market symbol.
    side: OrderSide
        Reported direction. Venues that report net sizes are normalised to
        BUY/SELL before this value object is built.
    quantity: float
        Reported quantity.
    average_entry_price: float | None
        Average entry price reported by the venue, when available.
    reported_at: datetime
        When the venue reported this state (aware UTC).
    """

    symbol: str
    side: OrderSide
    quantity: float
    average_entry_price: float | None = None
    reported_at: datetime | None = None

    @property
    def signed_quantity(self) -> float:
        return signed_volume(self.side, self.quantity)

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary."""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "average_entry_price": self.average_entry_price,
            "reported_at": self.reported_at.isoformat(timespec="milliseconds")
            if self.reported_at is not None
            else None,
        }


@dataclass(frozen=True, slots=True)
class PositionDiscrepancy:
    """A single observable disagreement on one symbol."""

    symbol: str
    kind: DiscrepancyKind
    venue_signed: float | None = None
    internal_signed: float | None = None
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "kind": self.kind.value,
            "venue_signed": self.venue_signed,
            "internal_signed": self.internal_signed,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class ReconciliationReport:
    """Full comparison of venue and internal position state for one symbol.

    Attributes
    ----------
    symbol: str
        Market symbol.
    venue_position: VenuePosition | None
        Venue-reported position, if any.
    internal_position: Position | None
        Internally-tracked position, if any.
    discrepancies: tuple[PositionDiscrepancy, ...]
        Every disagreement found.
    reconciled_at: datetime | None
        When the comparison ran (aware UTC).
    """

    symbol: str
    venue_position: VenuePosition | None
    internal_position: Position | None
    discrepancies: tuple[PositionDiscrepancy, ...] = ()
    reconciled_at: datetime | None = None

    @property
    def consistent(self) -> bool:
        """True when venue and internal state fully agree."""
        return not self.discrepancies

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "consistent": self.consistent,
            "discrepancies": [d.as_dict() for d in self.discrepancies],
            "reconciled_at": self.reconciled_at.isoformat(timespec="milliseconds")
            if self.reconciled_at is not None
            else None,
        }
