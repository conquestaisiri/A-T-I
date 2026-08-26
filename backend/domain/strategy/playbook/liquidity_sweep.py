# backend/domain/strategy/playbook/liquidity_sweep.py
"""Buy-side / sell-side liquidity sweep (stop-hunt) strategy."""

from __future__ import annotations

from typing import Any


def signal(context: Any) -> dict[str, Any] | None:
    """Detect sweep: wick beyond recent high/low then close back inside."""
    try:
        snap = context.snapshot
        highs = [float(e.payload.get("high", 0)) for e in snap.events[-20:] if "high" in e.payload]
        lows = [float(e.payload.get("low", 0)) for e in snap.events[-20:] if "low" in e.payload]
        if len(highs) < 10:
            return None
        recent_high = max(highs[:-1])
        recent_low = min(lows[:-1])
        last = snap.events[-1].payload
        close = float(last.get("price") or last.get("close") or 0)
        high = float(last.get("high") or close)
        low = float(last.get("low") or close)
        # Sweep high then rejection
        if high > recent_high and close < recent_high:
            return {"bias": "bearish", "pattern": "buy_side_sweep", "level": recent_high}
        if low < recent_low and close > recent_low:
            return {"bias": "bullish", "pattern": "sell_side_sweep", "level": recent_low}
    except Exception:
        return None
    return None
