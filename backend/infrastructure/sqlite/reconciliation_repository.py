# backend/infrastructure/sqlite/reconciliation_repository.py
"""SQLite persistence for reconciliation reports (P0-012 follow-up).

The ``ReconciliationReport.as_dict()`` public view is a summary; this module
keeps its own lossless JSON round-trip (venue, internal, discrepancies) so a
report survives the process that produced it intact.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from backend.application.interfaces.reconciliation_store import ReconciliationStore
from backend.domain.execution.order import OrderSide
from backend.domain.execution.position import Position
from backend.domain.execution.reconciliation import (
    DiscrepancyKind,
    PositionDiscrepancy,
    ReconciliationReport,
    VenuePosition,
)
from backend.infrastructure.sqlite.database import Database

_DISCREPANCY_KINDS = {kind.value: kind for kind in DiscrepancyKind}


def _dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat(timespec="milliseconds")


def _iso_to_dt(value: str | None) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(UTC)


def _venue_to_dict(venue: VenuePosition | None) -> dict[str, Any] | None:
    if venue is None:
        return None
    return {
        "symbol": venue.symbol,
        "side": venue.side.value,
        "quantity": venue.quantity,
        "average_entry_price": venue.average_entry_price,
        "reported_at": _dt_to_iso(venue.reported_at),
    }


def _venue_from_dict(data: dict[str, Any] | None) -> VenuePosition | None:
    if data is None:
        return None
    return VenuePosition(
        symbol=str(data["symbol"]),
        side=OrderSide(str(data["side"])),
        quantity=float(data["quantity"]),
        average_entry_price=(
            float(data["average_entry_price"])
            if data.get("average_entry_price") is not None
            else None
        ),
        reported_at=_iso_to_dt(data.get("reported_at")),
    )


def _position_to_dict(position: Position | None) -> dict[str, Any] | None:
    if position is None:
        return None
    return {
        "symbol": position.symbol,
        "side": position.side.value,
        "quantity": position.quantity,
        "average_entry_price": position.average_entry_price,
        "opened_at": _dt_to_iso(position.opened_at),
        "stop_loss_price": position.stop_loss_price,
        "take_profit_price": position.take_profit_price,
    }


def _position_from_dict(data: dict[str, Any] | None) -> Position | None:
    if data is None:
        return None
    return Position(
        symbol=str(data["symbol"]),
        side=OrderSide(str(data["side"])),
        quantity=float(data["quantity"]),
        average_entry_price=float(data["average_entry_price"]),
        opened_at=_iso_to_dt(data.get("opened_at")) or datetime(1970, 1, 1, tzinfo=UTC),
        stop_loss_price=(
            float(data["stop_loss_price"]) if data.get("stop_loss_price") is not None else None
        ),
        take_profit_price=(
            float(data["take_profit_price"]) if data.get("take_profit_price") is not None else None
        ),
    )


def _discrepancies_from_data(data: list[Any]) -> tuple[PositionDiscrepancy, ...]:
    out: list[PositionDiscrepancy] = []
    for item in data:
        kind = _DISCREPANCY_KINDS.get(str(item.get("kind")))
        if kind is None:
            raise ValueError(f"Unknown discrepancy kind: {item.get('kind')}")
        out.append(
            PositionDiscrepancy(
                symbol=str(item["symbol"]),
                kind=kind,
                venue_signed=(
                    float(item["venue_signed"]) if item.get("venue_signed") is not None else None
                ),
                internal_signed=(
                    float(item["internal_signed"])
                    if item.get("internal_signed") is not None
                    else None
                ),
                detail=str(item.get("detail", "")),
            )
        )
    return tuple(out)


def _report_round_trip(report: ReconciliationReport) -> dict[str, Any]:
    return {
        "symbol": report.symbol,
        "reconciled_at": _dt_to_iso(report.reconciled_at),
        "venue_position": _venue_to_dict(report.venue_position),
        "internal_position": _position_to_dict(report.internal_position),
        "discrepancies": [d.as_dict() for d in report.discrepancies],
    }


def _report_from_round_trip(data: dict[str, Any]) -> ReconciliationReport:
    return ReconciliationReport(
        symbol=str(data["symbol"]),
        venue_position=_venue_from_dict(data.get("venue_position")),
        internal_position=_position_from_dict(data.get("internal_position")),
        discrepancies=_discrepancies_from_data(data.get("discrepancies", [])),
        reconciled_at=_iso_to_dt(data.get("reconciled_at")),
    )


class SqliteReconciliationRepository(ReconciliationStore):
    """Stores reconciliation reports in the durable SQLite store."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._conn = database.connection

    def save_report(self, report: ReconciliationReport) -> None:
        with self._db.lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO reconciliation_reports
                    (symbol, reconciled_at, consistent, discrepancy_count,
                     venue_signed, internal_signed, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report.symbol,
                    _dt_to_iso(report.reconciled_at) or "1970-01-01T00:00:00.000",
                    int(report.consistent),
                    len(report.discrepancies),
                    (
                        report.venue_position.signed_quantity
                        if report.venue_position is not None
                        else None
                    ),
                    (
                        report.internal_position.signed_quantity
                        if report.internal_position is not None
                        else None
                    ),
                    json.dumps(_report_round_trip(report)),
                ),
            )

    def recent_reports(
        self, *, symbol: str | None = None, limit: int = 20
    ) -> list[ReconciliationReport]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        if symbol is not None:
            rows = self._conn.execute(
                """
                SELECT payload FROM reconciliation_reports
                WHERE symbol = ?
                ORDER BY reconciled_at DESC, id DESC
                LIMIT ?
                """,
                (symbol, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT payload FROM reconciliation_reports
                ORDER BY reconciled_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_report_from_round_trip(json.loads(row["payload"])) for row in rows]

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM reconciliation_reports").fetchone()
        else:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM reconciliation_reports WHERE symbol = ?",
                (symbol,),
            ).fetchone()
        return int(row["n"])
