# backend/domain/execution/position.py
"""Position value object."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .order import OrderSide, signed_volume


@dataclass(frozen=True, slots=True)
class Position:
    """An open position on a symbol.

    Attributes
    ----------
    symbol: str
        Market symbol.
    side: OrderSide
        Long or short.
    quantity: float
        Current quantity.
    average_entry_price: float
        Volume-weighted average entry price.
    opened_at: datetime
        When the position was opened (aware UTC).
    """

    symbol: str
    side: OrderSide
    quantity: float
    average_entry_price: float
    opened_at: datetime
    stop_loss_price: float | None = None
    take_profit_price: float | None = None

    def __post_init__(self) -> None:
        import math

        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if not math.isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("quantity must be finite and > 0")
        if not math.isfinite(self.average_entry_price) or self.average_entry_price <= 0:
            raise ValueError("average_entry_price must be finite and > 0")
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware")
        if self.stop_loss_price is not None and not math.isfinite(self.stop_loss_price):
            raise ValueError("stop_loss_price must be finite when set")
        if self.take_profit_price is not None and not math.isfinite(self.take_profit_price):
            raise ValueError("take_profit_price must be finite when set")

    @property
    def signed_quantity(self) -> float:
        """Signed volume: +quantity for longs, -quantity for shorts."""
        return signed_volume(self.side, self.quantity)
