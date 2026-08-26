# backend/application/research/alt_data_service.py
"""Historical alternative-data service (task P1-006).

The application-side owner of alt-data history. It records new events as they
become public and answers research queries **only as point-in-time snapshots**:
``state_at(cutoff)`` returns exactly the data public by ``cutoff``. There is no
method that reads the store 'now', which is what makes it structurally
impossible for a backtest to use an article or filing that a live feature
cache would have seen later.

The live caches that feed realtime features (SentimentService, EdgarService)
are separate; this service is the one the research factory reads from, so
backtests pull history, never the current cache.
"""

from __future__ import annotations

from datetime import datetime

from backend.application.interfaces.alt_data_store import AltDataStore
from backend.domain.research.alt_data import AltDataEvent, AltDataKind, AltDataSnapshot


class AltDataService:
    """Record and point-in-time-read historical alternative data."""

    def __init__(self, store: AltDataStore) -> None:
        self._store = store

    def record(self, event: AltDataEvent) -> AltDataEvent:
        """Persist a newly-public alt-data event (immutable insert)."""
        symbol = event.symbol.upper()
        if symbol != event.symbol:
            event = AltDataEvent(
                event_id=event.event_id,
                symbol=symbol,
                kind=event.kind,
                event_timestamp=event.event_timestamp,
                published_at=event.published_at,
                payload=event.payload,
            )
        self._store.save_event(event)
        return event

    def state_at(
        self,
        cutoff: datetime,
        *,
        symbol: str | None = None,
        kind: AltDataKind | None = None,
    ) -> AltDataSnapshot:
        """Return the point-in-time alt-data world at ``cutoff``.

        Only events with ``published_at <= cutoff`` are visible. This is the
        only way to read research alt-data; a backtest with decision time
        ``t`` calls ``state_at(t)`` and can never see later publications.
        """
        return self._store.snapshot_at(cutoff, symbol=symbol, kind=kind)

    def event_count(self) -> int:
        """Total stored events (observability)."""
        return self._store.event_count()
