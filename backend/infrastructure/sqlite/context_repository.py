# backend/infrastructure/sqlite/context_repository.py
"""SQLite implementation of the ContextRepository port."""

from __future__ import annotations

import json

from backend.application.interfaces.context_repository import ContextRepository
from backend.domain.context.market_context import MarketContext
from backend.infrastructure.sqlite.database import Database


class SqliteContextRepository(ContextRepository):
    """Persists produced :class:`MarketContext` snapshots for history/observability."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def save(self, context: MarketContext) -> None:
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO market_contexts
                    (symbol, created_at, snapshot_json, features_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    context.snapshot.symbol,
                    context.created_at.isoformat(timespec="milliseconds"),
                    json.dumps(context.snapshot.as_dict()),
                    json.dumps({name: feature.as_dict() for name, feature in context.features}),
                ),
            )

    def latest(self, symbol: str) -> MarketContext | None:
        row = self._conn.execute(
            """
            SELECT snapshot_json, features_json, created_at FROM market_contexts
            WHERE symbol = ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (symbol,),
        ).fetchone()
        if row is None:
            return None
        return MarketContext.from_dict(
            {
                "snapshot": json.loads(row["snapshot_json"]),
                "features": json.loads(row["features_json"]),
                "created_at": row["created_at"],
            }
        )

    def history(self, symbol: str, limit: int = 20) -> list[MarketContext]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self._conn.execute(
            """
            SELECT snapshot_json, features_json, created_at FROM market_contexts
            WHERE symbol = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        contexts = [
            MarketContext.from_dict(
                {
                    "snapshot": json.loads(row["snapshot_json"]),
                    "features": json.loads(row["features_json"]),
                    "created_at": row["created_at"],
                }
            )
            for row in rows
        ]
        contexts.reverse()
        return contexts
