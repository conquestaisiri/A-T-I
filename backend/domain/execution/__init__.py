# backend/domain/execution/__init__.py
"""Execution domain: venue-agnostic order/position/report contracts.

These value objects are the internal models strategies and the AI interact
with. No exchange SDK, no venue knowledge, ever — one adapter per venue sits
behind the ``OrderGateway`` port in the application layer.
"""

from .execution_report import ExecutionReport
from .order import OrderRequest, OrderSide, OrderStatus, OrderType
from .pnl import realized_pnl, unrealized_pnl
from .position import Position
from .trade_record import TradeRecord, TradeStatus

__all__ = [
    "ExecutionReport",
    "OrderRequest",
    "OrderSide",
    "OrderStatus",
    "OrderType",
    "Position",
    "TradeRecord",
    "TradeStatus",
    "realized_pnl",
    "unrealized_pnl",
]
