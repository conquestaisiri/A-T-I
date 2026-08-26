# backend/domain/execution/trade_record.py
"""Durable trade outcome record (the ledger row)."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .order import OrderSide


class TradeStatus(enum.StrEnum):
    """Lifecycle status of a trade in the ledger."""

    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class TradeRecord:
    """A durable record of one trade's outcome.

    This is the artifact the reflection and learning layers read. It is
    immutable; closing an open trade produces a new closed record.

    Attributes
    ----------
    trade_id: str
        Unique trade identifier.
    proposal_id: str | None
        Proposal that opened the trade, if any.
    correlation_id: str | None
        Stable correlation id across the pipeline.
    symbol: str
        Market symbol.
    side: OrderSide
        Long or short.
    quantity: float
        Traded quantity.
    entry_price: float
        Average fill price at entry.
    opened_at: datetime
        Entry timestamp (aware UTC).
    exit_price: float | None
        Average fill price at exit (None while open).
    closed_at: datetime | None
        Exit timestamp (None while open).
    realized_pnl: float | None
        Realised PnL **net of all execution fees** (None while open).
    gross_pnl: float | None
        Realised PnL **before any fees** (None while open).
    fee: float | None
        Execution fees charged so far: the entry fee while open, the sum
        of entry + exit fees once closed. None when fees are not modelled.
    funding_cost: float | None
        Total funding / carry cost for the trade, if charged. Kept separate
        from execution fees so the two streams are never conflated. The paper
        simulator charges a deterministic funding schedule only when a
        ``FundingConfig`` is supplied; otherwise it stays None.
    status: TradeStatus
        Open or closed.
    """

    trade_id: str
    proposal_id: str | None
    correlation_id: str | None
    symbol: str
    side: OrderSide
    quantity: float
    entry_price: float
    opened_at: datetime
    exit_price: float | None
    closed_at: datetime | None
    realized_pnl: float | None
    status: TradeStatus
    gross_pnl: float | None = None
    fee: float | None = None
    funding_cost: float | None = None
    entry_arrival_price: float | None = None
    exit_arrival_price: float | None = None

    def __post_init__(self) -> None:
        import math

        if not self.trade_id or not self.trade_id.strip():
            raise ValueError("trade_id must be non-empty")
        if not self.symbol or not self.symbol.strip():
            raise ValueError("symbol must be non-empty")
        if not math.isfinite(self.quantity) or self.quantity <= 0:
            raise ValueError("quantity must be finite and > 0")
        if not math.isfinite(self.entry_price) or self.entry_price <= 0:
            raise ValueError("entry_price must be finite and > 0")
        if self.exit_price is not None and (
            not math.isfinite(self.exit_price) or self.exit_price <= 0
        ):
            raise ValueError("exit_price must be finite and > 0 when set")
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware")

    @classmethod
    def open(
        cls,
        *,
        trade_id: str,
        proposal_id: str | None,
        correlation_id: str | None,
        symbol: str,
        side: OrderSide,
        quantity: float,
        entry_price: float,
        opened_at: datetime,
        fee: float | None = None,
        entry_arrival_price: float | None = None,
    ) -> TradeRecord:
        """Create an open trade record.

        ``fee`` carries the entry fee charged so far (None when fees are not
        modelled or none are charged on entry). ``entry_arrival_price`` is the
        mid at decision/submission time, captured for execution attribution.
        """
        return cls(
            trade_id=trade_id,
            proposal_id=proposal_id,
            correlation_id=correlation_id,
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            opened_at=opened_at,
            exit_price=None,
            closed_at=None,
            realized_pnl=None,
            status=TradeStatus.OPEN,
            gross_pnl=None,
            fee=fee,
            funding_cost=None,
            entry_arrival_price=entry_arrival_price,
            exit_arrival_price=None,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TradeRecord:
        """Reconstruct a trade record from a plain dictionary."""
        exit_price = data.get("exit_price")
        closed_at = data.get("closed_at")
        realized_pnl = data.get("realized_pnl")
        gross_pnl = data.get("gross_pnl")
        fee = data.get("fee")
        funding_cost = data.get("funding_cost")
        entry_arrival_price = data.get("entry_arrival_price")
        exit_arrival_price = data.get("exit_arrival_price")
        return cls(
            trade_id=str(data["trade_id"]),
            proposal_id=_optional_str(data.get("proposal_id")),
            correlation_id=_optional_str(data.get("correlation_id")),
            symbol=str(data["symbol"]),
            side=OrderSide(data["side"]),
            quantity=float(data["quantity"]),
            entry_price=float(data["entry_price"]),
            opened_at=datetime.fromisoformat(data["opened_at"]),
            exit_price=float(exit_price) if exit_price is not None else None,
            closed_at=datetime.fromisoformat(closed_at) if closed_at is not None else None,
            realized_pnl=float(realized_pnl) if realized_pnl is not None else None,
            status=TradeStatus(data["status"]),
            gross_pnl=float(gross_pnl) if gross_pnl is not None else None,
            fee=float(fee) if fee is not None else None,
            funding_cost=float(funding_cost) if funding_cost is not None else None,
            entry_arrival_price=(
                float(entry_arrival_price) if entry_arrival_price is not None else None
            ),
            exit_arrival_price=(
                float(exit_arrival_price) if exit_arrival_price is not None else None
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        """Serialise the trade record to a plain dictionary."""
        return {
            "trade_id": self.trade_id,
            "proposal_id": self.proposal_id,
            "correlation_id": self.correlation_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "opened_at": self.opened_at.isoformat(timespec="milliseconds"),
            "exit_price": self.exit_price,
            "closed_at": self.closed_at.isoformat(timespec="milliseconds")
            if self.closed_at is not None
            else None,
            "realized_pnl": self.realized_pnl,
            "gross_pnl": self.gross_pnl,
            "fee": self.fee,
            "funding_cost": self.funding_cost,
            "entry_arrival_price": self.entry_arrival_price,
            "exit_arrival_price": self.exit_arrival_price,
            "status": self.status.value,
        }


def _optional_str(value: object) -> str | None:
    return str(value) if value is not None else None
