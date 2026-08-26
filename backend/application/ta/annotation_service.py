# backend/application/ta/annotation_service.py
"""AI-drawn technical analysis — rule-based MVP, LLM-ready.

Computes support/resistance, trend, and suggested entry/SL/TP from recent
MT5 bars. The AI chat and the engine both consume this; the dashboard
renders it as price lines + markers on Lightweight Charts.
"""

from __future__ import annotations

from typing import Any


def compute_levels(bars: list[dict[str, Any]]) -> dict[str, Any]:
    if len(bars) < 20:
        return {"levels": [], "trend": "unknown", "bias": "neutral", "annotations": []}
    closes = [float(b["close"]) for b in bars]
    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]

    # Simple swing levels: recent highs/lows
    recent_high = max(highs[-20:])
    recent_low = min(lows[-20:])
    mid = (recent_high + recent_low) / 2

    # Trend via 20 vs 50 MA
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else ma20
    if ma20 > ma50 * 1.002:
        trend = "uptrend"
        bias = "bullish"
    elif ma20 < ma50 * 0.998:
        trend = "downtrend"
        bias = "bearish"
    else:
        trend = "ranging"
        bias = "neutral"

    # Entry zone: pullback to mid
    last = closes[-1]
    annotations: list[dict[str, Any]] = [
        {"price": recent_high, "label": "Resistance", "color": "#ff4757"},
        {"price": recent_low, "label": "Support", "color": "#00e5a0"},
        {"price": mid, "label": "Mid / Entry zone", "color": "#5b8def"},
    ]
    # SL/TP suggestion
    atr_like = (recent_high - recent_low) * 0.15
    if bias == "bullish":
        annotations.append({"price": last - atr_like * 2, "label": "SL (bull)", "color": "#ffb820"})
        annotations.append({"price": last + atr_like * 3, "label": "TP (bull)", "color": "#00e5a0"})
    elif bias == "bearish":
        annotations.append({"price": last + atr_like * 2, "label": "SL (bear)", "color": "#ffb820"})
        annotations.append({"price": last - atr_like * 3, "label": "TP (bear)", "color": "#ff4757"})

    # FVG gaps — 3-candle gap where wick leaves imbalance
    fvgs: list[dict[str, Any]] = []
    for i in range(1, len(bars) - 1):
        if lows[i + 1] > highs[i - 1]:
            fvgs.append(
                {
                    "type": "bullish_fvg",
                    "top": lows[i + 1],
                    "bottom": highs[i - 1],
                    "mid": (lows[i + 1] + highs[i - 1]) / 2,
                }
            )
        elif highs[i + 1] < lows[i - 1]:
            fvgs.append(
                {
                    "type": "bearish_fvg",
                    "top": lows[i - 1],
                    "bottom": highs[i + 1],
                    "mid": (highs[i + 1] + lows[i - 1]) / 2,
                }
            )
    fvgs = fvgs[-3:]
    for f in fvgs:
        color = "#00e5a0" if f["type"] == "bullish_fvg" else "#ff4757"
        annotations.append({"price": f["mid"], "label": f["type"], "color": color, "fvg": f})

    return {
        "symbol": "",
        "trend": trend,
        "bias": bias,
        "levels": [
            {"price": recent_high, "type": "resistance"},
            {"price": recent_low, "type": "support"},
            {"price": mid, "type": "mid"},
        ],
        "annotations": annotations,
        "fvgs": fvgs,
        "last_close": last,
        "ma20": ma20,
        "ma50": ma50,
    }
