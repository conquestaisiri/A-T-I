# backend/application/pipeline/macro_calendar_service.py
"""Economic-calendar poller: official Forex Factory weekly JSON -> bus + store.

Responsibilities (and non-responsibilities):
- Polls the documented JSON export (ICS/CSV/JSON/XML are FF's official
  formats; no HTML scraping), upserts every event durably, and publishes
  exactly one ``ObservationEvent(MACRO)`` per SCHEDULED -> RELEASED
  transition onto the trading ObservationBus.
- It NEVER decides anything. The calendar is evidence; the veto in the
  decision pipeline and the reaction-research dataset decide what it means.

Hermetic by construction: the HTTP fetch is an injected async callable, so
tests run without network and CI stays deterministic.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any

from backend.domain.macro.event import MacroEventData, compute_event_surprise
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.macro_event_repository import SqliteMacroEventRepository

logger = logging.getLogger(__name__)

JsonFetcher = Callable[[], Awaitable[list[dict[str, Any]]]]


def make_http_json_fetcher(url: str) -> JsonFetcher:
    """Default fetcher: aiohttp GET of the official weekly export."""

    async def _fetch() -> list[dict[str, Any]]:
        import aiohttp

        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "ATI-MacroCalendar/1.0"},
        ) as session, session.get(url) as resp:
            resp.raise_for_status()
            data = await resp.json(content_type=None)
        return data if isinstance(data, list) else []

    return _fetch


class MacroCalendarService:
    """Poll -> upsert -> publish release transitions. Nothing more."""

    def __init__(
        self,
        bus: ObservationBus,
        repository: SqliteMacroEventRepository,
        *,
        fetcher: JsonFetcher,
        poll_seconds: int = 300,
    ) -> None:
        self._bus = bus
        self._repo = repository
        self._fetcher = fetcher
        self._poll_seconds = max(30, int(poll_seconds))
        self._running = True
        self.events_seen = 0
        self.releases_published = 0
        self._task: asyncio.Task[None] | None = None

    def start(self) -> asyncio.Task[None]:
        """Spawn the poll loop; returns the task for lifespan tracking."""
        self._task = asyncio.create_task(self._run())
        return self._task

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        logger.info("MacroCalendarService started (poll=%ss)", self._poll_seconds)
        while self._running:
            try:
                await self.poll_once()
            except Exception:  # noqa: BLE001 -- a failed poll must never kill the loop
                logger.exception("Macro calendar poll failed")
            await asyncio.sleep(self._poll_seconds)
        logger.info("MacroCalendarService stopped")

    async def poll_once(self, *, now: datetime | None = None) -> int:
        """One fetch+upsert+publish cycle. Returns releases published."""
        seen_at = now or datetime.now().astimezone()
        items = await self._fetcher()
        published = 0
        for item in items:
            event = MacroEventData.from_ff_json(item)
            if event is None:
                continue
            self.events_seen += 1
            result = self._repo.upsert(event, seen_at=seen_at)
            if result == "released":
                published += 1
                await self._publish_release(event)
        if published:
            logger.info("Macro calendar: %d release(s) published", published)
        self.releases_published += published
        return published

    async def _publish_release(self, event: MacroEventData) -> None:
        surprise = compute_event_surprise(
            actual=event.actual or 0.0,
            forecast=event.forecast,
            previous=event.previous,
        )
        payload = {
            "symbol": f"MACRO:{event.currency}",
            "event_id": event.event_id,
            "source": "forex_factory",
            "currency": event.currency,
            "title": event.title,
            "impact": event.impact,
            "scheduled_at": event.scheduled_at.isoformat(),
            "actual": event.actual,
            "forecast": event.forecast,
            "previous": event.previous,
            "headline_surprise": surprise.headline_surprise,
            "net_surprise": surprise.net_surprise,
        }
        observation = ObservationEvent(
            source_id="forex_factory",
            source_name="Forex Factory Calendar",
            event_type=ObservationEventType.MACRO,
            timestamp=datetime.now(event.scheduled_at.tzinfo),
            payload=payload,
        )
        await self._bus.publish(observation)
