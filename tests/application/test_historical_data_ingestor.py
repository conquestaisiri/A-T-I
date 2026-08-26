"""Tests for historical bar ingestion (P5-005)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.dataset_service import DatasetService
from backend.application.research.historical_data_ingestor import (
    HistoricalDataIngestor,
)
from backend.domain.observation.event import ObservationEventType
from backend.domain.research.dataset import DatasetKind, DatasetPurpose
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=5)


def bars(n: int) -> list:
    from backend.domain.research.historical_bar import HistoricalBar

    result = []
    price = 100.0
    for i in range(n):
        ts = T0 + i * STEP
        result.append(
            HistoricalBar(
                timestamp=ts,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.5,
                volume=100.0,
            )
        )
        price += 0.5
    return result


def service(tmp_path) -> DatasetService:
    return DatasetService(SqliteDatasetRepository(Database(tmp_path / "d.db")))


def test_bars_to_events_shape(tmp_path):
    ingestor = HistoricalDataIngestor(service(tmp_path))
    events = ingestor.bars_to_events(bars(3), symbol="btcusdt")
    assert len(events) == 3
    for index, event in enumerate(events):
        assert event.event_type is ObservationEventType.TRADE
        assert event.timestamp == T0 + index * STEP
        assert event.payload["symbol"] == "btcusdt"
        assert event.payload["price"] == event.payload["close"]
        assert event.payload["trade_id"] == index + 1
        assert event.payload["open"] == 100.0 + index * 0.5
        assert event.payload["quantity"] == 100.0


def test_empty_series_rejected(tmp_path):
    ingestor = HistoricalDataIngestor(service(tmp_path))
    with pytest.raises(ValueError, match="empty"):
        ingestor.bars_to_events([], symbol="btcusdt")


def test_unsorted_bars_rejected(tmp_path):
    ingestor = HistoricalDataIngestor(service(tmp_path))
    with pytest.raises(ValueError, match="chronological"):
        ingestor.bars_to_events(list(reversed(bars(3))), symbol="btcusdt")


def test_duplicate_timestamps_rejected(tmp_path):
    ingestor = HistoricalDataIngestor(service(tmp_path))
    with pytest.raises(ValueError, match="chronological"):
        ingestor.bars_to_events([bars(2)[0], bars(2)[0]], symbol="btcusdt")


def test_freeze_raw_dataset(tmp_path):
    svc = service(tmp_path)
    ingestor = HistoricalDataIngestor(svc)
    version = ingestor.freeze_raw_dataset(
        bars(10),
        dataset_id="btcusdt",
        symbol="btcusdt",
        available_at=T0 + timedelta(days=1),
        metadata={"source": "binance_klines"},
    )
    assert version.version == 1
    assert version.kind is DatasetKind.RAW
    assert version.metadata.get("source") == "binance_klines"
    records = svc._store.load_records(
        "btcusdt", 1, kind=DatasetKind.RAW, purpose=DatasetPurpose.TEST
    )
    assert len(records) == 10
    assert records[0].source_timestamp == T0


def test_freeze_is_immutable_and_versioned(tmp_path):
    svc = service(tmp_path)
    ingestor = HistoricalDataIngestor(svc)
    ingestor.freeze_raw_dataset(bars(5), dataset_id="btcusdt", symbol="btcusdt")
    second = ingestor.freeze_raw_dataset(bars(5), dataset_id="btcusdt", symbol="btcusdt")
    assert second.version == 2
