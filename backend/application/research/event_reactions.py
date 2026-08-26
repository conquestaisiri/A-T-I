# backend/application/research/event_reactions.py
"""Event -> market-reaction dataset builder (calendar intelligence, Â§4).

For a released macro event and an affected symbol, measures forward returns
from recorded observations at fixed horizons (+1m, +5m, +15m, +1h, +4h, +1d).
Accumulated across events this becomes the historical reaction library that
future hypothesis tests ("does a CPI beat move EURUSD persistently?") run on.

Honesty rules:
- Horizons without observations yet are reported as ``None`` (never filled
  from fabricated prices).
- The anchor is the first recorded trade/ticker at or after the scheduled
  release time (within a small grace window); no anchor -> empty reaction.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, TypedDict

from backend.domain.macro.event import MacroEventData
from backend.infrastructure.sqlite.database import Database

DEFAULT_HORIZONS_MINUTES: tuple[int, ...] = (1, 5, 15, 60, 240, 1440)
_GRACE_SECONDS = 120


class EventReactionReport(TypedDict):
    """Forward-reaction record for one released event and one symbol."""

    event_id: str
    currency: str
    title: str
    symbol: str
    scheduled_at: str
    anchor: dict[str, Any] | None
    reactions: dict[str, float | None]
    complete: bool


def _nearest_observation(
    conn: object,
    symbol: str,
    start_iso: str,
) -> tuple[datetime, float] | None:
    """First trade/ticker at-or-after ``start``, parsed and verified.

    Stored ``event_time`` strings are millisecond-truncated, so pure SQL
    string comparison can mis-order equal instants; candidates are re-checked
    as parsed datetimes.
    """
    # Widen the SQL net by one second, then verify precisely in Python.
    probe = _shift_iso(start_iso, seconds=-1)
    rows = conn.execute(  # type: ignore[attr-defined]
        """
        SELECT event_time, payload FROM observation_events
        WHERE symbol = ?
          AND event_time >= ?
          AND event_type IN ('trade', 'ticker')
        ORDER BY event_time ASC LIMIT 50
        """,
        (symbol.upper(), probe),
    ).fetchall()
    target = datetime.fromisoformat(start_iso)
    # Storage truncates to milliseconds: tolerate <=1ms representation loss
    # while still rejecting genuinely earlier prints.
    earliest = target - timedelta(milliseconds=1)
    for row in rows:
        when = datetime.fromisoformat(str(row["event_time"]))
        if when < earliest:
            continue
        data = json.loads(str(row["payload"]))
        # observation_events.payload stores the full event envelope; the price
        # lives one level down under the event's own ``payload``.
        inner = data.get("payload", data) if isinstance(data, dict) else {}
        price = inner.get("price") if isinstance(inner, dict) else None
        if isinstance(price, (int, float)) and float(price) > 0:
            return when, float(price)
    return None


def _shift_iso(iso: str, *, seconds: int) -> str:
    base = datetime.fromisoformat(iso)
    return (base + timedelta(seconds=seconds)).isoformat()


def compute_event_reactions(
    database: Database,
    event: MacroEventData,
    symbol: str,
    *,
    horizons_minutes: tuple[int, ...] = DEFAULT_HORIZONS_MINUTES,
    as_of: datetime | None = None,
) -> EventReactionReport:
    """Forward returns (%) for ``symbol`` after ``event``'s release window.

    Anchor = first recorded trade/ticker within ``_GRACE_SECONDS`` of the
    scheduled time. Each horizon reports the % change from anchor to the
    nearest observation at-or-after anchor+h (bounded by ``as_of``/now).
    """
    scheduled = event.scheduled_at
    grace_end = scheduled + timedelta(seconds=_GRACE_SECONDS)

    with database.lock:
        # Anchor: first recorded trade/ticker at/after the release. If it lies
        # beyond the grace window there is no clean pre-drift anchor; report
        # honestly rather than anchoring on drifted prices.
        found = _nearest_observation(database.connection, symbol, scheduled.isoformat())
        if found is not None and found[0] > grace_end + timedelta(seconds=30):
            found = None

        if found is None:
            return {
                "event_id": event.event_id,
                "currency": event.currency,
                "title": event.title,
                "symbol": symbol.upper(),
                "scheduled_at": scheduled.isoformat(),
                "anchor": None,
                "reactions": {f"{m}m": None for m in horizons_minutes},
                "complete": False,
            }

        anchor_time, anchor_price = found
        limit = min(as_of or datetime.now(anchor_time.tzinfo), _now_utc_tz(anchor_time))
        # Horizons run on the EVENT clock (scheduled + m), not the anchor's —
        # so an anchor lagging the release by seconds never shifts windows.
        reactions: dict[str, float | None] = {}
        for minutes in horizons_minutes:
            target = scheduled + timedelta(minutes=minutes)
            if target > limit:
                reactions[f"{minutes}m"] = None
                continue
            obs = _nearest_observation(database.connection, symbol, target.isoformat())
            reactions[f"{minutes}m"] = (
                round((obs[1] / anchor_price - 1.0) * 100.0, 6) if obs else None
            )

    complete = all(v is not None for v in reactions.values())
    return {
        "event_id": event.event_id,
        "currency": event.currency,
        "title": event.title,
        "symbol": symbol.upper(),
        "scheduled_at": scheduled.isoformat(),
        "anchor": {
            "time": anchor_time.isoformat(),
            "price": anchor_price,
        },
        "reactions": reactions,
        "complete": complete,
    }


def _now_utc_tz(reference: datetime) -> datetime:
    """Wall-clock now pinned to the reference's tzinfo (aware arithmetic)."""
    from datetime import UTC

    return datetime.now(UTC).astimezone(reference.tzinfo or UTC)
