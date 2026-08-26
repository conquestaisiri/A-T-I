# backend/application/window_manager_impl.py
"""Concrete implementation of the WindowManager interface.

The manager stores observation events per market symbol, maintains a
time‑based rolling window, and provides immutable :class:`ContextSnapshot`
objects.  Configuration is injected via :class:`ContextSettings` at
construction time and remains immutable for the object's lifetime.

Thread safety is achieved with a re‑entrant lock protecting the internal
state.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

from backend.application.interfaces.context_settings import ContextSettings
from backend.application.interfaces.window_manager import WindowManager
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.observation.event import ObservationEvent


class InMemoryWindowManager(WindowManager):
    """Thread‑safe, in‑memory rolling window manager.

    Events are stored per symbol.  When a new event is added, events older
    than ``window_duration`` relative to the newest event's timestamp are
    discarded.  Snapshots are immutable and contain events ordered by
    timestamp.
    """

    def __init__(self, settings: ContextSettings) -> None:
        self._window_duration: timedelta = settings.window_duration
        self._events_by_symbol: dict[str, list[ObservationEvent]] = {}
        self._lock = threading.RLock()

    def add(self, event: ObservationEvent) -> None:
        """Add a single observation event to the appropriate symbol window.

        The ``symbol`` is extracted from ``event.payload['symbol']``.  If the
        payload does not contain a ``symbol`` key, a ``KeyError`` is raised –
        this is considered a configuration‑level error.
        """
        symbol = event.payload.get("symbol")
        if not isinstance(symbol, str):
            raise KeyError("ObservationEvent payload missing required 'symbol' string field")

        with self._lock:
            events = self._events_by_symbol.setdefault(symbol, [])
            events.append(event)
            # Ensure chronological order – events may arrive slightly out of order.
            events.sort(key=lambda e: e.timestamp)
            # Prune events older than the window duration relative to the newest event.
            cutoff: datetime = events[-1].timestamp - self._window_duration
            # Remove from the left while older than cutoff.
            while events and events[0].timestamp < cutoff:
                events.pop(0)

    def snapshot(self, symbol: str) -> ContextSnapshot:
        """Return an immutable snapshot for the given symbol.

        Raises ``KeyError`` if no events have been recorded for the symbol.
        """
        with self._lock:
            if symbol not in self._events_by_symbol:
                raise KeyError(f"No events stored for symbol '{symbol}'")
            events = tuple(self._events_by_symbol[symbol])
            return ContextSnapshot.from_events(events)

    def clear(self, symbol: str) -> None:
        """Clear all stored events for the provided symbol."""
        with self._lock:
            self._events_by_symbol.pop(symbol, None)
