# backend/infrastructure/sqlite/proposal_repository.py
"""SQLite implementation of the ProposalRepository port."""

from __future__ import annotations

import json

from backend.application.interfaces.proposal_repository import ProposalRepository
from backend.domain.decision.proposal import DecisionProposal
from backend.infrastructure.sqlite.database import Database


class SqliteProposalRepository(ProposalRepository):
    """Persists decision proposals with at-least-once semantics via UNIQUE id."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def save(self, proposal: DecisionProposal) -> None:
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO decision_proposals
                    (proposal_id, correlation_id, symbol, created_at, confidence, payload)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(proposal_id) DO NOTHING
                """,
                (
                    proposal.proposal_id,
                    proposal.correlation_id,
                    proposal.symbol,
                    proposal.created_at.isoformat(timespec="milliseconds"),
                    proposal.confidence,
                    json.dumps(proposal.as_dict()),
                ),
            )

    def find_by_id(self, proposal_id: str) -> DecisionProposal | None:
        row = self._conn.execute(
            "SELECT payload FROM decision_proposals WHERE proposal_id = ?",
            (proposal_id,),
        ).fetchone()
        if row is None:
            return None
        return DecisionProposal.from_dict(json.loads(row["payload"]))

    def find_recent(self, symbol: str, limit: int = 20) -> list[DecisionProposal]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self._conn.execute(
            """
            SELECT payload FROM decision_proposals
            WHERE symbol = ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        proposals = [DecisionProposal.from_dict(json.loads(row["payload"])) for row in rows]
        proposals.reverse()
        return proposals

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM decision_proposals").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM decision_proposals WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return int(row["n"])
