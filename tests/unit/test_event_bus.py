"""Unit tests for InMemoryEventBus."""

from __future__ import annotations

import pytest
from backend.domain.context.errors import EventBusError
from backend.infrastructure.event_bus.in_memory_event_bus import InMemoryEventBus


class TestInMemoryEventBus:
    def test_publish_and_subscribe(self):
        bus = InMemoryEventBus()
        received = []
        bus.subscribe("TestEvent", received.append)
        bus.publish("TestEvent", {"value": 1})
        assert received == [{"value": 1}]

    def test_empty_event_name_rejected(self):
        bus = InMemoryEventBus()
        with pytest.raises(EventBusError):
            bus.publish("", {"value": 1})

    def test_handler_failure_raises_event_bus_error(self):
        bus = InMemoryEventBus()

        def bad_handler(_payload):
            raise RuntimeError("handler failed")

        bus.subscribe("FailEvent", bad_handler)
        with pytest.raises(EventBusError):
            bus.publish("FailEvent", {})
