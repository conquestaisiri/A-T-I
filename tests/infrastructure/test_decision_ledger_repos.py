"""Unit tests for SQLite proposal and ledger persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.execution.order import OrderSide
from backend.domain.execution.trade_record import TradeRecord, TradeStatus
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_proposal(
    proposal_id: str = "prop-1",
    symbol: str = "btcusdt",
    created_at: datetime | None = None,
    **overrides: Any,
) -> DecisionProposal:
    params: dict[str, Any] = dict(
        proposal_id=proposal_id,
        correlation_id="corr-1",
        created_at=created_at or ts(),
        symbol=symbol,
        hypothesis=Hypothesis(
            statement="trend",
            supporting_evidence=(EvidenceItem(source="trend", summary="up", value=1.0),),
            opposing_evidence=(),
        ),
        confidence=0.8,
        uncertainty="none",
        actions=(
            ProposedAction(
                action_type=ProposedActionType.ENTER_LONG,
                size_fraction=0.10,
                order=1,
                rationale="go",
            ),
        ),
        risk_context=RiskContext(
            account_equity=100_000.0,
            open_exposure_pct=0.0,
            daily_loss_pct=0.0,
            monthly_loss_pct=0.0,
            total_loss_pct=0.0,
            drawdown_pct=0.0,
            position_count=0,
        ),
        alternatives=(),
        rationale="go",
    )
    params.update(overrides)
    return DecisionProposal(**params)


def make_trade(
    trade_id: str = "trade-1",
    symbol: str = "btcusdt",
    status: TradeStatus = TradeStatus.OPEN,
) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        proposal_id="prop-1",
        correlation_id="corr-1",
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=100.0,
        opened_at=ts(),
        exit_price=None,
        closed_at=None,
        realized_pnl=None,
        status=status,
    )


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def proposal_repo(database: Database) -> SqliteProposalRepository:
    return SqliteProposalRepository(database)


@pytest.fixture
def ledger_repo(database: Database) -> SqliteLedgerRepository:
    return SqliteLedgerRepository(database)


class TestSqliteProposalRepository:
    def test_save_then_find_by_id_roundtrip(self, proposal_repo):
        proposal_repo.save(make_proposal())
        reloaded = proposal_repo.find_by_id("prop-1")
        assert reloaded is not None
        assert reloaded == make_proposal()
        assert reloaded.hypothesis.statement == "trend"

    def test_save_is_idempotent(self, proposal_repo):
        proposal_repo.save(make_proposal())
        proposal_repo.save(make_proposal())
        assert proposal_repo.count() == 1

    def test_find_by_id_returns_none_when_absent(self, proposal_repo):
        assert proposal_repo.find_by_id("missing") is None

    def test_find_recent_oldest_first(self, proposal_repo):
        base = ts()
        for i in range(3):
            proposal_repo.save(
                make_proposal(
                    proposal_id=f"prop-{i}",
                    created_at=base.replace(second=i),
                )
            )
        recent = proposal_repo.find_recent("btcusdt", limit=3)
        assert [p.proposal_id for p in recent] == ["prop-0", "prop-1", "prop-2"]

    def test_find_recent_respects_limit(self, proposal_repo):
        base = ts()
        for i in range(5):
            proposal_repo.save(
                make_proposal(
                    proposal_id=f"prop-{i}",
                    created_at=base.replace(second=i),
                )
            )
        assert len(proposal_repo.find_recent("btcusdt", limit=2)) == 2

    def test_find_recent_is_symbol_scoped(self, proposal_repo):
        proposal_repo.save(make_proposal(symbol="btcusdt"))
        proposal_repo.save(make_proposal(proposal_id="prop-2", symbol="ethusdt"))
        assert len(proposal_repo.find_recent("btcusdt")) == 1
        assert proposal_repo.count("btcusdt") == 1
        assert proposal_repo.count() == 2

    def test_find_recent_invalid_limit_rejected(self, proposal_repo):
        with pytest.raises(ValueError):
            proposal_repo.find_recent("btcusdt", limit=0)

    def test_roundtrip_preserves_full_proposal(self, proposal_repo):
        proposal_repo.save(make_proposal())
        reloaded = proposal_repo.find_by_id("prop-1")
        assert reloaded == make_proposal()


class TestSqliteLedgerRepository:
    def test_save_open_then_find_by_id(self, ledger_repo):
        ledger_repo.save(make_trade())
        reloaded = ledger_repo.find_by_id("trade-1")
        assert reloaded is not None
        assert reloaded.status is TradeStatus.OPEN
        assert reloaded == make_trade()

    def test_upsert_replaces_open_with_closed(self, ledger_repo):
        ledger_repo.save(make_trade())
        closed = make_trade(
            trade_id="trade-1",
            status=TradeStatus.CLOSED,
        )
        ledger_repo.save(closed)
        reloaded = ledger_repo.find_by_id("trade-1")
        assert reloaded is not None
        assert reloaded.status is TradeStatus.CLOSED
        assert ledger_repo.count() == 1

    def test_open_trades_returns_only_open(self, ledger_repo):
        ledger_repo.save(make_trade(trade_id="trade-1"))
        ledger_repo.save(
            make_trade(trade_id="trade-2", status=TradeStatus.CLOSED),
        )
        open_trades = ledger_repo.open_trades()
        assert [t.trade_id for t in open_trades] == ["trade-1"]

    def test_find_recent_oldest_first(self, ledger_repo):
        base = ts()
        for i in range(3):
            ledger_repo.save(
                replace(make_trade(trade_id=f"trade-{i}"), opened_at=base.replace(second=i))
            )
        recent = ledger_repo.find_recent("btcusdt", limit=3)
        assert [t.trade_id for t in recent] == ["trade-0", "trade-1", "trade-2"]

    def test_find_recent_respects_limit(self, ledger_repo):
        base = ts()
        for i in range(5):
            ledger_repo.save(
                replace(make_trade(trade_id=f"trade-{i}"), opened_at=base.replace(second=i))
            )
        assert len(ledger_repo.find_recent("btcusdt", limit=2)) == 2

    def test_closed_record_roundtrip(self, ledger_repo):
        closed = replace(
            make_trade(trade_id="trade-1", status=TradeStatus.CLOSED),
            exit_price=110.0,
            closed_at=ts(),
            realized_pnl=100.0,
        )
        ledger_repo.save(closed)
        reloaded = ledger_repo.find_by_id("trade-1")
        assert reloaded == closed
        assert reloaded.realized_pnl == 100.0
