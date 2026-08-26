# backend/infrastructure/event_bus/in_memory_event_bus.py
"""Thread-safe in-memory EventBus implementation for development and testing."""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from backend.application.interfaces.event_bus import EventBus
from backend.domain.context.errors import EventBusError

logger = logging.getLogger(__name__)

EventHandler = Callable[[Any], None]


class InMemoryEventBus(EventBus):
    """Simple synchronous event bus backed by in-memory subscriber lists.

    Subscribers can be registered via :meth:`subscribe` for test assertions.
    Publishing is thread-safe and invokes handlers synchronously.
    """

    def __init__(self) -> None:
        self._subscribers: defaultdict[str, list[EventHandler]] = defaultdict(list)
        self._published: list[tuple[str, Any]] = []
        self._lock = threading.RLock()

    def publish(self, event_name: str, payload: Any) -> None:
        """Publish an event to all registered subscribers."""
        if not event_name:
            raise EventBusError("event_name must be a non-empty string")

        with self._lock:
            self._published.append((event_name, payload))
            handlers = list(self._subscribers.get(event_name, []))

        logger.debug("Publishing event %s", event_name)
        for handler in handlers:
            try:
                handler(payload)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Event handler failed for %s: %s", event_name, exc)
                raise EventBusError(f"Handler failed for event '{event_name}': {exc}") from exc

    def subscribe(self, event_name: str, handler: EventHandler) -> None:
        """Register a handler for the given event name."""
        with self._lock:
            self._subscribers[event_name].append(handler)

    def published_events(self) -> list[tuple[str, Any]]:
        """Return a copy of all published events (useful for tests)."""
        with self._lock:
            return list(self._published)

    def clear(self) -> None:
        """Remove all subscribers and published history."""
        with self._lock:
            self._subscribers.clear()
            self._published.clear()
