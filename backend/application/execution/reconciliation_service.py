# backend/application/execution/reconciliation_service.py
"""Order and position reconciliation service (P0-012).

The venue is the source of truth for market-exposed state. This service
compares venue-reported positions against the internally-tracked positions and
produces per-symbol :class:`ReconciliationReport` objects. It also provides
restart recovery: rebuilding internal records from venue truth after the system
was down, so the simulator and ledger agree with reality before any new risk is
taken.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

from backend.domain.execution.position import Position
from backend.domain.execution.reconciliation import (
    DiscrepancyKind,
    PositionDiscrepancy,
    ReconciliationReport,
    VenuePosition,
    signed_volume,
)
from backend.domain.execution.trade_record import TradeRecord

# Relative volume tolerance for float comparisons after round-trips.
_VOLUME_EPS = 1e-9


class ReconciliationService:
    """Pure comparison logic for venue vs internal position state."""

    @staticmethod
    def reconcile(
        venue_positions: list[VenuePosition],
        internal_positions: list[Position],
        *,
        reconciled_at: datetime | None = None,
    ) -> dict[str, ReconciliationReport]:
        """Compare venue and internal positions, one report per symbol.

        A report is produced for every symbol seen on either side. Two net
        positions are reconciled by comparing *signed* volume; a quiet
        side-flip therefore surfaces as a mismatch rather than cancelling out.
        Quantity also matters: equal directions with different volumes are a
        QUANTITY discrepancy (exposure drift).
        """
        venue_by_symbol: dict[str, VenuePosition] = {p.symbol: p for p in venue_positions}
        internal_by_symbol: dict[str, Position] = {p.symbol: p for p in internal_positions}
        symbols = sorted(set(venue_by_symbol) | set(internal_by_symbol))

        reports: dict[str, ReconciliationReport] = {}
        for symbol in symbols:
            venue = venue_by_symbol.get(symbol)
            internal = internal_by_symbol.get(symbol)
            reports[symbol] = ReconciliationService._reconcile_one(
                symbol, venue, internal, reconciled_at=reconciled_at
            )
        return reports

    @staticmethod
    def _reconcile_one(
        symbol: str,
        venue: VenuePosition | None,
        internal: Position | None,
        *,
        reconciled_at: datetime | None,
    ) -> ReconciliationReport:
        discrepancies: list[PositionDiscrepancy] = []

        if venue is None and internal is None:
            return ReconciliationReport(symbol, None, None, (), reconciled_at)

        if venue is None:
            discrepancies.append(
                PositionDiscrepancy(
                    symbol=symbol,
                    kind=DiscrepancyKind.INTERNAL_ONLY,
                    venue_signed=0.0,
                    internal_signed=internal.signed_quantity if internal else 0.0,
                    detail="Position tracked internally but absent at the venue.",
                )
            )
            return ReconciliationReport(symbol, None, internal, tuple(discrepancies), reconciled_at)

        if internal is None:
            discrepancies.append(
                PositionDiscrepancy(
                    symbol=symbol,
                    kind=DiscrepancyKind.VENUE_ONLY,
                    venue_signed=venue.signed_quantity,
                    internal_signed=0.0,
                    detail="Position exists at the venue but is not tracked internally.",
                )
            )
            return ReconciliationReport(symbol, venue, None, tuple(discrepancies), reconciled_at)

        venue_volume = venue.signed_quantity
        internal_volume = signed_volume(internal.side, internal.quantity)
        if _volume_mismatch(venue_volume, internal_volume):
            # Same direction but different size
            discrepancies.append(
                PositionDiscrepancy(
                    symbol=symbol,
                    kind=DiscrepancyKind.QUANTITY,
                    venue_signed=venue_volume,
                    internal_signed=internal_volume,
                    detail=(
                        "Direction agrees but net volume differs — exposure "
                        "drift between the venue ledger and internal records."
                    ),
                )
            )
        elif (venue.side is not internal.side) and not (
            _close_to_zero(venue_volume) or _close_to_zero(internal_volume)
        ):
            discrepancies.append(
                PositionDiscrepancy(
                    symbol=symbol,
                    kind=DiscrepancyKind.SIDE,
                    venue_signed=venue_volume,
                    internal_signed=internal_volume,
                    detail="Venue and internal state disagree on the position side.",
                )
            )

        return ReconciliationReport(symbol, venue, internal, tuple(discrepancies), reconciled_at)

    @staticmethod
    def recover_open_records(
        venue_positions: list[VenuePosition],
        *,
        recovered_at: datetime | None = None,
        fallback_open_time: datetime | None = None,
    ) -> list[TradeRecord]:
        """Rebuild open trade records from venue truth after a restart.

        Each venue position becomes an OPEN ledger record. This is the restart
        recovery path: after downtime, no internal position can be trusted; the
        only honest open state is whatever the venue holds.
        """
        timestamp = recovered_at or datetime.now(UTC)
        records: list[TradeRecord] = []
        for index, position in enumerate(sorted(venue_positions, key=lambda p: p.symbol)):
            records.append(
                TradeRecord.open(
                    trade_id=_recovery_trade_id(position.symbol, index),
                    proposal_id=None,
                    correlation_id=None,
                    symbol=position.symbol,
                    side=position.side,
                    quantity=position.quantity,
                    entry_price=position.average_entry_price or 0.0,
                    opened_at=fallback_open_time or timestamp,
                    fee=None,
                )
            )
        return records


def _volume_mismatch(a: float, b: float) -> bool:
    return not _close_to_zero(a - b)


def _close_to_zero(value: float) -> bool:
    return math.isclose(value, 0.0, abs_tol=_VOLUME_EPS)


def _recovery_trade_id(symbol: str, index: int) -> str:
    return f"recovered-{symbol.lower().replace('/', '-')}-{index}"
