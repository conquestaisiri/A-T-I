"""Unit tests for the SQLite persistence layer."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository


def make_trade(
    symbol: str,
    trade_id: int,
    ts: datetime,
    price: float = 100.0,
) -> ObservationEvent:
    return ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts,
        payload={
            "symbol": symbol,
            "trade_id": trade_id,
            "price": price,
            "quantity": 1.0,
        },
    )


def make_context(symbol: str, ts: datetime) -> MarketContext:
    events = (make_trade(symbol, 1, ts, 100.0),)
    snapshot = ContextSnapshot.from_events(events)
    feature = ContextFeature(name="trend", value="up", computation_timestamp=ts, execution_time=0.1)
    return MarketContext(snapshot=snapshot, features=(("trend", feature),), created_at=ts)


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "test.db")


@pytest.fixture
def observation_repo(database: Database) -> SqliteObservationRepository:
    return SqliteObservationRepository(database)


@pytest.fixture
def context_repo(database: Database) -> SqliteContextRepository:
    return SqliteContextRepository(database)


class TestSqliteObservationRepository:
    def test_save_inserts_new_event(self, observation_repo):
        event = make_trade("btcusdt", 1, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
        assert observation_repo.save(event) is True
        assert observation_repo.count() == 1

    def test_save_replay_returns_false_and_deduplicates(self, observation_repo):
        event = make_trade("btcusdt", 1, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
        assert observation_repo.save(event) is True
        assert observation_repo.save(event) is False
        assert observation_repo.count() == 1

    def test_distinct_trade_ids_are_both_persisted(self, observation_repo):
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        assert observation_repo.save(make_trade("btcusdt", 1, base)) is True
        assert observation_repo.save(make_trade("btcusdt", 2, base + timedelta(seconds=1))) is True
        assert observation_repo.count() == 2

    def test_find_recent_returns_chronological_order(self, observation_repo):
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        for i in range(3):
            observation_repo.save(make_trade("btcusdt", i + 1, base + timedelta(seconds=i)))

        events = observation_repo.find_recent("btcusdt", limit=3)
        assert [e.payload["trade_id"] for e in events] == [1, 2, 3]

    def test_find_recent_respects_limit(self, observation_repo):
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        for i in range(5):
            observation_repo.save(make_trade("btcusdt", i + 1, base + timedelta(seconds=i)))

        events = observation_repo.find_recent("btcusdt", limit=2)
        assert len(events) == 2
        assert [e.payload["trade_id"] for e in events] == [4, 5]

    def test_find_recent_filters_by_symbol(self, observation_repo):
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        observation_repo.save(make_trade("btcusdt", 1, base))
        observation_repo.save(make_trade("ethusdt", 1, base))

        assert len(observation_repo.find_recent("btcusdt")) == 1
        assert observation_repo.count("btcusdt") == 1
        assert observation_repo.count("ethusdt") == 1
        assert observation_repo.count() == 2

    def test_count_without_symbol_is_total(self, observation_repo):
        assert observation_repo.count() == 0

    def test_missing_symbol_raises_value_error(self, observation_repo):
        event = ObservationEvent(
            source_id="binance",
            source_name="Binance",
            event_type=ObservationEventType.TRADE,
            timestamp=datetime(2026, 1, 15, tzinfo=UTC),
            payload={"trade_id": 1},
        )
        with pytest.raises(ValueError):
            observation_repo.save(event)

    def test_save_roundtrip_preserves_payload(self, observation_repo):
        event = make_trade("btcusdt", 7, datetime(2026, 1, 15, 12, 0, tzinfo=UTC), price=99.5)
        observation_repo.save(event)
        [reloaded] = observation_repo.find_recent("btcusdt")
        assert reloaded.payload["price"] == 99.5
        assert reloaded.payload["trade_id"] == 7


class TestSqliteContextRepository:
    def test_latest_returns_none_when_empty(self, context_repo):
        assert context_repo.latest("btcusdt") is None

    def test_save_then_latest_roundtrip(self, context_repo):
        ts = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        context = make_context("btcusdt", ts)
        context_repo.save(context)

        latest = context_repo.latest("btcusdt")
        assert latest is not None
        assert latest.snapshot.symbol == "btcusdt"
        assert latest.snapshot.events[0].payload["trade_id"] == 1
        assert latest.feature("trend").value == "up"
        assert latest.created_at == ts

    def test_latest_returns_newest(self, context_repo):
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        context_repo.save(make_context("btcusdt", base))
        context_repo.save(make_context("btcusdt", base + timedelta(seconds=5)))

        latest = context_repo.latest("btcusdt")
        assert latest is not None
        assert latest.created_at == base + timedelta(seconds=5)

    def test_history_returns_oldest_first(self, context_repo):
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        for i in range(3):
            context_repo.save(make_context("btcusdt", base + timedelta(seconds=i)))

        history = context_repo.history("btcusdt", limit=3)
        assert [c.created_at for c in history] == [base + timedelta(seconds=i) for i in range(3)]

    def test_history_is_symbol_scoped(self, context_repo):
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        context_repo.save(make_context("btcusdt", base))
        context_repo.save(make_context("ethusdt", base))

        assert len(context_repo.history("btcusdt")) == 1
        assert len(context_repo.history("ethusdt")) == 1
