# backend/domain/research/alt_data.py
"""Historical alternative-data contracts (task P1-006).

Alternative data — news sentiment, SEC insider/13F filings, proxy items —
is the classic backtest leakage vector: today's cache is easy to read, but a
backtest at time ``t`` may only use a news story or a filing if it had
*become public* by ``t``.

This module stores alt-data as immutable :class:`AltDataEvent` values. Every
event carries two clocks:

- ``event_timestamp``: when the underlying fact happened (the filing date, the
  article's publication time, the market observation moment).
- ``published_at``: when the system could first have known it (the story went
  live / the filing dropped on EDGAR). Backtests must filter on this clock.

The store deliberately exposes **only point-in-time queries** (a snapshot at a
cutoff) — there is no 'current value' read, so an experiment cannot silently
drift into look-ahead. The live features (SentimentFeature, InsiderFeature)
keep their caches; the research factory reads history from here.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class AltDataKind(enum.StrEnum):
    """Families of stored alternative data."""

    SENTIMENT = "sentiment"
    SEC_INSIDER = "sec_insider"
    SEC_13F = "sec_13f"
    PROXY = "proxy"


@dataclass(frozen=True, slots=True)
class AltDataEvent:
    """One immutable alternative-data observation.

    Parameters
    ----------
    event_id: str
        Unique, stable id (e.g. the article GUID or filing accession).
    symbol: str
        Subject symbol (normalised uppercase at write time by the caller).
    kind: AltDataKind
        Which family this event belongs to.
    event_timestamp: datetime
        When the fact itself happened (filing date, publication time).
    published_at: datetime
        When the data became publicly known / ingestable. Point-in-time:
        an event may enter a backtest state only when
        ``published_at <= decision time``.
    payload: Mapping[str, Any]
        The event body (signal scores, counts, title/hash for dedup, ...).
    """

    event_id: str
    symbol: str
    kind: AltDataKind
    event_timestamp: datetime
    published_at: datetime
    payload: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Serialise the event to a plain dictionary."""
        return {
            "event_id": self.event_id,
            "symbol": self.symbol,
            "kind": self.kind.value,
            "event_timestamp": self.event_timestamp.isoformat(timespec="milliseconds"),
            "published_at": self.published_at.isoformat(timespec="milliseconds"),
            "payload": dict(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AltDataEvent:
        """Reconstruct an event from :meth:`as_dict` output."""
        return cls(
            event_id=str(data["event_id"]),
            symbol=str(data["symbol"]),
            kind=AltDataKind(str(data["kind"])),
            event_timestamp=datetime.fromisoformat(str(data["event_timestamp"])),
            published_at=datetime.fromisoformat(str(data["published_at"])),
            payload=dict(data.get("payload", {})),
        )


@dataclass(frozen=True, slots=True)
class AltDataSnapshot:
    """Point-in-time view of alt-data at one cutoff.

    ``latest`` maps ``(symbol, kind) -> event`` for the most recent event with
    ``published_at <= cutoff``, plus ``events`` as the fully-filtered list so
    researchers can reconstruct series or count signals.
    """

    cutoff: datetime
    events: tuple[AltDataEvent, ...]

    @property
    def latest(self) -> dict[tuple[str, AltDataKind], AltDataEvent]:
        latest: dict[tuple[str, AltDataKind], AltDataEvent] = {}
        for event in sorted(self.events, key=lambda e: (e.symbol, e.kind.value, e.event_timestamp)):
            key = (event.symbol, event.kind)
            current = latest.get(key)
            if current is None or event.event_timestamp > current.event_timestamp:
                latest[key] = event
        return latest

    def for_symbol(self, symbol: str) -> list[AltDataEvent]:
        return [e for e in self.events if e.symbol == symbol]

    def as_dict(self) -> dict[str, Any]:
        return {
            "cutoff": self.cutoff.isoformat(timespec="milliseconds"),
            "events": [e.as_dict() for e in self.events],
        }
