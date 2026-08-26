# backend/application/interfaces/macro_calendar.py
"""Port: economic-calendar lookups used by the pre-trade event-risk veto.

The decision pipeline depends on this abstraction, never on SQLite or the
Forex Factory connector directly, so backtests/campaigns stay fully
deterministic by simply omitting it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from backend.domain.macro.event import MacroEventData


class MacroCalendar(Protocol):
    """Read-side calendar queries (release state included)."""

    def high_impact_within(
        self,
        symbol: str,
        *,
        now: datetime,
        pre_minutes: int,
        post_minutes: int,
    ) -> MacroEventData | None:
        """Return the High-impact event gating ``symbol`` right now, if any.

        Covers both sides of a release: an upcoming event within
        ``pre_minutes``, or a just-released one within ``post_minutes``
        (post-release spreads widen; standing aside is still correct).
        """
        ...
