# backend/infrastructure/sqlite/macro_event_repository.py
"""Durable store for economic-calendar events.

Upsert-by-event_id keeps the weekly feed idempotent across polls; the
SCHEDULED -> RELEASED transition is detected by comparing stored vs incoming
``actual`` so downstream consumers (reaction research) see exactly one
release moment per event.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from backend.domain.macro.event import MacroEventData
from backend.infrastructure.sqlite.database import Database


class SqliteMacroEventRepository:
    """SQLite-backed calendar store (single-writer discipline)."""

    def __init__(self, database: Database) -> None:
        self._db = database

    def upsert(self, event: MacroEventData, *, seen_at: datetime) -> str | None:
        """Insert or update one event.

        Returns ``"released"`` when this call *transitioned* the event from
        not-released to released (the single release signal), ``"updated"``
        for other content changes, ``"unchanged"`` otherwise.
        """
        previous_actual = self.get(event.event_id)
        payload = json.dumps(event.as_dict(), sort_keys=True)
        with self._db.lock, self._db.connection as conn:
            conn.execute(
                """
                INSERT INTO macro_events (
                    event_id, currency, title, scheduled_at, impact,
                    forecast, previous, actual, status,
                    first_seen_at, released_detected_at, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(event_id) DO UPDATE SET
                    forecast = excluded.forecast,
                    previous = excluded.previous,
                    actual = excluded.actual,
                    status = excluded.status,
                    released_detected_at = COALESCE(
                        macro_events.released_detected_at,
                        excluded.released_detected_at
                    ),
                    payload = excluded.payload
                """,
                (
                    event.event_id,
                    event.currency,
                    event.title,
                    event.scheduled_at.isoformat(),
                    event.impact,
                    event.forecast,
                    event.previous,
                    event.actual,
                    "RELEASED" if event.released else "SCHEDULED",
                    seen_at.isoformat(),
                    seen_at.isoformat() if event.released else None,
                    payload,
                ),
            )
        if previous_actual is None:
            return "updated"
        if previous_actual.actual is None and event.actual is not None:
            return "released"
        return "unchanged"

    def get(self, event_id: str) -> MacroEventData | None:
        row = self._db.connection.execute(
            "SELECT payload FROM macro_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return MacroEventData.from_dict(json.loads(str(row["payload"])))

    def list_between(
        self, start: datetime, end: datetime, *, impacts: set[str] | None = None
    ) -> list[MacroEventData]:
        """Events scheduled in [start, end], optionally filtered by impact."""
        sql = "SELECT payload FROM macro_events WHERE scheduled_at >= ? AND scheduled_at <= ?"
        params: list[Any] = [start.isoformat(), end.isoformat()]
        if impacts:
            marks = ",".join("?" for _ in impacts)
            sql += f" AND impact IN ({marks})"
            params.extend(sorted(impacts))
        rows = self._db.connection.execute(sql, params).fetchall()
        events = [MacroEventData.from_dict(json.loads(str(r["payload"]))) for r in rows]
        return sorted(events, key=lambda e: e.scheduled_at)

    def next_high_impact_for_currencies(
        self,
        currencies: set[str],
        *,
        now: datetime,
        within_minutes: int,
    ) -> MacroEventData | None:
        """Nearest upcoming High-impact event for any of ``currencies``.

        Looks forward ``within_minutes`` from ``now``; used pre-trade to
        stand aside ahead of market-moving releases.
        """
        if not currencies:
            return None
        horizon = datetime.fromtimestamp(now.timestamp() + within_minutes * 60, tz=now.tzinfo)
        marks = ",".join("?" for _ in currencies)
        row = self._db.connection.execute(
            f"""
            SELECT payload FROM macro_events
            WHERE impact = 'High'
              AND actual IS NULL
              AND currency IN ({marks})
              AND scheduled_at >= ?
              AND scheduled_at <= ?
            ORDER BY scheduled_at ASC LIMIT 1
            """,
            (*sorted(currencies), now.isoformat(), horizon.isoformat()),
        ).fetchone()
        if row is None:
            return None
        return MacroEventData.from_dict(json.loads(str(row["payload"])))

    def recent_released(self, limit: int = 100) -> list[MacroEventData]:
        """Most recently released events (reaction-research entry point)."""
        rows = self._db.connection.execute(
            """
            SELECT payload FROM macro_events
            WHERE actual IS NOT NULL
            ORDER BY released_detected_at DESC LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
        events = [MacroEventData.from_dict(json.loads(str(r["payload"]))) for r in rows]
        return events
