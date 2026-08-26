"""Unit tests for ContextPipelineService wiring."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from backend.application.context.bootstrap import (
    build_context_pipeline,
    build_observation_enrichment,
)
from backend.application.interfaces.context_settings import ContextSettings, FeatureSettings
from backend.application.interfaces.supervisor import SupervisorStatus
from backend.application.pipeline.context_pipeline_service import ContextPipelineService
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.application.validation.tick_recorder import TickRecorder
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository


def make_event(symbol: str, trade_id: int, ts: datetime) -> ObservationEvent:
    return ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts,
        payload={"symbol": symbol, "trade_id": trade_id, "price": 100.0, "quantity": 1.0},
    )


@pytest.fixture(scope="module")
def loop():
    return asyncio.new_event_loop()


@pytest.fixture
def database(tmp_path) -> Database:
    return Database(tmp_path / "pipeline.db")


@pytest.fixture
def pipeline(database: Database) -> ContextPipelineService:
    settings = ContextSettings(window_duration=timedelta(seconds=60), features={})
    builder, _, _, _ = build_context_pipeline(settings)
    return ContextPipelineService(
        bus=ObservationBus(maxsize=16),
        context_builder=builder,
        observation_repository=SqliteObservationRepository(database),
        context_repository=SqliteContextRepository(database),
    )


class TestContextPipelineService:
    def test_persists_event_and_context(self, pipeline, loop):
        event = make_event("btcusdt", 1, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))

        async def scenario():
            task = asyncio.create_task(pipeline.start())
            await pipeline._bus.publish(event)
            pipeline.stop()
            await pipeline._bus.publish(make_event("btcusdt", 99, event.timestamp))
            await task

        loop.run_until_complete(scenario())

        assert pipeline._observation_repository.count("btcusdt") >= 1
        latest = pipeline._context_repository.latest("btcusdt")
        assert latest is not None
        assert latest.snapshot.symbol == "btcusdt"

    def test_replay_does_not_duplicate_storage(self, pipeline, loop):
        event = make_event("btcusdt", 1, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))

        async def scenario():
            task = asyncio.create_task(pipeline.start())
            for _ in range(2):
                await pipeline._bus.publish(event)
            pipeline.stop()
            await pipeline._bus.publish(make_event("btcusdt", 99, event.timestamp))
            await task

        loop.run_until_complete(scenario())

        assert pipeline._observation_repository.count("btcusdt") == 1

    def test_pipeline_stops_only_at_event_boundary(self, pipeline, loop):
        event = make_event("btcusdt", 1, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))

        async def scenario():
            task = asyncio.create_task(pipeline.start())
            await pipeline._bus.publish(event)
            await asyncio.sleep(0.05)
            pipeline.stop()
            # A subsequent event unblocks the pending get() and lets start() exit.
            await pipeline._bus.publish(make_event("btcusdt", 99, event.timestamp))
            await task

        loop.run_until_complete(scenario())
        assert pipeline._running is False


class TestFreshnessRecording:
    def _pipeline_with_supervisor(
        self, database: Database, max_age: float
    ) -> tuple[ContextPipelineService, SupervisorService]:
        settings = ContextSettings(window_duration=timedelta(seconds=60), features={})
        builder, _, _, _ = build_context_pipeline(settings)
        supervisor = SupervisorService(max_data_age_seconds=max_age)
        pipeline = ContextPipelineService(
            bus=ObservationBus(maxsize=16),
            context_builder=builder,
            observation_repository=SqliteObservationRepository(database),
            context_repository=SqliteContextRepository(database),
            supervisor=supervisor,
        )
        return pipeline, supervisor

    def test_market_event_records_freshness(self, database, loop):
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        pipeline, supervisor = self._pipeline_with_supervisor(database, max_age=300.0)

        async def scenario():
            task = asyncio.create_task(pipeline.start())
            await pipeline._bus.publish(make_event("btcusdt", 1, now))
            pipeline.stop()
            await pipeline._bus.publish(make_event("btcusdt", 99, now))
            await task

        loop.run_until_complete(scenario())
        assert supervisor.check(now=now).status is SupervisorStatus.HEALTHY

    def test_stale_feed_without_pipeline_gate_degrades(self, database, loop):
        old = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        later = old + timedelta(seconds=301)
        pipeline, supervisor = self._pipeline_with_supervisor(database, max_age=300.0)

        async def scenario():
            task = asyncio.create_task(pipeline.start())
            await pipeline._bus.publish(make_event("btcusdt", 1, old))
            pipeline.stop()
            await pipeline._bus.publish(make_event("btcusdt", 99, old))
            await task

        loop.run_until_complete(scenario())
        assert supervisor.check(now=later).status is SupervisorStatus.DEGRADED

    def test_non_market_event_does_not_refresh_freshness(self, database, loop):
        from datetime import timedelta as td

        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        settings = ContextSettings(window_duration=timedelta(seconds=60), features={})
        builder, _, _, _ = build_context_pipeline(settings)
        supervisor = SupervisorService(max_data_age_seconds=1.0)
        # A plain pipeline (no Supervisor) so _record_freshness reads the bus
        # symbol without any builder dependency. The rule under test is the
        # event-type filter, not the whole ingest path.
        pipe = ContextPipelineService(
            bus=ObservationBus(maxsize=16),
            context_builder=builder,
            observation_repository=SqliteObservationRepository(database),
            context_repository=SqliteContextRepository(database),
            supervisor=supervisor,
        )
        trade = make_event("btcusdt", 1, now)
        news = ObservationEvent(
            source_id="news",
            source_name="NewsFeed",
            event_type=ObservationEventType.NEWS,
            timestamp=now + td(seconds=60),
            payload={"symbol": "btcusdt", "headline": "none", "sentiment": 0.1},
        )
        pipe._record_freshness(trade)
        assert supervisor.check(now=now).status is SupervisorStatus.HEALTHY
        pipe._record_freshness(news)
        # Even though the news event is newer, it MUST NOT refresh the gate:
        # a feed delivering only news keeps the market-data gate stale.
        assert supervisor.check(now=now + td(seconds=2)).status is SupervisorStatus.DEGRADED

    def test_supervisor_absent_records_nothing_and_crashes_nothing(self, database, loop):
        settings = ContextSettings(window_duration=timedelta(seconds=60), features={})
        builder, _, _, _ = build_context_pipeline(settings)
        pipeline = ContextPipelineService(
            bus=ObservationBus(maxsize=16),
            context_builder=builder,
            observation_repository=SqliteObservationRepository(database),
            context_repository=SqliteContextRepository(database),
        )

        async def scenario():
            task = asyncio.create_task(pipeline.start())
            await pipeline._bus.publish(
                make_event("btcusdt", 1, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
            )
            pipeline.stop()
            await pipeline._bus.publish(
                make_event("btcusdt", 99, datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
            )
            await task

        loop.run_until_complete(scenario())
        assert pipeline._observation_repository.count("btcusdt") >= 1


class TestObservationEnrichment:
    """P0-004: canonical observation enrichment path.

    ORDER_BOOK snapshots must update micro-price state, L2 delta events must
    update OFI and reach the tick recorder, and the pipeline must wire the
    enrichment in front of the context builder.
    """

    def _order_book_event(
        self, ts: datetime, bids, asks, *, delta: bool = False
    ) -> ObservationEvent:
        payload = {"symbol": "btcusdt", "bids": bids, "asks": asks}
        if delta:
            payload["delta"] = True
        return ObservationEvent(
            source_id="ccxt",
            source_name="CCXT",
            event_type=ObservationEventType.ORDER_BOOK,
            timestamp=ts,
            payload=payload,
        )

    def test_snapshot_updates_micro_price_state(self, tmp_path):
        enrichment = build_observation_enrichment(tick_recorder=TickRecorder(data_dir=tmp_path))
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        event = self._order_book_event(
            now,
            bids=[[100.0, 5.0], [99.5, 3.0]],
            asks=[[100.5, 4.0], [101.0, 2.0]],
        )

        enrichment.enrich(event)
        state = enrichment.micro_price("BTCUSDT")

        assert state is not None
        assert state["best_bid"] == 100.0
        assert state["best_ask"] == 100.5
        # micro-price = (bid*ask_size + ask*bid_size) / (bid+ask size)
        expected_micro = (100.0 * 4.0 + 100.5 * 5.0) / 9.0
        assert state["micro_price"] == pytest.approx(expected_micro)

    def test_delta_updates_ofi_and_reaches_recorder(self, tmp_path):
        recorder = TickRecorder(data_dir=tmp_path)
        enrichment = build_observation_enrichment(tick_recorder=recorder)
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        event = self._order_book_event(
            now,
            bids=[{"price": 100.0, "size": 5.0, "action": "add"}],
            asks=[{"price": 100.5, "size": 4.0, "action": "add"}],
            delta=True,
        )

        enrichment.enrich(event)
        ofi = enrichment.ofi("BTCUSDT")

        assert ofi["event_count"] > 0
        # bid add contributes +5 at level 0; ask add contributes -4
        assert ofi["best_level_ofi"] == pytest.approx(5.0 + (-4.0))

        recorder.close()
        stats = recorder.get_stats()
        assert "btcusdt" in stats
        assert stats["btcusdt"]["files"] >= 1

    def test_non_order_book_event_is_ignored(self):
        enrichment = build_observation_enrichment()
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        trade = make_event("btcusdt", 1, now)

        enrichment.enrich(trade)

        assert enrichment.micro_price("btcusdt") is None
        assert enrichment.ofi("BTCUSDT")["event_count"] == 0

    def test_pipeline_enriches_before_building_context(self, database, loop, tmp_path):
        settings = ContextSettings(
            window_duration=timedelta(seconds=60),
            features={
                "micro_price": FeatureSettings(enabled=True, parameters={"symbol": "BTCUSDT"})
            },
        )
        builder, _, _, _ = build_context_pipeline(settings)
        enrichment = build_observation_enrichment(tick_recorder=TickRecorder(data_dir=tmp_path))
        pipeline = ContextPipelineService(
            bus=ObservationBus(maxsize=16),
            context_builder=builder,
            observation_repository=SqliteObservationRepository(database),
            context_repository=SqliteContextRepository(database),
            enrichment=enrichment,
        )
        now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        book = self._order_book_event(
            now,
            bids=[[100.0, 5.0]],
            asks=[[100.5, 4.0]],
        )

        async def scenario():
            task = asyncio.create_task(pipeline.start())
            await pipeline._bus.publish(book)
            pipeline.stop()
            await pipeline._bus.publish(make_event("btcusdt", 99, now))
            await task

        loop.run_until_complete(scenario())

        latest = pipeline._context_repository.latest("btcusdt")
        assert latest is not None
        feature = latest.feature("micro_price")
        assert feature.value["cache_status"] == "warm"
        assert feature.value["best_bid"] == 100.0
