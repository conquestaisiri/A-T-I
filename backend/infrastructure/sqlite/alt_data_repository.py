# backend/infrastructure/sqlite/alt_data_repository.py
"""SQLite implementation of the AltDataStore port (task P1-006)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from backend.application.interfaces.alt_data_store import AltDataStore
from backend.domain.research.alt_data import AltDataEvent, AltDataKind, AltDataSnapshot
from backend.infrastructure.sqlite.database import Database


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


class SqliteAltDataRepository(AltDataStore):
    """Stores immutable alt-data events and serves point-in-time snapshots."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def save_event(self, event: AltDataEvent) -> None:
        if event.published_at < event.event_timestamp:
            raise ValueError(
                f"event {event.event_id} published before its event time (impossible clock order)"
            )
        if not event.event_id:
            raise ValueError("event_id must be non-empty")
        try:
            with self._db.lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO alt_data_events
                        (event_id, symbol, kind, event_time, published_at, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event.event_id,
                        event.symbol,
                        event.kind.value,
                        _iso(event.event_timestamp),
                        _iso(event.published_at),
                        json.dumps(event.payload, sort_keys=True),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(
                f"alt-data event {event.event_id} already exists (immutable events)"
            ) from exc

    def snapshot_at(
        self,
        cutoff: datetime,
        *,
        symbol: str | None = None,
        kind: AltDataKind | None = None,
    ) -> AltDataSnapshot:
        query = "SELECT * FROM alt_data_events WHERE published_at <= ?"
        params: list[object] = [_iso(cutoff)]
        if symbol is not None:
            query += " AND symbol = ?"
            params.append(symbol)
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind.value)
        query += " ORDER BY published_at, event_time, id"
        rows = self._conn.execute(query, params).fetchall()
        events = [
            AltDataEvent(
                event_id=row["event_id"],
                symbol=row["symbol"],
                kind=AltDataKind(row["kind"]),
                event_timestamp=_parse(row["event_time"]),
                published_at=_parse(row["published_at"]),
                payload=json.loads(row["payload"]),
            )
            for row in rows
        ]
        return AltDataSnapshot(cutoff=cutoff, events=tuple(events))

    def event_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS n FROM alt_data_events").fetchone()
        return int(row["n"]) if row is not None else 0
