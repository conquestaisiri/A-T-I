# backend/infrastructure/sqlite/memory_repository.py
"""SQLite implementation of the MemoryStore port (Constitution Document 05)."""

from __future__ import annotations

import json

from backend.application.interfaces.memory_store import MemoryStore
from backend.domain.memory.episode import MemoryEpisode
from backend.infrastructure.sqlite.database import Database


class SqliteMemoryRepository(MemoryStore):
    """Persists episodic market memory with at-least-once semantics."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def record(self, episode: MemoryEpisode) -> None:
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memory_episodes
                    (episode_id, correlation_id, symbol, created_at, proposal_id,
                     action_type, confidence, outcome, realized_pnl, summary, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(episode_id) DO NOTHING
                """,
                (
                    episode.episode_id,
                    episode.correlation_id,
                    episode.symbol,
                    episode.created_at.isoformat(timespec="milliseconds"),
                    episode.proposal_id,
                    episode.action_type,
                    episode.confidence,
                    episode.outcome.value,
                    episode.realized_pnl,
                    episode.summary,
                    json.dumps(episode.as_dict()),
                ),
            )

    def recall(self, symbol: str, limit: int = 10) -> list[MemoryEpisode]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self._conn.execute(
            """
            SELECT * FROM memory_episodes
            WHERE symbol = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        episodes = [MemoryEpisode.from_dict(json.loads(row["payload"])) for row in rows]
        episodes.reverse()
        return episodes

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM memory_episodes").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM memory_episodes WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return int(row["n"])
