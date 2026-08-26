# backend/domain/strategy/playbook/engulfing.py
"""Single-candle engulfing / pin-bar patterns."""

from __future__ import annotations

from typing import Any


def signal(context: Any) -> dict[str, Any] | None:
    try:
        snap = context.snapshot
        if len(snap.events) < 3:
            return None
        # Need OHLC on last two candles
        prev = snap.events[-2].payload
        last = snap.events[-1].payload
        po, ph, pl, pc = (float(prev.get(k) or 0) for k in ("open", "high", "low", "close"))
        o, h, lo, c = (float(last.get(k) or 0) for k in ("open", "high", "low", "close"))
        if not all([po, pc, o, c]):
            # Fallback to price-only
            return None
        # Bullish engulfing: prev red, last green engulfs body
        if pc < po and c > o and c > po and o < pc:
            return {"bias": "bullish", "pattern": "bullish_engulfing"}
        if pc > po and c < o and c < po and o > pc:
            return {"bias": "bearish", "pattern": "bearish_engulfing"}
        # Pin bar: long wick
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - lo
        if lower_wick > body * 2 and upper_wick < body * 0.5:
            return {"bias": "bullish", "pattern": "pin_bar_bull"}
        if upper_wick > body * 2 and lower_wick < body * 0.5:
            return {"bias": "bearish", "pattern": "pin_bar_bear"}
    except Exception:
        return None
    return None
