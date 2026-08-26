"""Tests for the self-feeding market-data loop (review action 3 / G4).

Drives the same durable pipeline the operator drive route uses, but through
the ObservationBus: a venue adapter (simulated as a plain publisher) feeds
trade events and the loop turns them into decisions automatically.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.application.context.bootstrap import (
    _build_reflection,
    build_context_pipeline_from_config,
    build_observation_enrichment,
)
from backend.application.decision.rule_based_solver import RuleBasedSolver
from backend.application.pipeline.context_pipeline_service import ContextPipelineService
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.pipeline.market_loop_service import MarketLoopService
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository


def build_components(tmp_path: Path):
    """Wire the real ingest + decision pipelines around a fresh SQLite DB."""
    database = Database(tmp_path / "market-loop.db")
    ledger_repo = SqliteLedgerRepository(database)
    fill_engine = PaperFillEngine()
    supervisor = SupervisorService()
    bus = ObservationBus(maxsize=1024)
    context_builder = build_context_pipeline_from_config()[0]
    ingest = ContextPipelineService(
        bus=bus,
        context_builder=context_builder,
        observation_repository=SqliteObservationRepository(database),
        context_repository=SqliteContextRepository(database),
        supervisor=supervisor,
        enrichment=build_observation_enrichment(),
    )
    simulator = PaperTradingSimulator(
        risk_gate=CircuitBreakerRiskGate(),
        order_gateway=fill_engine,
        ledger=ledger_repo,
    )
    decision = DecisionPipelineService(
        reasoner=RuleBasedSolver(),
        proposal_repository=SqliteProposalRepository(database),
        simulator=simulator,
        reflection=_build_reflection(database, SqliteMemoryRepository(database)),
        supervisor=supervisor,
    )
    return database, bus, ingest, decision, fill_engine


async def _synthetic_pre_warm(symbol: str) -> list[dict[str, object]]:
    """Hermetic stand-in for the MEXC klines fetch: 200 deterministic candles.

    Injected into every MarketLoopService under test so ``start()`` never
    touches the network — pre-warm behaviour stays exercised, reachability
    is not part of the contract these tests own.
    """
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    hour_ms = 3_600_000
    price = 100.0
    candles: list[dict[str, object]] = []
    for i in range(200):
        ts = now_ms - (200 - i) * hour_ms
        o = price
        c = round(price * 1.001, 6)
        candles.append(
            {"time": ts, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1.0}
        )
        price = c
    _ = symbol  # one warm series per requested symbol; content is symbol-agnostic
    return candles


async def _publish_tick(
    bus: ObservationBus,
    symbol: str,
    price: float,
    *,
    index: int,
    base: datetime,
) -> None:
    event = ObservationEvent(
        source_id="test-venue",
        source_name="Test Venue",
        event_type=ObservationEventType.TRADE,
        timestamp=base + timedelta(seconds=index),
        payload={
            "symbol": symbol,
            "trade_id": index,
            "price": price,
            "quantity": 1.0,
        },
    )
    await bus.publish(event)


def test_loop_drives_decisions_from_bus(tmp_path: Path) -> None:
    """A price campaign on the bus is traded automatically through the real pipeline."""
    database, bus, ingest, decision, fill_engine = build_components(tmp_path)
    try:

        async def scenario() -> dict[str, int]:
            loop = MarketLoopService(
                bus=bus,
                ingest_pipeline=ingest,
                decision_pipeline=decision,
                fill_engine=fill_engine,
                symbol="btcusdt",
                thread_lock=threading.Lock(),
                pre_warm_fetcher=_synthetic_pre_warm,
            )
            task = asyncio.create_task(loop.start())
            base = datetime.now(UTC)
            price = 100.0
            for i in range(20):
                price = round(price * 1.002, 4)
                await _publish_tick(bus, "btcusdt", price, index=i, base=base)
            for _ in range(100):
                if loop.stats()["events_seen"] >= 20:
                    break
                await asyncio.sleep(0.02)
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return loop.stats()

        stats = asyncio.run(scenario())
        assert stats["events_seen"] == 20
        assert stats["decisions_driven"] > 0
    finally:
        database.close()


def test_loop_ignores_other_symbols(tmp_path: Path) -> None:
    """Events for a symbol the loop does not trade never reach the decision path."""
    database, bus, ingest, decision, fill_engine = build_components(tmp_path)
    try:

        async def scenario() -> dict[str, int]:
            loop = MarketLoopService(
                bus=bus,
                ingest_pipeline=ingest,
                decision_pipeline=decision,
                fill_engine=fill_engine,
                symbol="btcusdt",
                pre_warm_fetcher=_synthetic_pre_warm,
            )
            task = asyncio.create_task(loop.start())
            base = datetime.now(UTC)
            for i in range(2):
                await _publish_tick(bus, "ethusdt", 3000.0 + i, index=i, base=base)
            for _ in range(100):
                if loop.stats()["events_seen"] >= 2:
                    break
                await asyncio.sleep(0.02)
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return loop.stats()

        stats = asyncio.run(scenario())
        assert stats["events_seen"] == 2
        assert stats["decisions_driven"] == 0
    finally:
        database.close()


def test_loop_survives_malformed_event(tmp_path: Path) -> None:
    """A single malformed event is logged and skipped; the loop keeps running."""
    database, bus, ingest, decision, fill_engine = build_components(tmp_path)
    try:

        async def scenario() -> dict[str, int]:
            loop = MarketLoopService(
                bus=bus,
                ingest_pipeline=ingest,
                decision_pipeline=decision,
                fill_engine=fill_engine,
                symbol="btcusdt",
                pre_warm_fetcher=_synthetic_pre_warm,
            )
            task = asyncio.create_task(loop.start())
            base = datetime.now(UTC)
            # A good tick first (so decisions_driven has a floor), then a malformed one.
            await _publish_tick(bus, "btcusdt", 100.0, index=0, base=base)
            await bus.publish(
                ObservationEvent(
                    source_id="test-venue",
                    source_name="Test Venue",
                    event_type=ObservationEventType.TRADE,
                    timestamp=base + timedelta(seconds=1),
                    # symbol deliberately missing -> ingest raises
                    payload={"symbol": None, "trade_id": 1, "price": 101.0},
                )
            )
            for _ in range(100):
                if loop.stats()["events_seen"] >= 2:
                    break
                await asyncio.sleep(0.02)
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return loop.stats()

        stats = asyncio.run(scenario())
        assert stats["events_seen"] == 2
        assert stats["decisions_driven"] >= 1
    finally:
        database.close()


def test_loop_trades_multiple_symbols(tmp_path: Path) -> None:
    """With ``symbols``, every listed symbol reaches the decision path."""
    database, bus, ingest, decision, fill_engine = build_components(tmp_path)
    try:

        async def scenario() -> dict[str, int]:
            loop = MarketLoopService(
                bus=bus,
                ingest_pipeline=ingest,
                decision_pipeline=decision,
                fill_engine=fill_engine,
                symbols=["BTCUSDT", "ETHUSDT"],
                pre_warm_fetcher=_synthetic_pre_warm,
            )
            task = asyncio.create_task(loop.start())
            base = datetime.now(UTC)
            await _publish_tick(bus, "btcusdt", 100.0, index=0, base=base)
            await _publish_tick(bus, "ethusdt", 3000.0, index=1, base=base)
            for _ in range(100):
                if loop.stats()["events_seen"] >= 2:
                    break
                await asyncio.sleep(0.02)
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return loop.stats()

        stats = asyncio.run(scenario())
        assert stats["events_seen"] == 2
        # Both symbols must have been driven through decisions (cooldown is
        # per-loop, so the second symbol's first event still decides).
        assert stats["decisions_driven"] == 2
    finally:
        database.close()


def test_cooldown_skips_rapid_second_decision(tmp_path: Path) -> None:
    """Second decision inside the cooldown window is skipped, ingest continues."""
    database, bus, ingest, decision, fill_engine = build_components(tmp_path)
    try:

        async def scenario() -> dict[str, int]:
            loop = MarketLoopService(
                bus=bus,
                ingest_pipeline=ingest,
                decision_pipeline=decision,
                fill_engine=fill_engine,
                symbols=["BTCUSDT"],
                min_decision_interval_seconds=3600.0,
                pre_warm_fetcher=_synthetic_pre_warm,
            )
            task = asyncio.create_task(loop.start())
            base = datetime.now(UTC)
            await _publish_tick(bus, "btcusdt", 100.0, index=0, base=base)
            await _publish_tick(bus, "btcusdt", 101.0, index=1, base=base)
            for _ in range(100):
                if loop.stats()["events_seen"] >= 2:
                    break
                await asyncio.sleep(0.02)
            loop.stop()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            return loop.stats()

        stats = asyncio.run(scenario())
        assert stats["events_seen"] == 2
        assert stats["decisions_driven"] == 1
        assert stats["decisions_skipped_cooldown"] == 1
    finally:
        database.close()
