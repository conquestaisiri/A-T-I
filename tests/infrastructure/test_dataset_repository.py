"""Tests for the versioned dataset store (P1-001).

The store must be immutable (no overwrites), content-addressable (a declared
hash must match the records), and preserve both the source timestamp and the
point-in-time availability timestamp across a save/reload round-trip.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

import pytest
from backend.domain.research.dataset import DatasetKind, DatasetRecord, compute_content_hash
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository


def rec(
    dataset_id: str,
    source: datetime,
    available: datetime,
    price: float,
    *,
    kind: DatasetKind = DatasetKind.RAW,
) -> DatasetRecord:
    return DatasetRecord(
        dataset_id=dataset_id,
        source_timestamp=source,
        available_at=available,
        payload={"symbol": "btcusdt", "price": price},
        kind=kind,
    )


@pytest.fixture
def store(tmp_path) -> SqliteDatasetRepository:
    return SqliteDatasetRepository(Database(tmp_path / "datasets.db"))


def now() -> datetime:
    return datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


class TestVersionedDatasetStore:
    def test_freezes_and_round_trips(self, store):
        t0 = now()
        records = [rec("d1", t0, t0, 100.0), rec("d1", t0, t0, 101.0)]
        store.append_version(
            dataset_id="d1",
            version=1,
            kind=DatasetKind.RAW,
            content_hash=compute_content_hash(records),
            records=records,
            metadata={"symbol": "btcusdt"},
            created_at=t0,
        )

        loaded = store.load_records("d1", 1)
        assert len(loaded) == 2
        assert loaded[0].source_timestamp == t0
        assert loaded[0].available_at == t0
        assert loaded[0].payload["price"] == 100.0
        assert loaded[0].kind is DatasetKind.RAW

        version = store.latest_version("d1")
        assert version is not None
        assert version.version == 1
        assert version.record_count == 2
        assert version.content_hash == compute_content_hash(records)

    def test_immutability_rejects_duplicate_version(self, store):
        t0 = now()
        records = [rec("d1", t0, t0, 100.0)]
        store.append_version(
            dataset_id="d1",
            version=1,
            kind=DatasetKind.RAW,
            content_hash=compute_content_hash(records),
            records=records,
            metadata={},
            created_at=t0,
        )
        with pytest.raises(sqlite3.IntegrityError):
            store.append_version(
                dataset_id="d1",
                version=1,
                kind=DatasetKind.RAW,
                content_hash=compute_content_hash(records),
                records=records,
                metadata={},
                created_at=t0,
            )

    def test_rejects_hash_mismatch(self, store):
        t0 = now()
        records = [rec("d1", t0, t0, 100.0)]
        with pytest.raises(ValueError):
            store.append_version(
                dataset_id="d1",
                version=1,
                kind=DatasetKind.RAW,
                content_hash="0" * 64,
                records=records,
                metadata={},
                created_at=t0,
            )

    def test_rejects_empty_snapshot(self, store):
        t0 = now()
        with pytest.raises(ValueError):
            store.append_version(
                dataset_id="d1",
                version=1,
                kind=DatasetKind.RAW,
                content_hash="0" * 64,
                records=[],
                metadata={},
                created_at=t0,
            )

    def test_kind_filter_and_time_window(self, store):
        t0 = now()
        raw = [rec("d1", t0, t0, 100.0), rec("d1", t0, t0, 101.0)]
        store.append_version(
            dataset_id="d1",
            version=1,
            kind=DatasetKind.RAW,
            content_hash=compute_content_hash(raw),
            records=raw,
            metadata={},
            created_at=t0,
        )
        norm = [
            DatasetRecord(
                dataset_id="d1",
                source_timestamp=t0,
                available_at=t0,
                payload={"trend": 0.5},
                kind=DatasetKind.NORMALIZED,
            )
        ]
        store.append_version(
            dataset_id="d1",
            version=2,
            kind=DatasetKind.NORMALIZED,
            content_hash=compute_content_hash(norm),
            records=norm,
            metadata={},
            created_at=t0,
        )

        assert len(store.load_records("d1", 1, kind=DatasetKind.RAW)) == 2
        assert len(store.load_records("d1", 1, kind=DatasetKind.NORMALIZED)) == 0
        assert len(store.load_records("d1", 2, kind=DatasetKind.NORMALIZED)) == 1

        versions = store.list_versions("d1")
        assert [v.version for v in versions] == [2, 1]
        assert store.list_datasets() == ["d1"]
