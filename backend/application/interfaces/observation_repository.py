# backend/application/interfaces/observation_repository.py
"""Port for persisting and querying ObservationEvents.

The port lives in the application layer so application services depend on an
abstraction, while the concrete SQLite implementation lives in infrastructure.
At-least-once semantics: ``save`` must not raise for a duplicate event; it
returns ``True`` when a new row was inserted and ``False`` for a replay.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.observation.event import ObservationEvent


class ObservationRepository(ABC):
    """Contract for durable storage of normalized observation events."""

    @abstractmethod
    def save(self, event: ObservationEvent) -> bool:
        """Persist an event at-least-once.

        Returns ``True`` if a new row was inserted, ``False`` if the event was
        already present (idempotent replay).
        """

    @abstractmethod
    def find_recent(self, symbol: str, limit: int = 20) -> list[ObservationEvent]:
        """Return the most recent events for a symbol, ordered by time.

        ``limit`` bounds the result set; it must be a positive integer.
        """

    @abstractmethod
    def count(self, symbol: str | None = None) -> int:
        """Return the number of persisted events, optionally per symbol."""
