# backend/infrastructure/sqlite/dataset_repository.py
"""SQLite implementation of the versioned DatasetStore port (tasks P1-001, P5-002)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from backend.application.interfaces.dataset_store import DatasetStore
from backend.domain.research.dataset import (
    DatasetKind,
    DatasetPurpose,
    DatasetRecord,
    DatasetVersion,
    TestPeriodLock,
    compute_content_hash,
)
from backend.infrastructure.sqlite.database import Database


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class SqliteDatasetRepository(DatasetStore):
    """Stores immutable dataset versions and records in SQLite.

    Immutability is enforced at two levels: a PRIMARY KEY on
    ``(dataset_id, version)`` rejects duplicate versions, and the repository
    recomputes the content hash of the incoming records and rejects a snapshot
    whose hash does not match the caller-declared hash — so a stored version is
    provably the exact bytes that were approved for freezing.

    The research firewall (P5-002) lives in :meth:`load_records`: a training
    load whose source-time window overlaps any recorded ``test_period_locks``
    claim is refused at data-access time. Lock claims are append-only — the
    repository exposes no update or delete path for them.
    """

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

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
        if version <= 0:
            raise ValueError("version must be a positive integer")
        actual = compute_content_hash(records)
        if actual != content_hash:
            raise ValueError(
                "content_hash does not match the records (leak/ordering hazard): "
                f"declared {content_hash[:12]}…, computed {actual[:12]}…"
            )
        if not records:
            raise ValueError("cannot freeze an empty dataset version")

        # Stable source window across both kinds stored in the version.
        source_times = [r.source_timestamp for r in records]
        source_start = _iso(min(source_times))
        source_end = _iso(max(source_times))

        with self._db.lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO dataset_versions
                    (dataset_id, version, kind, content_hash, record_count,
                     source_start, source_end, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    version,
                    kind.value,
                    content_hash,
                    len(records),
                    source_start,
                    source_end,
                    _iso(created_at),
                    json.dumps(metadata, sort_keys=True),
                ),
            )
            for record in records:
                self._conn.execute(
                    """
                    INSERT INTO dataset_records
                        (dataset_id, version, kind, source_time, available_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset_id,
                        version,
                        record.kind.value,
                        _iso(record.source_timestamp),
                        _iso(record.available_at),
                        json.dumps(record.payload, sort_keys=True),
                    ),
                )
        # The INSERT raises IntegrityError on a duplicate (dataset_id, version);
        # nothing below has run, so a duplicate version leaves no partial state.
        if cur.rowcount != 1:
            raise ValueError(f"version {version} already exists for dataset {dataset_id}")

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
        """Record an immutable claim that ``[start, end]`` is a locked test set.

        Append-only: there is no update or delete path, so a claim can never
        be silently amended after the fact.
        """
        if not dataset_id:
            raise ValueError("dataset_id must not be empty")
        if not experiment_id:
            raise ValueError("experiment_id must not be empty")
        if not claimed_by:
            raise ValueError("claimed_by must not be empty")
        if start.tzinfo is None or end.tzinfo is None or claimed_at.tzinfo is None:
            raise ValueError("lock times must be timezone-aware")
        if start > end:
            raise ValueError("lock start must not be after lock end")
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO test_period_locks
                    (dataset_id, start_time, end_time, experiment_id, claimed_by, claimed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    dataset_id,
                    _iso(start),
                    _iso(end),
                    experiment_id,
                    claimed_by,
                    _iso(claimed_at),
                ),
            )

    def list_test_locks(self, dataset_id: str) -> list[TestPeriodLock]:
        rows = self._conn.execute(
            """
            SELECT * FROM test_period_locks
            WHERE dataset_id = ?
            ORDER BY claimed_at DESC, id DESC
            """,
            (dataset_id,),
        ).fetchall()
        return [
            TestPeriodLock(
                dataset_id=row["dataset_id"],
                start=_parse(row["start_time"]),
                end=_parse(row["end_time"]),
                experiment_id=row["experiment_id"],
                claimed_by=row["claimed_by"],
                claimed_at=_parse(row["claimed_at"]),
            )
            for row in rows
        ]

    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        rows = self._conn.execute(
            """
            SELECT * FROM dataset_versions
            WHERE dataset_id = ?
            ORDER BY created_at DESC, version DESC
            """,
            (dataset_id,),
        ).fetchall()
        return [_row_to_version(row) for row in rows]

    def latest_version(self, dataset_id: str) -> DatasetVersion | None:
        row = self._conn.execute(
            """
            SELECT * FROM dataset_versions
            WHERE dataset_id = ?
            ORDER BY created_at DESC, version DESC
            LIMIT 1
            """,
            (dataset_id,),
        ).fetchone()
        return _row_to_version(row) if row is not None else None

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
        if purpose is DatasetPurpose.TRAINING:
            self._assert_training_window_free(dataset_id, version, kind, start, end, available_by)
        query = """
            SELECT * FROM dataset_records
            WHERE dataset_id = ? AND version = ?
        """
        params: list[Any] = [dataset_id, version]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind.value)
        if start is not None:
            query += " AND source_time >= ?"
            params.append(_iso(start))
        if end is not None:
            query += " AND source_time <= ?"
            params.append(_iso(end))
        if available_by is not None:
            query += " AND available_at <= ?"
            params.append(_iso(available_by))
        query += " ORDER BY source_time, id"

        rows = self._conn.execute(query, params).fetchall()
        return [
            DatasetRecord(
                dataset_id=row["dataset_id"],
                source_timestamp=_parse(row["source_time"]),
                available_at=_parse(row["available_at"]),
                payload=json.loads(row["payload"]),
                kind=DatasetKind(row["kind"]),
            )
            for row in rows
        ]

    def _assert_training_window_free(
        self,
        dataset_id: str,
        version: int,
        kind: DatasetKind | None,
        start: datetime | None,
        end: datetime | None,
        available_by: datetime | None,
    ) -> None:
        """Refuse a training load that would return any locked test records.

        The check is evaluated against the exact scope of the load (kind,
        source window, point-in-time availability): a lock conflicts only if
        the load's records actually include a source period claimed as a test
        set. A window-less load defaults to the whole version, so it cannot
        silently sweep a locked period. Refusal happens here — at data-access
        time — not at validation time (P5-002 acceptance).
        """
        query = """
            SELECT DISTINCT lock.dataset_id, lock.start_time, lock.end_time,
                            lock.experiment_id, lock.claimed_by, lock.claimed_at
            FROM test_period_locks AS lock
            JOIN dataset_records AS rec
              ON rec.dataset_id = lock.dataset_id
             AND rec.source_time BETWEEN lock.start_time AND lock.end_time
            WHERE rec.dataset_id = ? AND rec.version = ?
        """
        params: list[Any] = [dataset_id, version]
        if kind is not None:
            query += " AND rec.kind = ?"
            params.append(kind.value)
        if start is not None:
            query += " AND rec.source_time >= ?"
            params.append(_iso(start))
        if end is not None:
            query += " AND rec.source_time <= ?"
            params.append(_iso(end))
        if available_by is not None:
            query += " AND rec.available_at <= ?"
            params.append(_iso(available_by))
        query += " ORDER BY lock.claimed_at DESC, lock.id DESC LIMIT 1"

        row = self._conn.execute(query, params).fetchone()
        if row is None:
            return
        raise ValueError(
            "research firewall: training load would leak locked test data "
            f"(dataset={dataset_id}, version={version}): "
            f"[{row['start_time']}..{row['end_time']}] claimed by {row['claimed_by']!r} "
            f"on {row['claimed_at']} for experiment {row['experiment_id']!r}"
        )

    def list_datasets(self) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT dataset_id FROM dataset_versions ORDER BY dataset_id"
        ).fetchall()
        return [row["dataset_id"] for row in rows]


def _row_to_version(row: Any) -> DatasetVersion:
    return DatasetVersion(
        dataset_id=row["dataset_id"],
        version=row["version"],
        kind=DatasetKind(row["kind"]),
        content_hash=row["content_hash"],
        record_count=row["record_count"],
        source_start=_parse(row["source_start"]),
        source_end=_parse(row["source_end"]),
        created_at=_parse(row["created_at"]),
        metadata=json.loads(row["metadata"]),
    )
