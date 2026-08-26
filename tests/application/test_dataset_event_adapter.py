"""Tests for the dataset record -> events/bars adapter (P5-005)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.dataset_event_adapter import (
    records_to_bars,
    records_to_events,
)
from backend.domain.observation.event import ObservationEventType
from backend.domain.research.dataset import DatasetKind, DatasetRecord
from backend.domain.research.historical_bar import HistoricalBar

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)


def record(index: int, **payload_overrides) -> DatasetRecord:
    payload = {
        "symbol": "btcusdt",
        "trade_id": index + 1,
        "price": 100.0 + index,
        "quantity": 100.0,
        "trade_time": (T0 + index * STEP).isoformat(timespec="milliseconds"),
        "is_market_maker": False,
        "open": 100.0 + index,
        "high": 101.0 + index,
        "low": 99.0 + index,
        "close": 100.5 + index,
        "volume": 100.0,
        "source": "historical",
    }
    payload.update(payload_overrides)
    return DatasetRecord(
        dataset_id="btcusdt",
        source_timestamp=T0 + index * STEP,
        available_at=T0 + timedelta(days=1),
        payload=payload,
        kind=DatasetKind.RAW,
    )


def test_records_to_events_round_trip():
    events = records_to_events([record(0), record(1)], expected_symbol="btcusdt")
    assert len(events) == 2
    assert events[0].event_type is ObservationEventType.TRADE
    assert events[0].timestamp == T0
    assert events[0].payload["price"] == 100.0
    assert events[1].payload["trade_id"] == 2


def test_empty_records_rejected():
    with pytest.raises(ValueError, match="empty"):
        records_to_events([])


def test_missing_symbol_rejected():
    with pytest.raises(ValueError, match="symbol"):
        records_to_events([record(0, symbol=None)])


def test_wrong_symbol_rejected():
    with pytest.raises(ValueError, match="expected symbol"):
        records_to_events([record(0)], expected_symbol="ethusdt")


def test_missing_price_rejected():
    with pytest.raises(ValueError, match="price"):
        records_to_events([record(0, price=0.0)])


def test_out_of_order_records_rejected():
    with pytest.raises(ValueError, match="chronological"):
        records_to_events([record(1), record(0)])


def test_records_to_bars_round_trip():
    bars = records_to_bars([record(0), record(1)])
    assert len(bars) == 2
    assert isinstance(bars[0], HistoricalBar)
    assert bars[0].timestamp == T0
    assert bars[0].open == 100.0
    assert bars[0].close == 100.5


def test_records_to_bars_rejects_ohlcv_missing():
    with pytest.raises(ValueError, match="OHLCV"):
        records_to_bars([record(0, open=None)])
