# backend/domain/context/context_snapshot.py
"""Immutable snapshot of observation events used for context building.

The snapshot holds a chronologically ordered immutable collection of
:class:`~backend.domain.observation.event.ObservationEvent` instances.
It provides a lightweight ``as_dict`` for serialization.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from backend.domain.observation.event import ObservationEvent


@dataclass(frozen=True, slots=True)
class ContextSnapshot:
    """Immutable collection of observation events.

    Attributes
    ----------
    events: Tuple[ObservationEvent, ...]
        Ordered events captured up to a point in time.
    start_timestamp: datetime
        Timestamp of the earliest event in the snapshot.
    end_timestamp: datetime
        Timestamp of the latest event in the snapshot.
    """

    events: tuple[ObservationEvent, ...]
    start_timestamp: datetime
    end_timestamp: datetime

    @property
    def symbol(self) -> str:
        """Trading symbol covered by the snapshot, taken from the first event."""
        if not self.events:
            raise ValueError("Cannot derive a symbol from an empty snapshot")
        raw = self.events[0].payload.get("symbol")
        if not isinstance(raw, str) or not raw:
            raise ValueError("Snapshot events carry no 'symbol' in their payload")
        return raw

    @classmethod
    def from_events(cls, events: tuple[ObservationEvent, ...]) -> ContextSnapshot:
        """Create a snapshot from a sequence of events.
        The sequence must be non‑empty and ordered by ``timestamp``.
        """
        if not events:
            raise ValueError("Cannot create ContextSnapshot from empty event list")
        start = events[0].timestamp
        end = events[-1].timestamp
        return cls(events=events, start_timestamp=start, end_timestamp=end)

    def as_dict(self) -> dict[str, object]:
        """Serialise the snapshot to a plain dictionary.
        Returns a mapping with ``events`` as a list of dicts and timestamps.
        """
        return {
            "start_timestamp": self.start_timestamp.isoformat(timespec="milliseconds"),
            "end_timestamp": self.end_timestamp.isoformat(timespec="milliseconds"),
            "events": [e.model_dump(mode="json") for e in self.events],
        }

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> ContextSnapshot:
        """Reconstruct a snapshot from the output of :meth:`as_dict`."""
        raw_events = data["events"]
        if not isinstance(raw_events, list):
            raise ValueError("ContextSnapshot dict must contain an 'events' list")
        events = tuple(ObservationEvent.model_validate(event) for event in raw_events)
        return cls.from_events(events)
