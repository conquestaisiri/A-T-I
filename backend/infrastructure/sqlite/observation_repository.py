# backend/infrastructure/sqlite/observation_repository.py
"""SQLite implementation of the ObservationRepository port."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.application.interfaces.observation_repository import ObservationRepository
from backend.domain.observation.event import ObservationEvent
from backend.infrastructure.sqlite.database import Database


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string with millisecond precision."""
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class SqliteObservationRepository(ObservationRepository):
    """Persists normalized observation events with at-least-once semantics.

    At-least-once is implemented via a UNIQUE ``event_key``: replays of the
    same market event insert no row and report ``False``, so re-delivery
    never produces duplicates.
    """

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def save(self, event: ObservationEvent) -> bool:
        symbol = event.payload.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("ObservationEvent payload missing required 'symbol' string field")

        row = (
            event.event_key,
            event.source_id,
            event.source_name,
            event.event_type.value,
            symbol,
            event.timestamp.isoformat(timespec="milliseconds"),
            event.model_dump_json(),
            _utc_now_iso(),
        )
        try:
            with self._db.lock, self._conn:
                self._conn.execute(
                    """
                    INSERT INTO observation_events
                        (event_key, source_id, source_name, event_type, symbol,
                         event_time, payload, ingested_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
            return True
        except Exception:
            # UNIQUE constraint violation on replay is expected and benign.
            return False

    def find_recent(self, symbol: str, limit: int = 20) -> list[ObservationEvent]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self._conn.execute(
            """
            SELECT payload FROM observation_events
            WHERE symbol = ?
            ORDER BY event_time DESC, id DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        events = [ObservationEvent.model_validate_json(row["payload"]) for row in rows]
        # Return in chronological order for deterministic downstream use.
        events.reverse()
        return events

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM observation_events").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM observation_events WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return int(row["n"])
