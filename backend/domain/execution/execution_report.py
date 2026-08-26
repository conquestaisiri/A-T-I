# backend/domain/execution/execution_report.py
"""Execution report contract returned by an order gateway.

Extended with fee, venue, maker/taker, and arrival price for slippage
measurement (ADR 0012 follow-up). All new fields are optional to preserve
backward compatibility with PaperFillEngine.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .order import OrderSide, OrderStatus


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """Result of attempting to execute an order.

    Attributes
    ----------
    order_id: str
        Identifier of the order this report concerns.
    symbol: str
        Market symbol.
    side: OrderSide
        Direction executed.
    quantity: float
        Quantity filled (may be partial).
    average_fill_price: float
        Volume-weighted average fill price.
    status: OrderStatus
        Final status of the order.
    executed_at: datetime
        Timestamp of the report (aware UTC).
    fee: float | None
        Fee paid to the venue. None if not reported.
    funding_cost: float | None
        Funding / carry cost charged for this execution, if any. Kept
        separate from the execution fee so the two cost streams are never
        conflated. The paper simulator applies its deterministic funding
        schedule at the trade level (not per order); this field remains None
        on gateway reports.
    venue: str | None
        Exchange venue identifier (e.g., "binance", "bybit").
    is_maker: bool | None
        True if maker (liquidity provider), False if taker, None if unknown.
    arrival_price: float | None
        Mid-price at order submission time for slippage calculation.
    latency_ms: float | None
        Round-trip order-submission latency in milliseconds, measured from
        just before the venue call to its response. None when not measured.
    """

    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    average_fill_price: float
    status: OrderStatus
    executed_at: datetime
    fee: float | None = None
    funding_cost: float | None = None
    venue: str | None = None
    is_maker: bool | None = None
    arrival_price: float | None = None
    latency_ms: float | None = None
    remaining_quantity: float | None = None
    queue_position: int | None = None

    @property
    def is_filled(self) -> bool:
        """Whether any quantity was filled."""
        return self.status in (OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)

    @property
    def slippage_bps(self) -> float | None:
        """Slippage in basis points relative to arrival price.

        Positive = unfavorable (paid more on buy, received less on sell).
        Returns None if arrival_price is not available or order not filled.
        """
        if self.arrival_price is None or not self.is_filled or self.arrival_price <= 0:
            return None
        if self.side is OrderSide.BUY:
            return ((self.average_fill_price - self.arrival_price) / self.arrival_price) * 10_000
        return ((self.arrival_price - self.average_fill_price) / self.arrival_price) * 10_000
