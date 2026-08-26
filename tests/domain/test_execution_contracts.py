"""Unit tests for the execution domain contracts."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderRequest, OrderSide, OrderStatus, OrderType
from backend.domain.execution.position import Position
from backend.domain.execution.trade_record import TradeRecord, TradeStatus


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


class TestOrderRequest:
    def test_zero_quantity_rejected(self):
        with pytest.raises(ValueError):
            make_order(quantity=0.0)

    def test_limit_order_requires_limit_price(self):
        with pytest.raises(ValueError):
            make_order(order_type=OrderType.LIMIT, limit_price=None)

    def test_limit_order_with_price_is_valid(self):
        order = make_order(order_type=OrderType.LIMIT, limit_price=100.0)
        assert order.limit_price == 100.0


class TestExecutionReport:
    def test_is_filled_true_for_filled(self):
        report = ExecutionReport(
            order_id="ord-1",
            symbol="btcusdt",
            side=OrderSide.BUY,
            quantity=1.0,
            average_fill_price=100.0,
            status=OrderStatus.FILLED,
            executed_at=ts(),
        )
        assert report.is_filled

    def test_is_filled_false_for_new(self):
        report = ExecutionReport(
            order_id="ord-1",
            symbol="btcusdt",
            side=OrderSide.BUY,
            quantity=0.0,
            average_fill_price=0.0,
            status=OrderStatus.NEW,
            executed_at=ts(),
        )
        assert not report.is_filled


class TestTradeRecord:
    def test_open_record_defaults(self):
        record = TradeRecord.open(
            trade_id="trade-1",
            proposal_id="prop-1",
            correlation_id="corr-1",
            symbol="btcusdt",
            side=OrderSide.BUY,
            quantity=1.0,
            entry_price=100.0,
            opened_at=ts(),
        )
        assert record.status is TradeStatus.OPEN
        assert record.realized_pnl is None
        assert record.exit_price is None

    def test_roundtrip_preserves_fields(self):
        record = TradeRecord(
            trade_id="trade-1",
            proposal_id="prop-1",
            correlation_id="corr-1",
            symbol="btcusdt",
            side=OrderSide.BUY,
            quantity=1.0,
            entry_price=100.0,
            opened_at=ts(),
            exit_price=110.0,
            closed_at=ts(),
            realized_pnl=10.0,
            status=TradeStatus.CLOSED,
        )
        reloaded = TradeRecord.from_dict(record.as_dict())
        assert reloaded == record

    def test_open_record_roundtrip_with_none_fields(self):
        record = TradeRecord.open(
            trade_id="trade-1",
            proposal_id="prop-1",
            correlation_id=None,
            symbol="btcusdt",
            side=OrderSide.BUY,
            quantity=1.0,
            entry_price=100.0,
            opened_at=ts(),
        )
        reloaded = TradeRecord.from_dict(record.as_dict())
        assert reloaded == record
        assert reloaded.exit_price is None
        assert reloaded.closed_at is None


class TestPosition:
    def test_position_fields(self):
        position = Position(
            symbol="btcusdt",
            side=OrderSide.BUY,
            quantity=1.0,
            average_entry_price=100.0,
            opened_at=ts(),
        )
        assert position.quantity == 1.0
        assert position.average_entry_price == 100.0
