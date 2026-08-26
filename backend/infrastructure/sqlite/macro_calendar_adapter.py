# backend/infrastructure/sqlite/macro_calendar_adapter.py
"""SQLite-backed implementation of the MacroCalendar veto port."""

from __future__ import annotations

from datetime import datetime, timedelta

from backend.domain.macro.event import MacroEventData, currencies_for_symbol
from backend.infrastructure.sqlite.macro_event_repository import SqliteMacroEventRepository


class SqliteMacroCalendar:
    """Answers 'is this symbol gated by a High-impact event right now?'."""

    def __init__(self, repository: SqliteMacroEventRepository) -> None:
        self._repo = repository

    def high_impact_within(
        self,
        symbol: str,
        *,
        now: datetime,
        pre_minutes: int,
        post_minutes: int,
    ) -> MacroEventData | None:
        currencies = currencies_for_symbol(symbol)
        if not currencies:
            return None

        # Upcoming release inside the pre-event window.
        upcoming = self._repo.next_high_impact_for_currencies(
            currencies, now=now, within_minutes=pre_minutes
        )
        if upcoming is not None:
            return upcoming

        # Just-released inside the post-event window (still released state).
        window_start = now - timedelta(minutes=post_minutes)
        for event in self._repo.list_between(window_start, now, impacts={"High"}):
            if event.currency in currencies and event.released:
                return event
        return None
