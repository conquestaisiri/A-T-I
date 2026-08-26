# tests/application/test_macro_calendar_service.py
"""Hermetic tests: poll -> upsert -> publish exactly once per release."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from backend.application.pipeline.macro_calendar_service import MacroCalendarService
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.macro_event_repository import SqliteMacroEventRepository


def _ff_item(actual: str = "") -> dict[str, str]:
    return {
        "title": "Core PCE Price Index m/m",
        "country": "USD",
        "date": "2026-08-26T08:30:00-04:00",
        "impact": "High",
        "forecast": "0.2%",
        "previous": "0.1%",
        **({"actual": actual} if actual else {}),
    }


def _build(tmp_path, fetch_items: list[dict[str, str]]):
    database = Database(tmp_path / "macro.db")
    bus = ObservationBus(maxsize=64)
    repo = SqliteMacroEventRepository(database)

    async def fetcher() -> list[dict[str, str]]:
        return fetch_items

    service = MacroCalendarService(bus, repo, fetcher=fetcher, poll_seconds=60)
    return database, bus, repo, service


def test_schedule_then_release_publishes_exactly_once(tmp_path) -> None:
    database, bus, repo, _service = _build(tmp_path, [])
    subscriber = bus.subscribe()

    async def scenario() -> None:
        # Poll 1: scheduled only.
        service1 = MacroCalendarService(
            bus, repo, fetcher=_make_fetcher([_ff_item()]), poll_seconds=60
        )
        published = await service1.poll_once()
        assert published == 0
        stored = list(repo.list_between(datetime(2026, 1, 1), datetime(2027, 1, 1)))
        assert len(stored) == 1
        assert not stored[0].released

        # Poll 2: actual lands -> single release transition.
        service2 = MacroCalendarService(
            bus,
            repo,
            fetcher=_make_fetcher([_ff_item(actual="0.3%")]),
            poll_seconds=60,
        )
        published = await service2.poll_once()
        assert published == 1

        # Poll 3: identical payload -> idempotent, no duplicate release.
        published = await service2.poll_once()
        assert published == 0

    asyncio.run(scenario())

    # Drain the bus: exactly one MACRO observation.
    async def drain() -> int:
        count = 0
        try:
            while True:
                event = await asyncio.wait_for(anext(subscriber), timeout=0.2)
                assert event.event_type.value == "macro"
                assert event.payload["symbol"] == "MACRO:USD"
                assert event.payload["actual"] == 0.3
                assert event.payload["headline_surprise"] is not None
                count += 1
        except (StopAsyncIteration, TimeoutError):
            pass
        return count

    assert asyncio.run(drain()) == 1
    database.close()


def _make_fetcher(items: list[dict[str, str]]):
    async def fetcher() -> list[dict[str, str]]:
        return items

    return fetcher


def test_upsert_transition_semantics(tmp_path) -> None:
    database = Database(tmp_path / "macro2.db")
    repo = SqliteMacroEventRepository(database)
    base = datetime.now(UTC).astimezone()

    from backend.domain.macro.event import MacroEventData

    def event_with(actual: float | None) -> MacroEventData:
        return MacroEventData(
            event_id="fixed-id",
            currency="USD",
            title="Core PCE Price Index m/m",
            scheduled_at=base,
            impact="High",
            forecast=0.2,
            previous=0.1,
            actual=actual,
        )

    first = repo.upsert(event_with(None), seen_at=base)
    second = repo.upsert(event_with(0.3), seen_at=base + timedelta(seconds=5))
    third = repo.upsert(event_with(0.3), seen_at=base + timedelta(seconds=10))

    assert first == "updated"
    assert second == "released"
    assert third == "unchanged"
    stored = repo.get("fixed-id")
    assert stored is not None and stored.released
    assert stored in repo.recent_released(limit=10)
    database.close()
