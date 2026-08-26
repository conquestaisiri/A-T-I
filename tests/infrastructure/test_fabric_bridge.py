"""Unit tests for the data-fabric -> observation bus bridge.

The bridge is the connector that makes paper mode self-feeding: the data
fabric emits rich :class:`NormalizedEvent` instances on its own event bus, and
the decision loop consumes :class:`ObservationEvent` instances on the
observation bus. These tests pin the translation contract so a change to the
fabric's normalized schema can never silently break the paper path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.domain.data_fabric.envelope import NormalizedEvent
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.data_fabric.event_bus import EnhancedEventBus
from backend.infrastructure.observation.fabric_bridge import (
    _FABRIC_TO_OBSERVATION,
    _to_observation_event,
)
from backend.infrastructure.observation.observation_bus import ObservationBus


def _fabric_trade() -> NormalizedEvent:
    return NormalizedEvent.create_trade(
        source_id="binance",
        source_name="Binance",
        venue="binance",
        instrument_id="BINANCE_BTCUSDT",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        event_time=datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC),
        price=60000.0,
        quantity=0.5,
        side="buy",
        trade_id=12345,
    )


def _fabric_quote() -> NormalizedEvent:
    return NormalizedEvent.create_quote(
        source_id="binance",
        source_name="Binance",
        venue="binance",
        instrument_id="BINANCE_BTCUSDT",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        event_time=datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC),
        bid=59999.5,
        ask=60000.5,
    )


def _fabric_book() -> NormalizedEvent:
    return NormalizedEvent(
        event_type="book",
        source_id="binance",
        source_name="Binance",
        venue="binance",
        symbol="BTCUSDT",
        event_time=datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC),
        payload={
            "bids": [[60000.0, 1.0]],
            "asks": [[60001.0, 2.0]],
            "last_update_id": 999,
        },
    )


def _fabric_candle() -> NormalizedEvent:
    return NormalizedEvent.create_candle(
        source_id="binance",
        source_name="Binance",
        venue="binance",
        instrument_id="BINANCE_BTCUSDT",
        symbol="BTCUSDT",
        base_asset="BTC",
        quote_asset="USDT",
        event_time=datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC),
        open_=59900.0,
        high=60100.0,
        low=59800.0,
        close=60050.0,
        volume=12.5,
        interval="1m",
    )


@pytest.mark.parametrize(
    ("fabric_type", "expected"),
    [
        ("trade", ObservationEventType.TRADE),
        ("quote", ObservationEventType.TICKER),
        ("book", ObservationEventType.ORDER_BOOK),
        ("candle", ObservationEventType.CANDLE),
    ],
)
def test_event_type_mapping_covers_market_types(
    fabric_type: str, expected: ObservationEventType
) -> None:
    assert _FABRIC_TO_OBSERVATION[fabric_type] is expected


def test_non_market_types_not_bridged() -> None:
    for fabric_type in ("news", "macro", "sentiment", "onchain"):
        assert fabric_type not in _FABRIC_TO_OBSERVATION


def test_trade_payload_matches_ccxt_schema() -> None:
    event = _to_observation_event(_fabric_trade())
    assert event is not None
    assert event.event_type is ObservationEventType.TRADE
    assert event.source_id == "binance"
    assert event.payload["symbol"] == "BTCUSDT"
    assert event.payload["trade_id"] == 12345
    assert event.payload["price"] == 60000.0
    assert event.payload["quantity"] == 0.5
    assert event.payload["side"] == "buy"


def test_quote_payload_carries_mid_for_mark_price() -> None:
    event = _to_observation_event(_fabric_quote())
    assert event is not None
    assert event.event_type is ObservationEventType.TICKER
    # MarketLoopService reads ``last``/``close`` for tickers; fabric mid is 60000.0.
    assert event.payload["last"] == 60000.0
    assert event.payload["close"] == 60000.0
    assert event.payload["bid"] == 59999.5
    assert event.payload["ask"] == 60000.5


def test_book_payload_matches_ccxt_schema() -> None:
    event = _to_observation_event(_fabric_book())
    assert event is not None
    assert event.event_type is ObservationEventType.ORDER_BOOK
    assert event.payload["bids"] == [[60000.0, 1.0]]
    assert event.payload["asks"] == [[60001.0, 2.0]]


def test_candle_payload_carries_ohlcv() -> None:
    event = _to_observation_event(_fabric_candle())
    assert event is not None
    assert event.event_type is ObservationEventType.CANDLE
    assert event.payload["symbol"] == "BTCUSDT"
    assert event.payload["open"] == 59900.0
    assert event.payload["high"] == 60100.0
    assert event.payload["low"] == 59800.0
    assert event.payload["close"] == 60050.0
    assert event.payload["volume"] == 12.5
    assert event.payload["interval"] == "1m"


def test_symbol_required_for_bridging() -> None:
    trade = _fabric_trade()
    event = _to_observation_event(trade)
    assert event is not None
    # An event without a symbol must be skipped (never forwarded).
    blank = NormalizedEvent(
        event_type="trade",
        source_id="x",
        source_name="X",
        symbol="",
        event_time=datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC),
    )
    assert _to_observation_event(blank) is None


def test_unknown_event_type_skipped() -> None:
    weird = NormalizedEvent(
        event_type="quantum_flux",
        source_id="x",
        source_name="X",
        symbol="BTCUSDT",
        event_time=datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC),
    )
    assert _to_observation_event(weird) is None


def test_bus_fans_out_every_event_to_all_subscribers() -> None:
    """Regression test: EnhancedEventBus must not steal events between subscribers.

    Before the fix the bus held a single shared queue, so two subscribers
    (news pipeline and the bridge) each received only a *subset* of events and
    paper mode starved. With fan-out every subscriber gets every event.
    """
    import asyncio

    async def scenario() -> None:
        fabric_bus = EnhancedEventBus(maxsize=64, persistence_enabled=False)
        events = [_fabric_trade(), _fabric_quote(), _fabric_book()]

        async def drain(stream: Any) -> list[NormalizedEvent]:
            out = []
            for _ in events:
                out.append(await anext(stream))
            return out

        stream_a = fabric_bus.subscribe()
        stream_b = fabric_bus.subscribe()
        try:
            for event in events:
                await fabric_bus.publish(event)

            got_a = await drain(stream_a)
            got_b = await drain(stream_b)

            assert [e.event_type for e in got_a] == ["trade", "quote", "book"]
            assert [e.event_type for e in got_b] == ["trade", "quote", "book"]
        finally:
            await stream_a.aclose()
            await stream_b.aclose()

    asyncio.run(scenario())


def test_bus_unregisters_subscriber_on_close() -> None:
    """Closed subscribers must not leak buffers or receive further events."""
    import asyncio

    async def scenario() -> None:
        fabric_bus = EnhancedEventBus(maxsize=64, persistence_enabled=False)
        stream = fabric_bus.subscribe()
        await fabric_bus.publish(_fabric_trade())
        assert await anext(stream)
        await stream.aclose()
        # After close the queue is gone; queue_depth returns to zero.
        assert fabric_bus._queue_depth() == 0  # noqa: SLF001 -- direct unit check
        # Publishing after close must not raise (no subscriber to deliver to).
        await fabric_bus.publish(_fabric_quote())

    asyncio.run(scenario())


def test_news_pipeline_does_not_reprocess_own_output() -> None:
    """With fan-out the news pipeline receives its own re-published output.

    The processed marker must make the loop skip (not re-publish) events it
    already enriched, otherwise it would loop forever. We subscribe *before*
    publishing and assert the enriched event surfaces exactly once (no
    unbounded re-publish cycle).
    """
    import asyncio

    from backend.infrastructure.data_fabric.pipeline import NewsPipelineService

    async def scenario() -> None:
        fabric_bus = EnhancedEventBus(maxsize=64, persistence_enabled=False)
        service = NewsPipelineService()
        await service.start(fabric_bus)
        await asyncio.sleep(0.2)  # let the pipeline task register its queue

        # A single news event published once must not multiply indefinitely.
        news = NormalizedEvent(
            event_type="news",
            source_id="cointelegraph",
            source_name="CoinTelegraph",
            symbol="BTCUSDT",
            event_time=datetime(2026, 8, 20, 10, 0, 0, tzinfo=UTC),
            payload={"headline": "Bitcoin surges on ETF inflows"},
        )

        seen: list[NormalizedEvent] = []
        stream = fabric_bus.subscribe()
        try:
            await fabric_bus.publish(news)
            # Give the pipeline loop a few turns to (wrongly) re-publish if the
            # guard is missing; must converge without looping forever.
            for _ in range(8):
                try:
                    event = await asyncio.wait_for(anext(stream), timeout=0.5)
                except (TimeoutError, StopAsyncIteration):
                    break
                seen.append(event)
        finally:
            await stream.aclose()
        await service.stop()

        news_seen = [e for e in seen if e.event_type == "news"]
        # Raw event + one enriched re-publish = exactly 2; never more.
        assert len(news_seen) == 2
        enriched = [e for e in news_seen if e.payload.get("news_processed") is True]
        assert len(enriched) == 1
        assert enriched[0].payload.get("impact_score") is not None

    asyncio.run(scenario())


def test_bridge_forwards_events_end_to_end() -> None:
    import asyncio
    import contextlib

    async def scenario() -> tuple[int, list[str]]:
        fabric_bus = EnhancedEventBus(maxsize=16, persistence_enabled=False)
        observation_bus = ObservationBus(maxsize=16)
        from backend.infrastructure.observation.fabric_bridge import FabricObservationBridge

        bridge = FabricObservationBridge(fabric_bus, observation_bus)
        task = asyncio.create_task(bridge.start())
        try:
            # Wait for the bridge task to register its fabric-bus subscription,
            # otherwise events published before that point are dropped.
            for _ in range(100):
                if len(fabric_bus._subscribers) > 0:  # noqa: SLF001 -- startup check
                    break
                await asyncio.sleep(0.01)
            assert len(fabric_bus._subscribers) > 0

            await fabric_bus.publish(_fabric_trade())
            await fabric_bus.publish(_fabric_quote())

            received: list[ObservationEvent] = []
            stream = observation_bus.subscribe()
            try:
                received.append(await anext(stream))
                received.append(await anext(stream))
            finally:
                await stream.aclose()

            assert len(received) == 2
            assert received[0].event_type is ObservationEventType.TRADE
            assert received[1].event_type is ObservationEventType.TICKER
            assert bridge.stats()["bridged"] == 2
            return bridge.stats()["bridged"], [r.event_type.value for r in received]
        finally:
            bridge.stop()
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):  # noqa: BLE001
                await task

    bridged, types = asyncio.run(scenario())
    assert bridged == 2
    assert types == ["trade", "ticker"]
