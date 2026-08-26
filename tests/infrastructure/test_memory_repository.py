"""Unit tests for SQLite episodic memory persistence."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_episode(
    episode_id: str = "ep-1",
    symbol: str = "btcusdt",
    created_at: datetime | None = None,
    outcome: MemoryOutcome = MemoryOutcome.WIN,
    realized_pnl: float | None = 12.5,
) -> MemoryEpisode:
    return MemoryEpisode(
        episode_id=episode_id,
        correlation_id="corr-1",
        symbol=symbol,
        created_at=created_at or ts(),
        proposal_id="prop-1",
        action_type="enter_long",
        confidence=0.7,
        outcome=outcome,
        realized_pnl=realized_pnl,
        summary="long gained",
    )


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def memory_repo(database: Database) -> SqliteMemoryRepository:
    return SqliteMemoryRepository(database)


class TestSqliteMemoryRepository:
    def test_record_then_recall_roundtrip(self, memory_repo):
        memory_repo.record(make_episode())
        recalled = memory_repo.recall("btcusdt")
        assert len(recalled) == 1
        assert recalled[0] == make_episode()

    def test_record_is_idempotent(self, memory_repo):
        memory_repo.record(make_episode())
        memory_repo.record(make_episode())
        assert memory_repo.count() == 1

    def test_recall_oldest_first(self, memory_repo):
        base = ts()
        for i in range(3):
            memory_repo.record(
                make_episode(episode_id=f"ep-{i}", created_at=base.replace(second=i))
            )
        recalled = memory_repo.recall("btcusdt", limit=3)
        assert [e.episode_id for e in recalled] == ["ep-0", "ep-1", "ep-2"]

    def test_recall_respects_limit(self, memory_repo):
        base = ts()
        for i in range(5):
            memory_repo.record(
                make_episode(episode_id=f"ep-{i}", created_at=base.replace(second=i))
            )
        assert len(memory_repo.recall("btcusdt", limit=2)) == 2

    def test_recall_is_symbol_scoped(self, memory_repo):
        memory_repo.record(make_episode(symbol="btcusdt"))
        memory_repo.record(make_episode(episode_id="ep-2", symbol="ethusdt"))
        assert len(memory_repo.recall("btcusdt")) == 1
        assert memory_repo.count("btcusdt") == 1
        assert memory_repo.count() == 2

    def test_recall_invalid_limit_rejected(self, memory_repo):
        with pytest.raises(ValueError):
            memory_repo.recall("btcusdt", limit=0)

    def test_closed_loss_episode_roundtrip(self, memory_repo):
        ep = replace(make_episode(), outcome=MemoryOutcome.LOSS, realized_pnl=-30.0)
        memory_repo.record(ep)
        assert memory_repo.recall("btcusdt")[0] == ep
