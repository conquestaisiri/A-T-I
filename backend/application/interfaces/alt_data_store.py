# backend/application/interfaces/alt_data_store.py
"""Port for the historical alternative-data store (task P1-006).

The store persists immutable :class:`AltDataEvent` values and answers
**point-in-time** queries: a researcher asks for the world at a cutoff and
receives exactly the events that were *public* by that cutoff. There is no
"current value" read on this port — the live feature caches (sentiment,
insider) live elsewhere; backtests must never consult them.

Implementations must guarantee:

- events are immutable: ``save_event`` rejects a duplicate ``event_id`` and
  never overwrites;
- ``snapshot_at`` filters strictly on ``published_at <= cutoff`` (an event
  with a future publication time is invisible before that time);
- reads are deterministic (stable ordering).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from backend.domain.research.alt_data import AltDataEvent, AltDataKind, AltDataSnapshot


class AltDataStore(ABC):
    """Contract for persisting and point-in-time reading alt-data events."""

    @abstractmethod
    def save_event(self, event: AltDataEvent) -> None:
        """Persist a new alt-data event (immutable; duplicate id raises)."""
        raise NotImplementedError

    @abstractmethod
    def snapshot_at(
        self,
        cutoff: datetime,
        *,
        symbol: str | None = None,
        kind: AltDataKind | None = None,
    ) -> AltDataSnapshot:
        """Return the point-in-time world at ``cutoff``.

        Includes only events with ``published_at <= cutoff``, optionally
        filtered by symbol and/or kind.
        """
        raise NotImplementedError

    @abstractmethod
    def event_count(self) -> int:
        """Total stored events (observability)."""
        raise NotImplementedError
