# backend/domain/strategy/playbook/daily_open_close.py
"""Daily open/close — London open bias + NY close reversion."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def signal(context: Any) -> dict[str, Any] | None:
    try:
        snap = context.snapshot
        # Find today's open (first event of current UTC day)
        today = datetime.now(UTC).date()
        todays = [e for e in snap.events if e.timestamp.date() == today]
        if len(todays) < 2:
            return None
        open_price = float(todays[0].payload.get("price") or todays[0].payload.get("close") or 0)
        last = float(todays[-1].payload.get("price") or todays[-1].payload.get("close") or 0)
        if open_price == 0:
            return None
        ret = (last - open_price) / open_price
        # Morning momentum continuation
        if ret > 0.002:
            return {
                "bias": "bullish",
                "pattern": "daily_open_momentum",
                "return": ret,
                "open": open_price,
            }
        if ret < -0.002:
            return {
                "bias": "bearish",
                "pattern": "daily_open_momentum",
                "return": ret,
                "open": open_price,
            }
    except Exception:
        return None
    return None
