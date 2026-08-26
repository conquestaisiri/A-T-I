"""Unit tests for the SQLite reconciliation repository (P0-012 follow-up).

The repository must persist every field of a report — venue truth, internal
state, and discrepancies — losslessly across a save/reload round-trip, because
``ReconciliationReport.as_dict()`` only exposes a summary view.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.domain.execution.order import OrderSide
from backend.domain.execution.position import Position
from backend.domain.execution.reconciliation import (
    DiscrepancyKind,
    PositionDiscrepancy,
    ReconciliationReport,
    VenuePosition,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.reconciliation_repository import (
    SqliteReconciliationRepository,
)


def ts() -> datetime:
    return datetime(2026, 3, 1, 10, 30, 0, tzinfo=UTC)


def venue(
    symbol: str = "btcusdt",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.5,
    entry: float = 99.25,
) -> VenuePosition:
    return VenuePosition(
        symbol=symbol,
        side=side,
        quantity=quantity,
        average_entry_price=entry,
        reported_at=ts(),
    )


def position(
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
        stop_loss_price=90.0,
        take_profit_price=110.0,
    )


def report(
    symbol: str = "btcusdt",
    *,
    consistent: bool = True,
) -> ReconciliationReport:
    discrepancies: tuple[PositionDiscrepancy, ...] = ()
    if not consistent:
        discrepancies = (
            PositionDiscrepancy(
                symbol=symbol,
                kind=DiscrepancyKind.QUANTITY,
                venue_signed=1.5,
                internal_signed=1.0,
                detail="Exposure drift.",
            ),
            PositionDiscrepancy(
                symbol=symbol,
                kind=DiscrepancyKind.SIDE,
                venue_signed=-1.0,
                internal_signed=1.0,
                detail="Side disagreement.",
            ),
        )
    return ReconciliationReport(
        symbol=symbol,
        venue_position=venue(symbol),
        internal_position=position(symbol),
        discrepancies=discrepancies,
        reconciled_at=ts(),
    )


@pytest.fixture
def repo(tmp_path) -> SqliteReconciliationRepository:
    return SqliteReconciliationRepository(Database(tmp_path / "test.db"))


class TestSaveReport:
    def test_empty_store_counts_zero(self, repo: SqliteReconciliationRepository) -> None:
        assert repo.count() == 0

    def test_save_inserts_and_counts(self, repo: SqliteReconciliationRepository) -> None:
        assert repo.count() == 0
        repo.save_report(report())
        assert repo.count() == 1

    def test_save_is_symbol_scoped(self, repo: SqliteReconciliationRepository) -> None:
        repo.save_report(report(symbol="btcusdt"))
        repo.save_report(report(symbol="ethusdt"))
        assert repo.count() == 2
        assert repo.count("btcusdt") == 1
        assert repo.count("ethusdt") == 1


class TestRoundTrip:
    def test_consistent_report_round_trips(self, repo: SqliteReconciliationRepository) -> None:
        original = report()
        repo.save_report(original)
        [reloaded] = repo.recent_reports(limit=1)
        assert reloaded.symbol == original.symbol
        assert reloaded.consistent
        assert reloaded.venue_position == original.venue_position
        assert reloaded.internal_position == original.internal_position
        assert reloaded.reconciled_at == original.reconciled_at

    def test_inconsistent_report_keeps_all_discrepancies(
        self, repo: SqliteReconciliationRepository
    ) -> None:
        original = report(consistent=False)
        repo.save_report(original)
        [reloaded] = repo.recent_reports(limit=1)
        assert not reloaded.consistent
        assert len(reloaded.discrepancies) == 2
        first, second = reloaded.discrepancies
        assert (first.kind, first.venue_signed, first.internal_signed) == (
            DiscrepancyKind.QUANTITY,
            1.5,
            1.0,
        )
        assert (second.kind, second.venue_signed, second.internal_signed) == (
            DiscrepancyKind.SIDE,
            -1.0,
            1.0,
        )
        assert first.detail == "Exposure drift."

    def test_venue_only_report_round_trips(self, repo: SqliteReconciliationRepository) -> None:
        only_venue = ReconciliationReport(
            symbol="btcusdt",
            venue_position=venue(),
            internal_position=None,
            discrepancies=(),
            reconciled_at=ts(),
        )
        repo.save_report(only_venue)
        [reloaded] = repo.recent_reports(limit=1)
        assert reloaded.venue_position == only_venue.venue_position
        assert reloaded.internal_position is None

    def test_internal_only_report_round_trips(self, repo: SqliteReconciliationRepository) -> None:
        only_internal = ReconciliationReport(
            symbol="btcusdt",
            venue_position=None,
            internal_position=position(),
            discrepancies=(),
            reconciled_at=ts(),
        )
        repo.save_report(only_internal)
        [reloaded] = repo.recent_reports(limit=1)
        assert reloaded.venue_position is None
        assert reloaded.internal_position == only_internal.internal_position

    def test_average_entry_price_none_survives(self, repo: SqliteReconciliationRepository) -> None:
        no_entry = ReconciliationReport(
            symbol="btcusdt",
            venue_position=VenuePosition(
                symbol="btcusdt",
                side=OrderSide.BUY,
                quantity=1.5,
                average_entry_price=None,
                reported_at=ts(),
            ),
            internal_position=position(),
            discrepancies=(),
            reconciled_at=ts(),
        )
        repo.save_report(no_entry)
        [reloaded] = repo.recent_reports(limit=1)
        assert reloaded.venue_position is not None
        assert reloaded.venue_position.average_entry_price is None


class TestRecentReports:
    def test_newest_first_order(self, repo: SqliteReconciliationRepository) -> None:
        for index in range(3):
            repo.save_report(
                ReconciliationReport(
                    symbol=f"sym{index}",
                    venue_position=None,
                    internal_position=None,
                    discrepancies=(),
                    reconciled_at=datetime(2026, 3, 1, index + 1, tzinfo=UTC),
                )
            )
        reloaded = repo.recent_reports(limit=3)
        assert [r.symbol for r in reloaded] == ["sym2", "sym1", "sym0"]

    def test_filters_by_symbol(self, repo: SqliteReconciliationRepository) -> None:
        repo.save_report(report(symbol="btcusdt"))
        repo.save_report(report(symbol="ethusdt"))
        reloaded = repo.recent_reports(symbol="ethusdt")
        assert [r.symbol for r in reloaded] == ["ethusdt"]

    def test_respects_limit(self, repo: SqliteReconciliationRepository) -> None:
        for index in range(5):
            repo.save_report(report(symbol=f"sym{index}"))
        reloaded = repo.recent_reports(limit=2)
        assert len(reloaded) == 2

    def test_invalid_limit_raises(self, repo: SqliteReconciliationRepository) -> None:
        with pytest.raises(ValueError):
            repo.recent_reports(limit=0)

    def test_unknown_symbol_is_empty(self, repo: SqliteReconciliationRepository) -> None:
        repo.save_report(report())
        assert repo.recent_reports(symbol="nope") == []
