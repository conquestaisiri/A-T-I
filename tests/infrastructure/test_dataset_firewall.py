"""Tests for the research firewall, task P5-002 ("locked test is dead").

The firewall's contract, from the review (Tier-1 #3) and the task queue:

1. A period claimed as a test set cannot later be served as training data.
2. The claim is recorded immutably (who, when, which experiment) — there is
   no update or delete path.
3. Violations are refused at data-access time, not just at validation time:
   ``load_records`` itself refuses, in every read path.
4. The locked period remains readable as TEST data — the experiment that
   owns the claim must be able to score against it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.dataset_service import DatasetService
from backend.domain.research.dataset import (
    DatasetKind,
    DatasetPurpose,
    DatasetRecord,
    compute_content_hash,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository

DAY = timedelta(days=1)


def t0() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def rec(dataset_id: str, day_offset: int, price: float) -> DatasetRecord:
    ts = t0() + day_offset * DAY
    return DatasetRecord(
        dataset_id=dataset_id,
        source_timestamp=ts,
        available_at=ts,
        payload={"symbol": "btcusdt", "price": price},
        kind=DatasetKind.RAW,
    )


@pytest.fixture
def store(tmp_path) -> SqliteDatasetRepository:
    return SqliteDatasetRepository(Database(tmp_path / "firewall.db"))


def freeze(store, dataset_id: str, records: list[DatasetRecord], version: int = 1) -> None:
    store.append_version(
        dataset_id=dataset_id,
        version=version,
        kind=DatasetKind.RAW,
        content_hash=compute_content_hash(records),
        records=records,
        metadata={},
        created_at=t0(),
    )


class TestLockClaim:
    def test_lock_round_trips_with_claimant_details(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="researcher-ada",
            claimed_at=t0() + 100 * DAY,
        )
        locks = store.list_test_locks("d1")
        assert len(locks) == 1
        lock = locks[0]
        assert lock.dataset_id == "d1"
        assert lock.experiment_id == "exp-1"
        assert lock.claimed_by == "researcher-ada"
        assert lock.start == t0() + 3 * DAY
        assert lock.end == t0() + 5 * DAY

    def test_locks_are_append_only(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0(),
            end=t0() + 2 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        store.lock_test_period(
            dataset_id="d1",
            start=t0(),
            end=t0() + 2 * DAY,
            experiment_id="exp-2",
            claimed_by="ada",
            claimed_at=t0(),
        )
        assert len(store.list_test_locks("d1")) == 2

    def test_rejects_invalid_claims(self, store):
        with pytest.raises(ValueError, match="dataset_id"):
            store.lock_test_period(
                dataset_id="",
                start=t0(),
                end=t0() + DAY,
                experiment_id="e",
                claimed_by="ada",
                claimed_at=t0(),
            )
        with pytest.raises(ValueError, match="experiment_id"):
            store.lock_test_period(
                dataset_id="d1",
                start=t0(),
                end=t0() + DAY,
                experiment_id="",
                claimed_by="ada",
                claimed_at=t0(),
            )
        with pytest.raises(ValueError, match="claimed_by"):
            store.lock_test_period(
                dataset_id="d1",
                start=t0(),
                end=t0() + DAY,
                experiment_id="e",
                claimed_by="",
                claimed_at=t0(),
            )
        with pytest.raises(ValueError, match="start must not be after"):
            store.lock_test_period(
                dataset_id="d1",
                start=t0() + 2 * DAY,
                end=t0(),
                experiment_id="e",
                claimed_by="ada",
                claimed_at=t0(),
            )
        with pytest.raises(ValueError, match="timezone-aware"):
            store.lock_test_period(
                dataset_id="d1",
                start=datetime(2026, 1, 1),
                end=datetime(2026, 1, 2),
                experiment_id="e",
                claimed_by="ada",
                claimed_at=t0(),
            )


class TestFirewallEnforcement:
    def test_training_load_overlapping_lock_is_refused(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        with pytest.raises(ValueError, match="research firewall"):
            store.load_records(
                "d1", 1, start=t0(), end=t0() + 4 * DAY, purpose=DatasetPurpose.TRAINING
            )

    def test_refusal_names_the_conflicting_claim(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="researcher-ada",
            claimed_at=t0(),
        )
        with pytest.raises(ValueError, match="exp-1"):
            store.load_records("d1", 1, purpose=DatasetPurpose.TRAINING)
        with pytest.raises(ValueError, match="researcher-ada"):
            store.load_records("d1", 1, purpose=DatasetPurpose.TRAINING)

    def test_windowless_training_load_overlapping_version_range_is_refused(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        # No window given: the implied window is the whole version range,
        # which overlaps the lock — refusal must still happen.
        with pytest.raises(ValueError, match="research firewall"):
            store.load_records("d1", 1, purpose=DatasetPurpose.TRAINING)

    def test_training_load_outside_lock_is_served(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        served = store.load_records(
            "d1", 1, start=t0(), end=t0() + 2 * DAY, purpose=DatasetPurpose.TRAINING
        )
        assert len(served) == 3

    def test_test_load_of_locked_period_is_served(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        served = store.load_records(
            "d1", 1, start=t0() + 3 * DAY, end=t0() + 5 * DAY, purpose=DatasetPurpose.TEST
        )
        assert len(served) == 3

    def test_default_purpose_is_training_fail_safe(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        with pytest.raises(ValueError, match="research firewall"):
            store.load_records("d1", 1)

    def test_lock_is_dataset_scoped(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        freeze(store, "d2", [rec("d2", i, 50.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 3 * DAY,
            end=t0() + 5 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        served = store.load_records("d2", 1, purpose=DatasetPurpose.TRAINING)
        assert len(served) == 10

    def test_lock_on_adjacent_period_does_not_refuse(self, store):
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 5 * DAY,
            end=t0() + 7 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        # Window ends strictly before the lock: intervals are closed, so a
        # window ending exactly at t0+5d would include boundary records.
        served = store.load_records(
            "d1", 1, start=t0(), end=t0() + 4 * DAY, purpose=DatasetPurpose.TRAINING
        )
        assert len(served) == 5

    def test_closed_intervals_refuse_boundary_touch(self, store):
        # Intervals are closed: a training window ending exactly at the lock
        # start shares the boundary record and must be refused.
        freeze(store, "d1", [rec("d1", i, 100.0 + i) for i in range(10)])
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 5 * DAY,
            end=t0() + 7 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        with pytest.raises(ValueError, match="research firewall"):
            store.load_records(
                "d1", 1, start=t0(), end=t0() + 5 * DAY, purpose=DatasetPurpose.TRAINING
            )


class TestFirewallServiceSeam:
    def test_service_lock_and_audit(self, tmp_path):
        service = DatasetService(SqliteDatasetRepository(Database(tmp_path / "svc.db")))
        events = [rec("btcusdt", i, 100.0 + i) for i in range(30)]
        service.build_raw_dataset(dataset_id="btcusdt", events=_as_events(events))
        service.lock_test_period(
            dataset_id="btcusdt",
            start=t0() + 20 * DAY,
            end=t0() + 29 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
        )
        locks = service.test_locks("btcusdt")
        assert len(locks) == 1
        assert locks[0].experiment_id == "exp-1"

    def test_service_point_in_time_query_is_firewalled(self, tmp_path):
        repo = SqliteDatasetRepository(Database(tmp_path / "svc2.db"))
        service = DatasetService(repo)
        # Live capture: every record knowable at its own source time.
        events = []
        for i in range(30):
            ts = t0() + i * DAY
            events.append(
                DatasetRecord(
                    dataset_id="btcusdt",
                    source_timestamp=ts,
                    available_at=ts,
                    payload={"price": 100.0 + i},
                    kind=DatasetKind.RAW,
                )
            )
        freeze(repo, "btcusdt", events)
        service.lock_test_period(
            dataset_id="btcusdt",
            start=t0() + 20 * DAY,
            end=t0() + 29 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
        )
        # A cutoff past the lock start would serve locked-period data: refused.
        with pytest.raises(ValueError, match="research firewall"):
            service.records_available_by("btcusdt", 1, t0() + 25 * DAY)
        # A cutoff before the lock is untouched: served normally.
        before = service.records_available_by("btcusdt", 1, t0() + 19 * DAY)
        assert len(before) == 20

    def test_point_in_time_query_serves_locked_period_not_yet_knowable(self, store):
        # Records inside the locked period exist, but are not knowable until
        # after the lock's end (backfill). A training query at an earlier
        # cutoff can never reach them and must be served — the firewall
        # evaluates against the exact scope of the load, not the whole version.
        records = []
        for i in range(30):
            ts = t0() + i * DAY
            records.append(
                DatasetRecord(
                    dataset_id="d1",
                    source_timestamp=ts,
                    available_at=ts + 40 * DAY,  # backfilled, known much later
                    payload={"price": 100.0 + i},
                    kind=DatasetKind.RAW,
                )
            )
        freeze(store, "d1", records)
        store.lock_test_period(
            dataset_id="d1",
            start=t0() + 20 * DAY,
            end=t0() + 29 * DAY,
            experiment_id="exp-1",
            claimed_by="ada",
            claimed_at=t0(),
        )
        # Not refused: none of the locked records are knowable by day 19, so
        # the load cannot leak them. It serves the empty set (all records
        # backfilled at day 40+).
        served = store.load_records(
            "d1", 1, purpose=DatasetPurpose.TRAINING, available_by=t0() + 19 * DAY
        )
        assert served == []


def _as_events(records: list[DatasetRecord]):
    from backend.domain.observation.event import ObservationEvent, ObservationEventType

    return [
        ObservationEvent(
            source_id="synthetic",
            source_name="Synthetic",
            event_type=ObservationEventType.TRADE,
            timestamp=r.source_timestamp,
            payload=dict(r.payload),
        )
        for r in records
    ]
