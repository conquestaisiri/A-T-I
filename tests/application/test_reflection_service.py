"""Tests for the reflection service (ledger -> episodic memory)."""

from __future__ import annotations

from datetime import UTC, datetime

from backend.application.context.bootstrap import build_reflection_service
from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.interfaces.memory_store import MemoryStore
from backend.application.interfaces.proposal_repository import ProposalRepository
from backend.application.reflection.reflection_service import ReflectionService
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
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository


def ts() -> datetime:
    return datetime(2026, 2, 1, 9, 30, 0, tzinfo=UTC)


class InMemoryLedger(LedgerRepository):
    def __init__(self) -> None:
        self._records: dict[str, TradeRecord] = {}
        self.by_symbol: dict[str, list[TradeRecord]] = {}

    def save(self, record: TradeRecord) -> None:
        self._records[record.trade_id] = record
        self.by_symbol.setdefault(record.symbol, []).append(record)

    def find_by_id(self, trade_id: str) -> TradeRecord | None:
        return self._records.get(trade_id)

    def find_recent(self, symbol: str, limit: int = 20) -> list[TradeRecord]:
        return self.by_symbol.get(symbol, [])[-limit:]

    def open_trades(self) -> list[TradeRecord]:
        return [r for r in self._records.values() if r.status is TradeStatus.OPEN]

    def closed_trades(self, limit: int = 100) -> list[TradeRecord]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        return [r for r in self._records.values() if r.status is TradeStatus.CLOSED][-limit:]

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            return len(self._records)
        return len(self.by_symbol.get(symbol, []))


class InMemoryProposals(ProposalRepository):
    def __init__(self, proposals: dict[str, DecisionProposal]) -> None:
        self._proposals = proposals

    def save(self, proposal: DecisionProposal) -> None:
        self._proposals[proposal.proposal_id] = proposal

    def find_by_id(self, proposal_id: str) -> DecisionProposal | None:
        return self._proposals.get(proposal_id)

    def find_recent(self, symbol: str, limit: int = 20) -> list[DecisionProposal]:
        return [p for p in self._proposals.values() if p.symbol == symbol][-limit:]

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            return len(self._proposals)
        return sum(1 for p in self._proposals.values() if p.symbol == symbol)


def closed_trade(
    trade_id: str,
    symbol: str = "btcusdt",
    *,
    side: OrderSide = OrderSide.BUY,
    proposal_id: str | None = "prop-1",
    correlation_id: str | None = "corr-1",
    realized_pnl: float = 50.0,
) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        proposal_id=proposal_id,
        correlation_id=correlation_id,
        symbol=symbol,
        side=side,
        quantity=1.0,
        entry_price=100.0,
        opened_at=ts(),
        exit_price=105.0,
        closed_at=ts(),
        realized_pnl=realized_pnl,
        status=TradeStatus.CLOSED,
    )


def proposal(proposal_id: str = "prop-1") -> DecisionProposal:
    return DecisionProposal(
        proposal_id=proposal_id,
        correlation_id="corr-1",
        created_at=ts(),
        symbol="btcusdt",
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


def make_service(
    ledger: LedgerRepository,
    proposals: ProposalRepository,
    memory: MemoryStore,
) -> ReflectionService:
    return ReflectionService(ledger=ledger, proposals=proposals, memory=memory)


class TestReflect:
    def test_echoes_stats_for_closed_trades(self, tmp_path):
        ledger = InMemoryLedger()
        ledger.save(closed_trade("t1", realized_pnl=50.0))
        ledger.save(closed_trade("t2", realized_pnl=-20.0))
        ledger.save(closed_trade("t3", realized_pnl=0.0))
        proposals = InMemoryProposals({"prop-1": proposal()})
        memory = SqliteMemoryRepository(Database(tmp_path / "r.db"))
        service = make_service(ledger, proposals, memory)

        stats = service.reflect("btcusdt")

        assert stats.trades_scanned == 3
        assert stats.episodes_recorded == 3
        assert stats.wins == 1
        assert stats.losses == 1
        assert stats.flats == 1
        assert memory.count("btcusdt") == 3

    def test_writes_outcome_mapped_episodes(self, tmp_path):
        ledger = InMemoryLedger()
        ledger.save(closed_trade("t1", realized_pnl=50.0))
        ledger.save(closed_trade("t2", realized_pnl=-20.0))
        ledger.save(closed_trade("t3", realized_pnl=0.0))
        memory = SqliteMemoryRepository(Database(tmp_path / "r.db"))
        service = make_service(ledger, InMemoryProposals({"prop-1": proposal()}), memory)

        service.reflect("btcusdt")

        by_id = {ep.episode_id: ep for ep in memory.recall("btcusdt", limit=10)}
        assert by_id["ep-t1"].outcome.value == "win"
        assert by_id["ep-t1"].realized_pnl == 50.0
        assert by_id["ep-t2"].outcome.value == "loss"
        assert by_id["ep-t3"].outcome.value == "flat"

    def test_carries_proposal_confidence_and_primary_action(self, tmp_path):
        ledger = InMemoryLedger()
        ledger.save(closed_trade("t1"))
        memory = SqliteMemoryRepository(Database(tmp_path / "r.db"))
        service = make_service(ledger, InMemoryProposals({"prop-1": proposal()}), memory)

        service.reflect("btcusdt")
        episode = memory.recall("btcusdt", limit=10)[0]

        assert episode.proposal_id == "prop-1"
        assert episode.confidence == 0.8
        assert episode.action_type == "enter_long"
        assert episode.summary == "btcusdt long win (pnl +50.00)"

    def test_missing_proposal_falls_back_to_side(self, tmp_path):
        ledger = InMemoryLedger()
        ledger.save(closed_trade("t1", proposal_id=None, realized_pnl=10.0))
        memory = SqliteMemoryRepository(Database(tmp_path / "r.db"))
        service = make_service(ledger, InMemoryProposals({}), memory)

        service.reflect("btcusdt")
        episode = memory.recall("btcusdt", limit=10)[0]

        assert episode.proposal_id == ""
        assert episode.action_type == "enter_long"
        assert episode.confidence == 0.5

    def test_skips_open_trades(self, tmp_path):
        ledger = InMemoryLedger()
        ledger.save(
            TradeRecord.open(
                trade_id="t-open",
                proposal_id="prop-1",
                correlation_id="corr-1",
                symbol="btcusdt",
                side=OrderSide.BUY,
                quantity=1.0,
                entry_price=100.0,
                opened_at=ts(),
            )
        )
        ledger.save(closed_trade("t-closed", realized_pnl=10.0))
        memory = SqliteMemoryRepository(Database(tmp_path / "r.db"))
        service = make_service(ledger, InMemoryProposals({"prop-1": proposal()}), memory)

        stats = service.reflect("btcusdt")

        assert stats.trades_scanned == 2
        assert stats.episodes_recorded == 1
        assert memory.count("btcusdt") == 1

    def test_is_idempotent(self, tmp_path):
        ledger = InMemoryLedger()
        ledger.save(closed_trade("t1", realized_pnl=50.0))
        memory = SqliteMemoryRepository(Database(tmp_path / "r.db"))
        service = make_service(ledger, InMemoryProposals({"prop-1": proposal()}), memory)

        service.reflect("btcusdt")
        service.reflect("btcusdt")

        assert memory.count("btcusdt") == 1

    def test_limit_validation(self, tmp_path):
        memory = SqliteMemoryRepository(Database(tmp_path / "r.db"))
        service = make_service(InMemoryLedger(), InMemoryProposals({}), memory)

        try:
            service.reflect("btcusdt", limit=0)
        except ValueError:
            return
        raise AssertionError("expected ValueError for limit=0")


class TestBuildReflectionService:
    def test_wires_from_bootstrap(self, tmp_path):
        service = build_reflection_service(tmp_path / "r.db")
        assert isinstance(service, ReflectionService)
        assert isinstance(service._memory, SqliteMemoryRepository)  # noqa: SLF001
        assert service._memory.count() == 0  # noqa: SLF001
