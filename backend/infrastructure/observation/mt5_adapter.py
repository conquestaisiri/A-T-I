"""MT5 Observation Adapter — feeds MetaTrader 5 market data into the shared ObservationBus.

This adapter runs in a background task, polling MT5 rates and ticks, and publishing
ObservationEvent instances (TICKER / TRADE shape) that the existing DecisionPipelineService,
risk gate, and PaperTradingSimulator already understand.

See investigation agent 7 report for full context:
  backend/domain/context/features/*.py — features are venue-agnostic (OHLCV)
  backend/application/pipeline/decision_pipeline_service.py — supervised + risk-gated
  backend/application/pipeline/market_loop_service.py — symbol-aware, cooldown-gated
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import Any

from backend.infrastructure.execution.mt5.bridge import MT5Bridge


async def _mt5_tick_event(bridge: MT5Bridge, symbol: str) -> dict[str, Any] | None:
    """Build a TICKER observation from the latest MT5 tick for *symbol*."""
    tick: dict[str, Any] | None = bridge.get_tick(symbol)
    if tick is None:
        return None
    mid = (tick.get("bid", 0) + tick.get("ask", 0)) / 2
    return {
        "symbol": symbol,
        "bid": tick.get("bid"),
        "ask": tick.get("ask"),
        "last": mid,
        "volume": tick.get("volume", 0.0),
    }


async def _mt5_trade_event(bridge: MT5Bridge, symbol: str) -> dict[str, Any] | None:
    """Build a TRADE observation from the latest rate bar for *symbol*."""
    rates: list[dict[str, Any]] | None = bridge.get_rates(symbol, "M1", 1)
    if not rates:
        return None
    r = rates[0]
    mid = (r.get("bid", 0) + r.get("ask", 0)) / 2
    return {
        "symbol": symbol,
        "trade_id": f"mt5-{symbol}-{_time.time()}",
        "price": mid,
        "quantity": r.get("volume", 1.0),
    }


class MT5ObservationAdapter:
    """Background task: poll MT5 and publish events onto the shared ObservationBus.

    Parameters
    ----------
    event_bus: Any
        The shared bus that feeds the ingest pipeline, supervisor freshness,
        and the decision loop.
    bridge: MT5Bridge
        Instantiated bridge (credentials + loop thread already started).
    symbols: tuple[str, ...]
        Tradeable symbols, e.g. ("EURUSD", "GBPUSD", "AUDUSD").
    tick_interval: float
        Seconds between tick polls (default 2.0).
    rate_interval: float
        Seconds between rate-bar polls (default 15.0).
    """

    def __init__(
        self,
        event_bus: Any,
        bridge: MT5Bridge,
        symbols: tuple[str, ...] = ("EURUSD", "GBPUSD", "AUDUSD"),
        tick_interval: float = 2.0,
        rate_interval: float = 15.0,
    ) -> None:
        self._bus = event_bus
        self._bridge = bridge
        self._symbols = symbols
        self._tick_interval = tick_interval
        self._rate_interval = rate_interval

    async def start(self) -> None:
        """Launch the dual-rate publisher loop."""
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        tick_cycle = 0
        rate_cycle = 0
        while True:
            # --- TICK polls (high frequency, lightweight) ---
            tick_cycle += 1
            if tick_cycle % max(1, __import__("math").ceil(60 / self._tick_interval)) == 0:
                for sym in self._symbols:
                    try:
                        ev = await _mt5_tick_event(self._bridge, sym)
                        if ev is not None:
                            await self._bus.publish(ev)
                    except Exception:  # noqa: BLE001 — one symbol must not stall the bus
                        pass

            # --- RATE-bar polls (lower frequency, full bar) ---
            rate_cycle += 1
            if rate_cycle % max(1, __import__("math").ceil(60 / self._rate_interval)) == 0:
                for sym in self._symbols:
                    try:
                        ev = await _mt5_trade_event(self._bridge, sym)
                        if ev is not None:
                            await self._bus.publish(ev)
                    except Exception:  # noqa: BLE001 — one symbol must not stall the bus
                        pass

            await asyncio.sleep(min(self._tick_interval, self._rate_interval))


def _build_adapter(event_bus: Any, bridge: MT5Bridge) -> MT5ObservationAdapter:
    """Factory: return a started adapter instance."""
    adapter = MT5ObservationAdapter(event_bus, bridge)
    asyncio.create_task(adapter.start())
    return adapter
