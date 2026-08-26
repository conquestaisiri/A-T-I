"""Tests for the versioned dataset service (P1-001).

The service is the researcher-facing entry point. It must:
- distinguish raw vs normalized datasets;
- freeze immutable, monotonically-versioned snapshots;
- preserve source timestamps and point-in-time availability timestamps;
- expose an explicit point-in-time query (records available by a cutoff).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.dataset_service import DatasetService
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.dataset import DatasetKind, DatasetRecord
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository


def event(symbol: str, ts: datetime, trade_id: int, price: float) -> ObservationEvent:
    return ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts,
        payload={"symbol": symbol, "trade_id": trade_id, "price": price, "quantity": 1.0},
    )


@pytest.fixture
def service(tmp_path) -> DatasetService:
    store = SqliteDatasetRepository(Database(tmp_path / "service.db"))
    return DatasetService(store)


def t0() -> datetime:
    return datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


class TestDatasetService:
    def test_build_raw_dataset_versioning(self, service):
        base = t0()
        events = [
            event("btcusdt", base, 1, 100.0),
            event("btcusdt", base + timedelta(seconds=1), 2, 100.5),
            event("btcusdt", base + timedelta(seconds=2), 3, 100.2),
        ]
        # Explicit availability time makes both builds byte-identical, proving
        # content-addressing: identical data + identical point-in-time stamps
        # must produce an identical hash regardless of wall-clock.
        v1 = service.build_raw_dataset(
            dataset_id="binance-btcusdt", events=events, available_at=base
        )

        assert v1.version == 1
        assert v1.kind is DatasetKind.RAW
        assert v1.record_count == 3
        assert v1.source_start == base
        assert v1.source_end == base + timedelta(seconds=2)

        # Second build is version 2, and v1 is unchanged.
        v2 = service.build_raw_dataset(
            dataset_id="binance-btcusdt", events=events, available_at=base
        )
        assert v2.version == 2
        assert v2.content_hash == v1.content_hash  # identical data, identical hash
        loaded_v1 = service.available_versions("binance-btcusdt")
        assert [v.version for v in loaded_v1] == [2, 1]

    def test_raw_records_are_distinguishable_from_normalized(self, service):
        base = t0()
        raw_events = [event("btcusdt", base, 1, 100.0)]
        service.build_raw_dataset(dataset_id="x", events=raw_events)

        norm = [
            DatasetRecord(
                dataset_id="x",
                source_timestamp=base,
                available_at=base + timedelta(milliseconds=1),
                payload={"trend": 0.1},
                kind=DatasetKind.NORMALIZED,
            )
        ]
        v2 = service.build_normalized_dataset(dataset_id="x", records=norm)

        assert v2.kind is DatasetKind.NORMALIZED
        assert service.latest_version("x").kind is DatasetKind.NORMALIZED
        # The raw version is untouched.
        assert len(service.available_versions("x")) == 2

    def test_normalized_rejects_forward_reference(self, service):
        base = t0()
        bad = DatasetRecord(
            dataset_id="x",
            source_timestamp=base,
            available_at=base - timedelta(seconds=10),  # known before it existed
            payload={"trend": 0.1},
            kind=DatasetKind.NORMALIZED,
        )
        with pytest.raises(ValueError):
            service.build_normalized_dataset(dataset_id="x", records=[bad])

    def test_normalized_rejects_raw_kind_records(self, service):
        base = t0()
        raw = DatasetRecord(
            dataset_id="x",
            source_timestamp=base,
            available_at=base,
            payload={"price": 1.0},
            kind=DatasetKind.RAW,
        )
        with pytest.raises(ValueError):
            service.build_normalized_dataset(dataset_id="x", records=[raw])

    def test_point_in_time_query(self, service):
        base = t0()
        # Identical market data, identical source timestamps...
        market_times = [base + timedelta(hours=i) for i in range(4)]

        # ...but different availability. "live" was captured at market time+10s;
        # "backfill" was only downloaded 48h later. The point-in-time query
        # must see each dataset at its OWN availability, not its market time.
        live = service.build_raw_dataset(
            dataset_id="live",
            events=[event("btcusdt", t, i, 100.0 + i) for i, t in enumerate(market_times)],
            available_at=base + timedelta(seconds=10),
        )
        download = base + timedelta(hours=48)
        backfill = service.build_raw_dataset(
            dataset_id="backfill",
            events=[event("btcusdt", t, i, 100.0 + i) for i, t in enumerate(market_times)],
            available_at=download,
        )
        assert live.record_count == backfill.record_count == 4

        # Live: by market time +60s all records are knowable.
        live_now = service.records_available_by("live", 1, cutoff=base + timedelta(seconds=60))
        assert len(live_now) == 4

        # Backfill: even long AFTER the market events, nothing is knowable
        # until the download time. Source timestamps must not leak.
        before_download = service.records_available_by(
            "backfill", 1, cutoff=base + timedelta(hours=47)
        )
        assert len(before_download) == 0

        at_download = service.records_available_by("backfill", 1, cutoff=download)
        assert len(at_download) == 4

    def test_record_ordering_is_chronological(self, service):
        base = t0()
        events = [
            event("btcusdt", base + timedelta(seconds=30), 3, 103.0),
            event("btcusdt", base, 1, 100.0),
            event("btcusdt", base + timedelta(seconds=10), 2, 102.0),
        ]
        service.build_raw_dataset(
            dataset_id="ordered", events=events, available_at=base + timedelta(seconds=60)
        )

        loaded = service.records_available_by("ordered", 1, cutoff=base + timedelta(seconds=120))
        assert [r.payload["price"] for r in loaded] == [100.0, 102.0, 103.0]
