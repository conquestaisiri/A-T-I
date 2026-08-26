# backend/infrastructure/sqlite/passport_repository.py
"""SQLite persistence for strategy passports (task P5-003b).

Passports are immutable facts and lifecycle events are appended: saving over
an existing passport id raises; replacing a snapshot requires the passport to
exist (the evidence engine records a lifecycle event first, then replaces).
The payload column stores the full ``StrategyPassport.as_dict()`` JSON so the
record round-trips exactly.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from backend.application.interfaces.passport_store import PassportStore
from backend.domain.research.passport import (
    PassportLifecycleEvent,
    PassportStatus,
    StrategyPassport,
)
from backend.infrastructure.sqlite.database import Database


class SqlitePassportRepository(PassportStore):
    """``PassportStore`` backed by the shared SQLite ``Database``."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @property
    def _conn(self) -> sqlite3.Connection:
        return self._db.connection

    def save_passport(self, passport: StrategyPassport) -> None:
        existing = self._conn.execute(
            "SELECT passport_id FROM strategy_passports WHERE passport_id = ?",
            (passport.passport_id,),
        ).fetchone()
        if existing is not None:
            raise ValueError(
                f"passport {passport.passport_id!r} already exists: "
                "records are immutable; express updates as lifecycle events"
            )
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO strategy_passports (
                    passport_id, created_at, hypothesis, dataset_id,
                    dataset_version, model, status, verdict, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    passport.passport_id,
                    passport.created_at.isoformat(timespec="milliseconds"),
                    passport.hypothesis,
                    passport.dataset_id,
                    passport.dataset_version,
                    passport.model,
                    passport.status.value,
                    passport.verdict.verdict.value,
                    json.dumps(passport.as_dict(), sort_keys=True),
                ),
            )

    def load_passport(self, passport_id: str) -> StrategyPassport | None:
        row = self._conn.execute(
            "SELECT payload FROM strategy_passports WHERE passport_id = ?",
            (passport_id,),
        ).fetchone()
        if row is None:
            return None
        return StrategyPassport.from_dict(json.loads(row["payload"]))

    def replace_passport(self, passport: StrategyPassport) -> None:
        existing = self._conn.execute(
            "SELECT passport_id FROM strategy_passports WHERE passport_id = ?",
            (passport.passport_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(f"cannot replace unknown passport {passport.passport_id!r}")
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                UPDATE strategy_passports
                SET status = ?, verdict = ?, payload = ?
                WHERE passport_id = ?
                """,
                (
                    passport.status.value,
                    passport.verdict.verdict.value,
                    json.dumps(passport.as_dict(), sort_keys=True),
                    passport.passport_id,
                ),
            )

    def append_lifecycle_event(self, event: PassportLifecycleEvent) -> None:
        existing = self._conn.execute(
            "SELECT passport_id FROM strategy_passports WHERE passport_id = ?",
            (event.passport_id,),
        ).fetchone()
        if existing is None:
            raise ValueError(
                f"cannot append lifecycle event to unknown passport {event.passport_id!r}"
            )
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO passport_lifecycle_events (
                    passport_id, event_type, occurred_at, from_status,
                    to_status, reason, payload
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.passport_id,
                    event.event_type,
                    event.occurred_at.isoformat(timespec="milliseconds"),
                    event.from_status.value if event.from_status else None,
                    event.to_status.value if event.to_status else None,
                    event.reason,
                    json.dumps(event.as_dict(), sort_keys=True),
                ),
            )

    def lifecycle(self, passport_id: str) -> tuple[PassportLifecycleEvent, ...]:
        rows = self._conn.execute(
            """
            SELECT payload FROM passport_lifecycle_events
            WHERE passport_id = ?
            ORDER BY id ASC
            """,
            (passport_id,),
        ).fetchall()
        return tuple(_lifecycle_from_dict(json.loads(row["payload"])) for row in rows)

    def all_passports(self) -> tuple[StrategyPassport, ...]:
        rows = self._conn.execute(
            "SELECT payload FROM strategy_passports ORDER BY id ASC"
        ).fetchall()
        return tuple(StrategyPassport.from_dict(json.loads(row["payload"])) for row in rows)


def _lifecycle_from_dict(data: dict[str, Any]) -> PassportLifecycleEvent:
    return PassportLifecycleEvent(
        passport_id=str(data["passport_id"]),
        event_type=str(data["event_type"]),
        occurred_at=datetime.fromisoformat(str(data["occurred_at"])),
        from_status=(
            PassportStatus(str(data["from_status"]))
            if data.get("from_status") is not None
            else None
        ),
        to_status=(
            PassportStatus(str(data["to_status"])) if data.get("to_status") is not None else None
        ),
        reason=str(data.get("reason") or ""),
    )
