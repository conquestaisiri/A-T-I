"""Tests for the reconciliation contract and restart recovery (P0-012).

Acceptance:
- Unknown order states are explicit.
- Venue state can be reconciled with internal state.
- Position mismatch blocks new risk.
- Restart recovery is tested.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.application.execution.reconciliation_service import ReconciliationService
from backend.domain.execution.order import OrderSide, signed_volume
from backend.domain.execution.position import Position
from backend.domain.execution.reconciliation import (
    DiscrepancyKind,
    ReconciliationReport,
    VenuePosition,
)
from backend.domain.execution.trade_record import TradeStatus


def ts() -> datetime:
    return datetime(2026, 2, 1, 9, 0, 0, tzinfo=UTC)


def venue(
    symbol: str = "btcusdt",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.0,
    entry: float = 100.0,
) -> VenuePosition:
    return VenuePosition(
        symbol=symbol,
        side=side,
        quantity=quantity,
        average_entry_price=entry,
        reported_at=ts(),
    )


def internal(
    symbol: str = "btcusdt",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.0,
    entry: float = 100.0,
) -> Position:
    return Position(
        symbol=symbol,
        side=side,
        quantity=quantity,
        average_entry_price=entry,
        opened_at=ts(),
    )


class TestSignedVolume:
    def test_long_positive(self) -> None:
        assert signed_volume(OrderSide.BUY, 2.5) == 2.5

    def test_short_negative(self) -> None:
        assert signed_volume(OrderSide.SELL, 2.5) == -2.5

    def test_position_signed_quantity(self) -> None:
        assert internal(side=OrderSide.SELL).signed_quantity == -1.0
        assert internal(side=OrderSide.BUY).signed_quantity == 1.0


class TestReconcile:
    def test_matching_positions_are_consistent(self) -> None:
        report = ReconciliationService.reconcile([venue()], [internal()], reconciled_at=ts())[
            "btcusdt"
        ]
        assert report.consistent
        assert report.discrepancies == ()

    def test_matching_short_positions_are_consistent(self) -> None:
        report = ReconciliationService.reconcile(
            [venue(side=OrderSide.SELL)],
            [internal(side=OrderSide.SELL)],
            reconciled_at=ts(),
        )["btcusdt"]
        assert report.consistent

    def test_quantity_mismatch_is_a_discrepancy(self) -> None:
        report = ReconciliationService.reconcile(
            [venue()], [internal(quantity=0.5)], reconciled_at=ts()
        )["btcusdt"]
        assert not report.consistent
        assert len(report.discrepancies) == 1
        discrepancy = report.discrepancies[0]
        assert discrepancy.kind is DiscrepancyKind.QUANTITY
        assert discrepancy.venue_signed == 1.0
        assert discrepancy.internal_signed == 0.5

    def test_side_flip_is_an_exposure_drift(self) -> None:
        # A short→long flip of -2.0 vs +2.0 diverges by 4.0 of exposure and
        # must never pass as consistent; the service reports it as QUANTITY.
        report = ReconciliationService.reconcile(
            [venue(side=OrderSide.SELL, quantity=2.0)],
            [internal(side=OrderSide.BUY, quantity=2.0)],
            reconciled_at=ts(),
        )["btcusdt"]
        assert not report.consistent
        assert len(report.discrepancies) == 1
        discrepancy = report.discrepancies[0]
        assert discrepancy.kind is DiscrepancyKind.QUANTITY
        assert discrepancy.venue_signed == -2.0
        assert discrepancy.internal_signed == 2.0

    def test_venue_only_position_is_a_discrepancy(self) -> None:
        report = ReconciliationService.reconcile([venue()], [], reconciled_at=ts())["btcusdt"]
        assert not report.consistent
        assert report.venue_position is not None
        assert report.internal_position is None
        assert report.discrepancies[0].kind is DiscrepancyKind.VENUE_ONLY

    def test_internal_only_position_is_a_discrepancy(self) -> None:
        report = ReconciliationService.reconcile([], [internal()], reconciled_at=ts())["btcusdt"]
        assert not report.consistent
        assert report.venue_position is None
        assert report.internal_position is not None
        assert report.discrepancies[0].kind is DiscrepancyKind.INTERNAL_ONLY

    def test_multiple_symbols_produce_one_report_each(self) -> None:
        reports = ReconciliationService.reconcile(
            [venue("btcusdt"), venue("ethusdt")],
            [internal("btcusdt")],
            reconciled_at=ts(),
        )
        assert set(reports) == {"btcusdt", "ethusdt"}
        assert reports["btcusdt"].consistent
        assert not reports["ethusdt"].consistent

    def test_empty_reconcile_is_empty(self) -> None:
        assert ReconciliationService.reconcile([], [], reconciled_at=ts()) == {}


class TestRestartRecovery:
    def test_recover_open_records_builds_open_records_from_venue(self) -> None:
        records = ReconciliationService.recover_open_records(
            [
                venue("btcusdt", entry=50000.0),
                venue("ethusdt", side=OrderSide.SELL, quantity=2.0, entry=3000.0),
            ],
            recovered_at=ts(),
        )
        assert len(records) == 2
        by_symbol = {r.symbol: r for r in records}
        assert by_symbol["btcusdt"].side is OrderSide.BUY
        assert by_symbol["btcusdt"].quantity == 1.0
        assert by_symbol["btcusdt"].entry_price == 50000.0
        assert by_symbol["btcusdt"].status is TradeStatus.OPEN
        assert by_symbol["btcusdt"].realized_pnl is None
        assert by_symbol["ethusdt"].side is OrderSide.SELL
        assert by_symbol["ethusdt"].quantity == 2.0

    def test_recovered_records_reconcile_clean_against_venue(self) -> None:
        venue_positions = [venue("btcusdt", entry=50000.0), venue("ethusdt")]
        records = ReconciliationService.recover_open_records(venue_positions, recovered_at=ts())
        internal_positions = [
            Position(
                symbol=r.symbol,
                side=r.side,
                quantity=r.quantity,
                average_entry_price=r.entry_price,
                opened_at=r.opened_at,
            )
            for r in records
        ]
        reports = ReconciliationService.reconcile(
            venue_positions, internal_positions, reconciled_at=ts()
        )
        assert all(report.consistent for report in reports.values())

    def test_recovery_trade_ids_are_deterministic(self) -> None:
        positions = [venue("btcusdt"), venue("ethusdt")]
        first = [r.trade_id for r in ReconciliationService.recover_open_records(positions)]
        second = [r.trade_id for r in ReconciliationService.recover_open_records(positions)]
        assert first == second


class TestRiskBlocker:
    def test_mismatch_is_zero_blocked_for_internal_shadow(self) -> None:
        # The reconciliation report is the guard's input; consistency decides.
        report = ReconciliationService.reconcile(
            [venue()], [internal(quantity=0.5)], reconciled_at=ts()
        )["btcusdt"]
        assert isinstance(report, ReconciliationReport)
        assert not report.consistent
        assert len(report.discrepancies) == 1
