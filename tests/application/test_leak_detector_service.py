"""Tests for the runtime leak-detector (T1-3-1 / T1-5-1).

The detector probes the research firewall for every frozen version and
reports, per version, whether locked test periods overlap it, whether the
firewall actually refused, and how many records the locks protect. It must:

- report a clean dataset with no locks as clean;
- report a lock over a version's records as refused with protected counts;
- report a lock protecting nothing as a DEAD_LOCK warning (not a leak);
- catch a firewall bypass (a TRAINING load served despite overlapping locks)
  as a LEAK finding — this is the runtime check the firewall itself cannot
  perform;
- keep a single implementation/owner: it drives the store's own
  ``load_records``, never re-implementing the overlap/refusal math.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.interfaces.dataset_store import DatasetStore
from backend.application.research.dataset_service import DatasetService
from backend.application.research.leak_detector_service import LeakDetectorService
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.dataset import (
    DatasetKind,
    DatasetPurpose,
    DatasetRecord,
    DatasetVersion,
)
from backend.domain.research.dataset import (
    TestPeriodLock as LockClaim,
)
from backend.domain.research.leak_detector import LeakFindingKind
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository


def event(ts: datetime, trade_id: int, price: float) -> ObservationEvent:
    return ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts,
        payload={"symbol": "btcusdt", "trade_id": trade_id, "price": price, "quantity": 1.0},
    )


def t0() -> datetime:
    return datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path) -> SqliteDatasetRepository:
    return SqliteDatasetRepository(Database(tmp_path / "leaks.db"))


@pytest.fixture
def service(store) -> LeakDetectorService:
    return LeakDetectorService(store)


class TestCleanAndBasicAudit:
    def test_unknown_dataset_refused(self, service) -> None:
        with pytest.raises(ValueError, match="not found"):
            service.audit("nope")

    def test_no_locks_is_clean(self, service) -> None:
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt",
            events=[event(t0(), 1, 100.0), event(t0() + timedelta(seconds=1), 2, 100.5)],
            available_at=t0(),
        )
        report = service.audit("btcusdt")
        assert report.clean is True
        assert len(report.versions) == 1
        assert report.versions[0].firewall_refused_training is False
        assert report.versions[0].overlapping_locks == ()
        assert report.versions[0].locked_record_count == 0
        assert report.leaks == ()
        assert report.dead_locks == ()

    def test_lock_over_records_is_refused_with_protection(self, service) -> None:
        svc = DatasetService(service._store)
        base = t0()
        svc.build_raw_dataset(
            dataset_id="btcusdt",
            events=[
                event(base, 1, 100.0),
                event(base + timedelta(seconds=1), 2, 100.5),
                event(base + timedelta(seconds=2), 3, 100.2),
                event(base + timedelta(seconds=60), 4, 99.0),
            ],
            available_at=base,
        )
        svc.lock_test_period(
            dataset_id="btcusdt",
            start=base + timedelta(seconds=1),
            end=base + timedelta(seconds=2),
            experiment_id="EXP-1",
            claimed_by="operator",
            claimed_at=base,
        )
        report = service.audit("btcusdt")
        assert report.clean is True
        audit = report.versions[0]
        assert audit.firewall_refused_training is True
        assert len(audit.overlapping_locks) == 1
        assert audit.locked_record_count == 2  # the two records inside the lock window
        assert report.leaks == ()
        assert report.dead_locks == ()
        assert len(report.coverages) == 1
        assert report.coverages[0].protected_record_count == 2

    def test_lock_covering_no_records_is_dead_lock(self, service) -> None:
        svc = DatasetService(service._store)
        base = t0()
        svc.build_raw_dataset(
            dataset_id="btcusdt",
            events=[event(base, 1, 100.0), event(base + timedelta(seconds=1), 2, 100.5)],
            available_at=base,
        )
        svc.lock_test_period(
            dataset_id="btcusdt",
            start=base + timedelta(days=1),
            end=base + timedelta(days=2),
            experiment_id="EXP-1",
            claimed_by="operator",
            claimed_at=base,
        )
        report = service.audit("btcusdt")
        assert report.clean is False
        assert report.leaks == ()
        assert len(report.dead_locks) == 1
        assert report.dead_locks[0].kind is LeakFindingKind.DEAD_LOCK
        audit = report.versions[0]
        assert audit.overlapping_locks == ()
        assert audit.firewall_refused_training is False
        assert report.coverages[0].protected_record_count == 0

    def test_lock_covering_one_version_leaves_other_served(self, service) -> None:
        svc = DatasetService(service._store)
        base = t0()
        svc.build_raw_dataset(
            dataset_id="btcusdt",
            events=[event(base, 1, 100.0), event(base + timedelta(seconds=1), 2, 100.5)],
            available_at=base,
        )
        svc.build_raw_dataset(
            dataset_id="btcusdt",
            events=[
                event(base + timedelta(days=5), 3, 101.0),
                event(base + timedelta(days=5, seconds=1), 4, 101.5),
            ],
            available_at=base + timedelta(days=5),
        )
        svc.lock_test_period(
            dataset_id="btcusdt",
            start=base + timedelta(seconds=0),
            end=base + timedelta(seconds=1),
            experiment_id="EXP-1",
            claimed_by="operator",
            claimed_at=base,
        )
        report = service.audit("btcusdt")
        assert len(report.versions) == 2
        v1, v2 = report.versions
        assert v1.version == 2 and v2.version == 1
        assert v2.firewall_refused_training is True
        assert v1.firewall_refused_training is False
        assert v1.overlapping_locks == ()
        assert report.leaks == ()
        assert report.dead_locks == ()

    def test_as_dict_round_trip(self, service) -> None:
        svc = DatasetService(service._store)
        base = t0()
        svc.build_raw_dataset(
            dataset_id="btcusdt",
            events=[event(base, 1, 100.0), event(base + timedelta(seconds=1), 2, 100.5)],
            available_at=base,
        )
        report = service.audit("btcusdt")
        data = report.as_dict()
        assert data["dataset_id"] == "btcusdt"
        assert data["clean"] is True
        assert data["versions"][0]["firewall_refused_training"] is False
        assert isinstance(data["findings"], list)

    def test_audit_all_covers_multiple_datasets(self, store) -> None:
        svc = DatasetService(store)
        base = t0()
        svc.build_raw_dataset(
            dataset_id="btcusdt", events=[event(base, 1, 100.0)], available_at=base
        )
        svc.build_raw_dataset(
            dataset_id="ethusdt", events=[event(base, 1, 3000.0)], available_at=base
        )
        reports = LeakDetectorService(store).audit_all()
        assert {r.dataset_id for r in reports} == {"btcusdt", "ethusdt"}
        assert all(r.clean for r in reports)


class TestFirewallBypassDetection:
    """The detector must catch a store that fails to refuse a training load.

    A bypass is the one failure the firewall itself cannot observe: a
    TRAINING load that serves records inside a locked window. The detector
    sees it because it compares its own lock-overlap scan against the
    store's actual refusal decision.
    """

    def test_broken_store_is_reported_as_leak(self) -> None:
        store = _FirewalllessStore()
        svc = DatasetService(store)
        base = t0()
        svc.build_raw_dataset(
            dataset_id="btcusdt",
            events=[event(base, 1, 100.0), event(base + timedelta(seconds=1), 2, 100.5)],
            available_at=base,
        )
        svc.lock_test_period(
            dataset_id="btcusdt",
            start=base,
            end=base + timedelta(seconds=1),
            experiment_id="EXP-1",
            claimed_by="operator",
            claimed_at=base,
        )
        report = LeakDetectorService(store).audit("btcusdt")
        audit = report.versions[0]
        assert audit.overlapping_locks != ()
        assert audit.firewall_refused_training is False  # the broken store served it
        assert audit.leak is True
        assert len(report.leaks) == 1
        assert report.leaks[0].kind is LeakFindingKind.LEAK
        assert report.clean is False


class _FirewalllessStore(DatasetStore):
    """In-memory store that records locks but never refuses a training load.

    Models the exact failure the runtime detector exists to catch: a
    data-access path that serves locked records as training data.
    """

    def __init__(self) -> None:
        self._versions: dict[str, list[DatasetVersion]] = {}
        self._records: dict[tuple[str, int], list[DatasetRecord]] = {}
        self._locks: dict[str, list[LockClaim]] = {}

    def append_version(
        self,
        *,
        dataset_id: str,
        version: int,
        kind: DatasetKind,
        content_hash: str,
        records: list[DatasetRecord],
        metadata: dict[str, object],
        created_at: datetime,
    ) -> None:
        self._versions.setdefault(dataset_id, []).append(
            DatasetVersion(
                dataset_id=dataset_id,
                version=version,
                kind=kind,
                content_hash=content_hash,
                record_count=len(records),
                source_start=min(r.source_timestamp for r in records),
                source_end=max(r.source_timestamp for r in records),
                created_at=created_at,
                metadata=metadata,
            )
        )
        self._records[(dataset_id, version)] = sorted(records, key=lambda r: r.source_timestamp)

    def lock_test_period(
        self,
        *,
        dataset_id: str,
        start: datetime,
        end: datetime,
        experiment_id: str,
        claimed_by: str,
        claimed_at: datetime,
    ) -> None:
        self._locks.setdefault(dataset_id, []).append(
            LockClaim(
                dataset_id=dataset_id,
                start=start,
                end=end,
                experiment_id=experiment_id,
                claimed_by=claimed_by,
                claimed_at=claimed_at,
            )
        )

    def list_test_locks(self, dataset_id: str) -> list[LockClaim]:
        return list(self._locks.get(dataset_id, []))

    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        return list(self._versions.get(dataset_id, []))

    def latest_version(self, dataset_id: str) -> DatasetVersion | None:
        versions = self._versions.get(dataset_id, [])
        return versions[-1] if versions else None

    def load_records(
        self,
        dataset_id: str,
        version: int,
        *,
        kind: DatasetKind | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        purpose: DatasetPurpose = DatasetPurpose.TRAINING,
        available_by: datetime | None = None,
    ) -> list[DatasetRecord]:
        # Deliberately NO firewall check: this store always serves training.
        records = list(self._records.get((dataset_id, version), []))
        if kind is not None:
            records = [r for r in records if r.kind is kind]
        if start is not None:
            records = [r for r in records if r.source_timestamp >= start]
        if end is not None:
            records = [r for r in records if r.source_timestamp <= end]
        if available_by is not None:
            records = [r for r in records if r.available_at <= available_by]
        return records

    def list_datasets(self) -> list[str]:
        return list(self._versions.keys())
