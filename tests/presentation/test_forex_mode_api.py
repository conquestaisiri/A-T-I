# tests/presentation/test_forex_mode_api.py
"""Forex-first mode: /v1/operator/system contract + MT5 routes passthrough."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from backend.domain.observation.event import ObservationEventType
from backend.infrastructure.observation.mt5_adapter import MT5ObservationAdapter
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def api(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    from backend.presentation.api.routes_operator import router as operator_router

    monkeypatch.setenv("TRADING_MODE", "forex")
    import importlib

    from backend.infrastructure.config.settings import Settings

    settings_mod = importlib.import_module("backend.infrastructure.config.settings")
    fresh = Settings(_env_file=None)  # type: ignore[call-arg]
    monkeypatch.setattr(settings_mod, "settings", fresh, raising=True)

    app = FastAPI()
    app.include_router(operator_router)
    return TestClient(app)


def test_system_endpoint_reports_forex_mode(api: TestClient) -> None:
    resp = api.get("/v1/operator/system")
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "forex"
    assert "EURUSD" in body["forex_symbols"]
    assert "GBPJPY" in body["forex_symbols"]
    # 28 fx pairs + gold
    assert len(body["forex_symbols"]) >= 28


class _StubBridge:
    """Duck-typed bridge: records requested (broker) symbols, returns fixtures."""

    def __init__(self) -> None:
        self.requested_ticks: list[str] = []
        self.requested_rates: list[str] = []

    def get_tick(self, symbol: str) -> dict[str, Any] | None:
        self.requested_ticks.append(symbol)
        return {"symbol": symbol, "bid": 1.0850, "ask": 1.0852, "last": 0.0, "volume": 0.0}

    def get_rates(self, symbol: str, timeframe: str = "M1", count: int = 1) -> list[dict[str, Any]]:
        self.requested_rates.append(symbol)
        return [{"time": 0, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0851, "volume": 5}]


class _CollectingBus:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> None:
        self.events.append(event)


def test_adapter_sweep_uses_prefix_and_publishes_canonical() -> None:
    bridge = _StubBridge()
    bus = _CollectingBus()
    adapter = MT5ObservationAdapter(
        bus,
        bridge,
        symbols=("eurusd", "gbpjpy"),
        symbol_prefix="frx",
    )

    published = asyncio.run(adapter.poll_once(include_rates=True))

    # Broker saw prefixed names; payloads carry canonical ones.
    assert set(bridge.requested_ticks) == {"frxEURUSD", "frxGBPJPY"}
    symbols_in_payloads = {e.payload["symbol"] for e in bus.events}
    assert symbols_in_payloads == {"EURUSD", "GBPJPY"}
    types = {e.event_type for e in bus.events}
    assert types == {ObservationEventType.TICKER, ObservationEventType.TRADE}
    # Currency mapping rides along so the event veto works without guessing.
    for e in bus.events:
        if e.payload["symbol"] == "GBPJPY":
            assert sorted(e.payload["currencies"]) == ["GBP", "JPY"]
    assert published == 4  # 2 symbols x (tick + bar)


def test_adapter_tolerates_dead_symbol(tmp_path: Any) -> None:
    class DeadBridge(_StubBridge):
        def get_tick(self, symbol: str):  # type: ignore[override]
            return None

        def get_rates(self, symbol: str, timeframe: str = "M1", count: int = 1):  # type: ignore[override]
            return []

    bus = _CollectingBus()
    adapter = MT5ObservationAdapter(bus, DeadBridge(), symbols=("EURUSD",))
    published = asyncio.run(adapter.poll_once())
    assert published == 0
    assert bus.events == []
