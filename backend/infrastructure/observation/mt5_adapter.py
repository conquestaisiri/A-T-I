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
import contextlib
import time as _time
from datetime import UTC, datetime
from typing import Any

from backend.domain.macro.event import currencies_for_symbol
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def _observation(event_type: ObservationEventType, payload: dict[str, Any]) -> ObservationEvent:
    return ObservationEvent(
        source_id="mt5",
        source_name="MetaTrader 5",
        event_type=event_type,
        timestamp=datetime.now(UTC),
        payload=payload,
    )


async def _mt5_tick_event(bridge: Any, broker_symbol: str, canonical: str) -> dict[str, Any] | None:
    """Build a TICKER observation from the latest MT5 tick."""
    tick: dict[str, Any] | None = bridge.get_tick(broker_symbol)
    if tick is None:
        return None
    bid = float(tick.get("bid") or 0.0)
    ask = float(tick.get("ask") or 0.0)
    mid = (bid + ask) / 2 if (bid and ask) else float(tick.get("last") or 0.0)
    if mid <= 0:
        return None
    return {
        "symbol": canonical,
        "bid": bid or None,
        "ask": ask or None,
        "last": mid,
        "volume": float(tick.get("volume") or 0.0),
        "currencies": sorted(currencies_for_symbol(canonical)),
    }


async def _mt5_trade_event(
    bridge: Any, broker_symbol: str, canonical: str
) -> dict[str, Any] | None:
    """Build a TRADE observation from the latest rate bar."""
    rates: list[dict[str, Any]] | None = bridge.get_rates(broker_symbol, "M1", 1)
    if not rates:
        return None
    r = rates[0]
    price = float(r.get("close") or 0.0)
    if price <= 0:
        return None
    return {
        "symbol": canonical,
        "trade_id": f"mt5-{canonical}-{_time.time()}",
        "price": price,
        "quantity": float(r.get("volume") or 1.0),
        "currencies": sorted(currencies_for_symbol(canonical)),
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
        bridge: Any,
        symbols: tuple[str, ...] = ("EURUSD", "GBPUSD", "AUDUSD"),
        tick_interval: float = 2.0,
        rate_interval: float = 15.0,
        symbol_prefix: str = "",
    ) -> None:
        self._bus = event_bus
        self._bridge = bridge
        # Canonical (prefix-free) symbols travel in payloads so downstream
        # consumers (currency veto mapping, UI) never see broker quirks.
        self._symbols: tuple[str, ...] = tuple(s.strip().upper() for s in symbols)
        self._tick_interval = tick_interval
        self._rate_interval = rate_interval
        self._prefix = symbol_prefix
        self._task: asyncio.Task[None] | None = None

    def _broker_symbol(self, canonical: str) -> str:
        return f"{self._prefix}{canonical}" if self._prefix else canonical

    def start(self) -> asyncio.Task[None]:
        """Spawn the dual-rate publisher loop; task returned for lifespan tracking."""
        self._task = asyncio.create_task(self._run())
        return self._task

    async def poll_once(self, *, include_rates: bool = True) -> int:
        """One sweep: tick for every symbol, plus M1 bar when due.

        Returns the number of observations published. Exposed for tests so the
        sweep runs hermetically against a stub bridge without the sleep loop.
        """
        published = 0
        for canonical in self._symbols:
            broker = self._broker_symbol(canonical)
            try:
                payload = await _mt5_tick_event(self._bridge, broker, canonical)
                if payload is not None:
                    await self._bus.publish(_observation(ObservationEventType.TICKER, payload))
                    published += 1
            except Exception:  # noqa: BLE001 — one symbol must not stall the bus
                continue
            if not include_rates:
                continue
            try:
                payload = await _mt5_trade_event(self._bridge, broker, canonical)
                if payload is not None:
                    await self._bus.publish(_observation(ObservationEventType.TRADE, payload))
                    published += 1
            except Exception:  # noqa: BLE001 — one symbol must not stall the bus
                continue
        return published

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        """Dual-rate loop: ticks every ``tick_interval``, bars every ``rate_interval``."""
        while True:
            rate_due = self._is_rate_due()
            await self.poll_once(include_rates=rate_due)
            await asyncio.sleep(min(self._tick_interval, self._rate_interval))

    def _is_rate_due(self) -> bool:
        """Rate sweep runs every ``rate_interval`` seconds of wall clock."""
        now = _time.monotonic()
        if getattr(self, "_last_rate_sweep", 0.0) == 0.0:
            self._last_rate_sweep = now
            return True
        if now - self._last_rate_sweep >= self._rate_interval:
            self._last_rate_sweep = now
            return True
        return False
