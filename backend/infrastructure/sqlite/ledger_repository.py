# backend/infrastructure/sqlite/ledger_repository.py
"""SQLite implementation of the LedgerRepository port."""

from __future__ import annotations

import json

from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.domain.execution.trade_record import TradeRecord
from backend.infrastructure.sqlite.database import Database


class SqliteLedgerRepository(LedgerRepository):
    """Persists trade records in the durable outcome ledger."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def save(self, record: TradeRecord) -> None:
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO trade_ledger
                    (trade_id, proposal_id, correlation_id, symbol, side, status,
                     opened_at, closed_at, realized_pnl, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(trade_id) DO UPDATE SET
                    status = excluded.status,
                    closed_at = excluded.closed_at,
                    realized_pnl = excluded.realized_pnl,
                    payload = excluded.payload
                """,
                (
                    record.trade_id,
                    record.proposal_id or "",
                    record.correlation_id or "",
                    record.symbol,
                    record.side.value,
                    record.status.value,
                    record.opened_at.isoformat(timespec="milliseconds"),
                    record.closed_at.isoformat(timespec="milliseconds")
                    if record.closed_at is not None
                    else None,
                    record.realized_pnl,
                    json.dumps(record.as_dict()),
                ),
            )

    def find_by_id(self, trade_id: str) -> TradeRecord | None:
        row = self._conn.execute(
            "SELECT payload FROM trade_ledger WHERE trade_id = ?",
            (trade_id,),
        ).fetchone()
        if row is None:
            return None
        return TradeRecord.from_dict(json.loads(row["payload"]))

    def find_recent(self, symbol: str, limit: int = 20) -> list[TradeRecord]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self._conn.execute(
            """
            SELECT payload FROM trade_ledger
            WHERE symbol = ?
            ORDER BY opened_at DESC, id DESC
            LIMIT ?
            """,
            (symbol, limit),
        ).fetchall()
        records = [TradeRecord.from_dict(json.loads(row["payload"])) for row in rows]
        records.reverse()
        return records

    def open_trades(self) -> list[TradeRecord]:
        rows = self._conn.execute(
            "SELECT payload FROM trade_ledger WHERE status = 'open'"
        ).fetchall()
        return [TradeRecord.from_dict(json.loads(row["payload"])) for row in rows]

    def closed_trades(self, limit: int = 100) -> list[TradeRecord]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        rows = self._conn.execute(
            """
            SELECT payload FROM trade_ledger
            WHERE status = 'closed'
            ORDER BY closed_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        records = [TradeRecord.from_dict(json.loads(row["payload"])) for row in rows]
        records.reverse()
        return records

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM trade_ledger").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM trade_ledger WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return int(row["n"])
