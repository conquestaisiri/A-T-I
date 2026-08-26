# backend/infrastructure/sqlite/experiment_repository.py
"""SQLite implementation of the ExperimentStore port (task P1-005)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from backend.application.interfaces.experiment_store import ExperimentStore
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)
from backend.infrastructure.sqlite.database import Database

_TERMINAL = (ExperimentStatus.DONE, ExperimentStatus.FAILED, ExperimentStatus.ABORTED)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class SqliteExperimentRepository(ExperimentStore):
    """Stores immutable experiment records and final-test claims in SQLite.

    Immutability is structural: ``experiment_id`` is UNIQUE, so re-registering
    an id raises and never overwrites; status transitions are validated in this
    repository against the stored state before any write; final-test claims
    are a PRIMARY KEY table whose rows are never deleted.
    """

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def save(self, record: ExperimentRecord) -> None:
        if record.status in _TERMINAL:
            raise ValueError(
                f"cannot save a terminal record {record.experiment_id} ({record.status.value})"
            )
        row = _record_row(record)
        try:
            with self._db.lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO experiments
                        (experiment_id, created_at, hypothesis, dataset_id,
                         dataset_version, group_kind, status, scorer_name,
                         features, label_definition, cost_model, metrics,
                         parent_experiment_id, failure_reason, payload)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"experiment {record.experiment_id} already exists (immutable records)"
            ) from exc

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        return _row_to_record(row) if row is not None else None

    def list(
        self,
        *,
        group: ExperimentGroup | None = None,
        status: ExperimentStatus | None = None,
    ) -> list[ExperimentRecord]:
        query = "SELECT * FROM experiments"
        clauses: list[str] = []
        params: list[str] = []
        if group is not None:
            clauses.append("group_kind = ?")
            params.append(group.value)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY created_at DESC, id DESC"
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_record(row) for row in rows]

    def set_status(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        failure_reason: str | None = None,
    ) -> ExperimentRecord:
        return self._record_result(
            experiment_id, status, metrics=None, failure_reason=failure_reason
        )

    def record_result(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        *,
        metrics: dict[str, object],
        failure_reason: str | None = None,
    ) -> ExperimentRecord:
        return self._record_result(
            experiment_id, status, metrics=metrics, failure_reason=failure_reason
        )

    def _record_result(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        *,
        metrics: dict[str, object] | None,
        failure_reason: str | None,
    ) -> ExperimentRecord:
        row = self._conn.execute(
            "SELECT * FROM experiments WHERE experiment_id = ?", (experiment_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown experiment {experiment_id}")
        current = ExperimentStatus(row["status"])
        if current in _TERMINAL:
            raise ValueError(f"cannot transition terminal record {experiment_id} ({current.value})")
        if status is ExperimentStatus.RUNNING:
            raise ValueError(f"cannot reopen {experiment_id} to running")
        if status not in _TERMINAL:
            raise ValueError(f"unknown terminal status {status.value}")
        stored_metrics = metrics if metrics is not None else json.loads(row["metrics"])
        stored = ExperimentRecord.from_dict(
            {
                "experiment_id": row["experiment_id"],
                "created_at": row["created_at"],
                "hypothesis": row["hypothesis"],
                "dataset_id": row["dataset_id"],
                "dataset_version": row["dataset_version"],
                "group": row["group_kind"],
                "scorer_name": row["scorer_name"],
                "features": json.loads(row["features"]),
                "label_definition": json.loads(row["label_definition"]),
                "cost_model": json.loads(row["cost_model"]),
                "metrics": stored_metrics,
                "status": status.value,
                "parent_experiment_id": row["parent_experiment_id"],
                "failure_reason": failure_reason,
            }
        )
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                UPDATE experiments
                SET status = ?, metrics = ?, failure_reason = ?, payload = ?
                WHERE experiment_id = ?
                """,
                (
                    status.value,
                    json.dumps(stored.metrics, sort_keys=True),
                    failure_reason,
                    json.dumps(stored.as_dict(), sort_keys=True),
                    experiment_id,
                ),
            )
        return stored

    def claim_final_test(self, dataset_id: str) -> bool:
        if not dataset_id:
            raise ValueError("dataset_id must be non-empty")
        inserted = False
        with self._db.lock, self._conn:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO final_test_claims (dataset_id, claimed_at)
                VALUES (?, ?)
                """,
                (dataset_id, _iso(datetime.now(UTC))),
            )
            inserted = cur.rowcount == 1
        return inserted

    def is_final_test(self, dataset_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM final_test_claims WHERE dataset_id = ?", (dataset_id,)
        ).fetchone()
        return row is not None


def _record_row(record: ExperimentRecord) -> tuple[object, ...]:
    return (
        record.experiment_id,
        _iso(record.created_at),
        record.hypothesis,
        record.dataset_id,
        record.dataset_version,
        record.group.value,
        record.status.value,
        record.scorer_name,
        json.dumps(list(record.features), sort_keys=True),
        json.dumps(record.label_definition, sort_keys=True),
        json.dumps(record.cost_model, sort_keys=True),
        json.dumps(record.metrics, sort_keys=True),
        record.parent_experiment_id,
        record.failure_reason,
        json.dumps(record.as_dict(), sort_keys=True),
    )


def _row_to_record(row: sqlite3.Row) -> ExperimentRecord:
    record = ExperimentRecord.from_dict(
        {
            "experiment_id": row["experiment_id"],
            "created_at": row["created_at"],
            "hypothesis": row["hypothesis"],
            "dataset_id": row["dataset_id"],
            "dataset_version": row["dataset_version"],
            "group": row["group_kind"],
            "scorer_name": row["scorer_name"],
            "features": json.loads(row["features"]),
            "label_definition": json.loads(row["label_definition"]),
            "cost_model": json.loads(row["cost_model"]),
            "metrics": json.loads(row["metrics"]),
            "status": row["status"],
            "parent_experiment_id": row["parent_experiment_id"],
            "failure_reason": row["failure_reason"],
        }
    )
    return record
