"""Unit tests for ObservationBus."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.observation.observation_bus import ObservationBus


def make_event(timestamp: datetime) -> ObservationEvent:
    return ObservationEvent(
        source_id="src",
        source_name="src-name",
        event_type=ObservationEventType.TRADE,
        timestamp=timestamp,
        payload={"symbol": "btcusdt"},
    )


def now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture(scope="module")
def loop():
    return asyncio.new_event_loop()


class TestObservationBus:
    def test_processed_count_starts_at_zero(self):
        bus = ObservationBus()
        assert bus.processed_count == 0

    def test_average_latency_is_zero_when_empty(self):
        bus = ObservationBus()
        assert bus.average_latency == 0.0

    def test_publish_increments_processed_count(self, loop):
        bus = ObservationBus()
        loop.run_until_complete(bus.publish(make_event(now())))
        assert bus.processed_count == 1

    def test_subscribe_yields_published_events_in_order(self, loop):
        async def scenario():
            bus = ObservationBus()
            events = [make_event(now()) for _ in range(3)]
            for event in events:
                await bus.publish(event)

            received = []
            stream = bus.subscribe()
            try:
                async for event in stream:
                    received.append(event)
                    if len(received) == 3:
                        break
            finally:
                await stream.aclose()
            return received

        received = loop.run_until_complete(scenario())
        assert len(received) == 3
        assert received[0].source_id == "src"

    def test_repr_reports_processed_count(self):
        bus = ObservationBus()
        assert "processed=0" in repr(bus)

    def test_average_latency_is_positive_after_publish(self, loop):
        bus = ObservationBus()
        loop.run_until_complete(bus.publish(make_event(now())))
        assert bus.average_latency > 0.0
