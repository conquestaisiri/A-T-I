"""Unit tests for the CCXT observation adapter.

The CCXT runtime and network are never touched: the adapter is constructed
with an injected fake exchange factory, so all tests are deterministic and
offline. This mirrors how ``test_binance_adapter.py`` isolates normalisation
from the websocket.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.ccxt_config import CcxtVenueConfig
from backend.infrastructure.observation import ccxt_adapter
from backend.infrastructure.observation.ccxt_adapter import (
    CcxtObservationAdapter,
    _detect_event_type,
    _ms_to_utc,
    normalize_ccxt,
)
from backend.infrastructure.observation.observation_bus import ObservationBus
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Fixtures – sample CCXT unified payloads
# ---------------------------------------------------------------------------
CCXT_TRADE = {
    "id": "12345",
    "timestamp": 1700000000000,
    "datetime": "2023-11-14T22:13:20.000Z",
    "symbol": "BTC/USDT",
    "side": "buy",
    "price": "37000.0",
    "amount": "0.5",
    "cost": "18500.0",
    "takerOrMaker": "taker",
    "fee": {"cost": "9.25", "currency": "USDT"},
}

CCXT_TICKER = {
    "symbol": "BTC/USDT",
    "timestamp": 1700000000000,
    "datetime": "2023-11-14T22:13:20.000Z",
    "bid": 36990.0,
    "ask": 37010.0,
    "last": 37000.0,
    "high": 37500.0,
    "low": 36500.0,
    "open": 36800.0,
    "close": 37000.0,
    "vwap": 36950.0,
    "change": 200.0,
    "percentage": 0.54,
    "baseVolume": 1500.0,
    "quoteVolume": 55425000.0,
}

CCXT_ORDER_BOOK = {
    "symbol": "BTC/USDT",
    "bids": [
        [36990.0, 1.0],
        [36980.0, 2.0],
        [36970.0, 3.0],
    ],
    "asks": [
        [37010.0, 1.0],
        [37020.0, 2.0],
        [37030.0, 3.0],
    ],
    "timestamp": 1700000000000,
    "nonce": 42,
}


def _make_adapter(
    *,
    config_overrides: dict[str, Any] | None = None,
    adapter_overrides: dict[str, Any] | None = None,
) -> CcxtObservationAdapter:
    config = CcxtVenueConfig(**(config_overrides or {}))
    return CcxtObservationAdapter(
        config=config,
        bus=ObservationBus(),
        **(adapter_overrides or {}),
    )


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------
class TestCcxtVenueConfig:
    def test_defaults(self) -> None:
        config = CcxtVenueConfig()
        assert config.venue_id == "binance"
        assert config.sandbox is True
        assert config.rate_limit_buffer == 0.8
        assert config.default_symbol == "BTC/USDT"
        assert config.enable_websocket is False
        assert config.market_type == "spot"

    def test_market_type_valid(self) -> None:
        for market_type in ("spot", "swap", "future", "delivery"):
            config = CcxtVenueConfig(market_type=market_type)
            assert config.market_type == market_type

    def test_invalid_market_type_raises(self) -> None:
        with pytest.raises(ValueError):
            CcxtVenueConfig(market_type="margin")
        with pytest.raises(ValueError):
            CcxtVenueConfig(market_type="")

    def test_empty_venue_id_raises(self) -> None:
        with pytest.raises(ValueError):
            CcxtVenueConfig(venue_id="")

    def test_rate_limit_buffer_out_of_range(self) -> None:
        with pytest.raises(ValueError):
            CcxtVenueConfig(rate_limit_buffer=0.0)
        with pytest.raises(ValueError):
            CcxtVenueConfig(rate_limit_buffer=1.1)

    def test_empty_symbol_raises(self) -> None:
        with pytest.raises(ValueError):
            CcxtVenueConfig(default_symbol="")


# ---------------------------------------------------------------------------
# Timestamp helper
# ---------------------------------------------------------------------------
class TestMsToUtc:
    def test_converts_milliseconds(self) -> None:
        result = _ms_to_utc(1700000000000)
        expected = datetime.fromtimestamp(1700000000, tz=UTC)
        assert result == expected
        assert result.utcoffset() == timedelta(0)


# ---------------------------------------------------------------------------
# Event-type detection
# ---------------------------------------------------------------------------
class TestDetectEventType:
    def test_trade_detected(self) -> None:
        assert _detect_event_type(CCXT_TRADE) is ObservationEventType.TRADE

    def test_ticker_detected(self) -> None:
        assert _detect_event_type(CCXT_TICKER) is ObservationEventType.TICKER

    def test_order_book_detected(self) -> None:
        assert _detect_event_type(CCXT_ORDER_BOOK) is ObservationEventType.ORDER_BOOK

    def test_unknown_payload_raises(self) -> None:
        with pytest.raises(ValueError):
            _detect_event_type({"foo": "bar"})


# ---------------------------------------------------------------------------
# Normalisation – pure functions
# ---------------------------------------------------------------------------
class TestNormalizeCcxt:
    def test_trade(self) -> None:
        event = normalize_ccxt("binance_btc", "Binance", CCXT_TRADE)
        assert event.event_type is ObservationEventType.TRADE
        assert event.source_id == "binance_btc"
        assert event.payload["symbol"] == "BTC/USDT"
        assert event.payload["price"] == 37000.0
        assert event.payload["quantity"] == 0.5
        assert event.payload["side"] == "buy"
        assert event.payload["cost"] == 18500.0
        assert event.payload["taker_or_maker"] == "taker"
        assert event.payload["fee_cost"] == 9.25

    def test_trade_timestamp(self) -> None:
        event = normalize_ccxt("s", "n", CCXT_TRADE)
        assert event.timestamp == datetime.fromtimestamp(1700000000, tz=UTC)

    def test_ticker(self) -> None:
        event = normalize_ccxt("s", "n", CCXT_TICKER)
        assert event.event_type is ObservationEventType.TICKER
        assert event.payload["bid"] == 36990.0
        assert event.payload["ask"] == 37010.0
        assert event.payload["last"] == 37000.0
        assert event.payload["high"] == 37500.0
        assert event.payload["base_volume"] == 1500.0

    def test_order_book_depth_capped(self) -> None:
        raw = dict(CCXT_ORDER_BOOK)
        raw["bids"] = [[36990.0 - i, 1.0] for i in range(20)]
        raw["asks"] = [[37010.0 + i, 1.0] for i in range(20)]
        event = normalize_ccxt("s", "n", raw)
        assert event.event_type is ObservationEventType.ORDER_BOOK
        assert len(event.payload["bids"]) == ccxt_adapter._ORDER_BOOK_DEPTH
        assert len(event.payload["asks"]) == ccxt_adapter._ORDER_BOOK_DEPTH

    def test_missing_timestamp_raises(self) -> None:
        raw = dict(CCXT_TRADE)
        del raw["timestamp"]
        with pytest.raises(ValueError):
            normalize_ccxt("s", "n", raw)

    def test_unrecognisable_payload_raises(self) -> None:
        with pytest.raises(ValueError):
            normalize_ccxt("s", "n", {"symbol": "BTC/USDT"})

    def test_result_is_frozen_and_serialisable(self) -> None:
        event = normalize_ccxt("s", "n", CCXT_TRADE)
        dumped = event.model_dump_json()
        assert "timestamp" in dumped
        with pytest.raises(ValidationError):
            event.source_id = "other"  # frozen – should raise


# ---------------------------------------------------------------------------
# Adapter – construction, port methods, lifecycle
# ---------------------------------------------------------------------------
class TestCcxtObservationAdapter:
    def test_default_source_id(self) -> None:
        adapter = _make_adapter(config_overrides={"default_symbol": "BTC/USDT"})
        assert adapter.source_id == "binance_BTC/USDT"

    def test_custom_source_id(self) -> None:
        adapter = _make_adapter(
            adapter_overrides={"source_id": "my_binance", "source_name": "My Binance"}
        )
        assert adapter.source_id == "my_binance"
        assert adapter.source_name == "My Binance"

    def test_normalize_delegates_to_pure_function(self) -> None:
        adapter = _make_adapter()
        event = adapter.normalize(CCXT_TRADE)
        assert event.event_type is ObservationEventType.TRADE
        assert event.source_id == adapter.source_id

    async def _run_subscribe_health(self) -> None:
        adapter = _make_adapter()
        await adapter.subscribe(["trade", "ticker"])
        health = await adapter.health()
        assert health["connected"] is False
        assert health["venue_id"] == "binance"
        assert health["mode"] == "polling"
        assert health["subscribed"] == ["trade", "ticker"]

    def test_subscribe_and_health(self) -> None:
        import asyncio

        asyncio.run(self._run_subscribe_health())

    async def _run_connect_disconnect(self) -> None:
        loaded: list[str] = []
        closed: list[bool] = []

        class FakeExchange:
            rate_limit = 1000
            symbol = "BTC/USDT"

            async def load_markets(self) -> None:
                loaded.append("loaded")

            async def close(self) -> None:
                closed.append(True)

            def set_sandbox_mode(self, enabled: bool) -> None:
                pass

        def factory(config: CcxtVenueConfig) -> Any:
            return FakeExchange()

        config = CcxtVenueConfig(sandbox=True)
        adapter = CcxtObservationAdapter(
            config=config, bus=ObservationBus(), exchange_factory=factory
        )
        await adapter.connect()
        health = await adapter.health()
        assert health["connected"] is True
        assert loaded == ["loaded"]
        await adapter.disconnect()
        assert closed == [True]
        health = await adapter.health()
        assert health["connected"] is False

    def test_connect_disconnect(self) -> None:
        import asyncio

        asyncio.run(self._run_connect_disconnect())

    async def _run_publish_loop(self) -> None:
        class FakeExchange:
            rate_limit = 1000
            symbol = "BTC/USDT"

            async def load_markets(self) -> None:
                pass

            async def close(self) -> None:
                pass

            def set_sandbox_mode(self, enabled: bool) -> None:
                pass

            async def fetch_trades(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]:
                return [dict(CCXT_TRADE), dict(CCXT_TRADE)]

        def factory(config: CcxtVenueConfig) -> Any:
            return FakeExchange()

        bus = ObservationBus()
        config = CcxtVenueConfig(enable_websocket=False)
        adapter = CcxtObservationAdapter(config=config, bus=bus, exchange_factory=factory)
        await adapter.connect()
        try:
            await adapter._poll_once()
        finally:
            await adapter.disconnect()

        published: list[ObservationEvent] = []
        while not bus._queue.empty():
            published.append(bus._queue.get_nowait())

        assert len(published) == 2
        assert all(e.event_type is ObservationEventType.TRADE for e in published)

    def test_poll_once_publishes_normalised_events(self) -> None:
        import asyncio

        asyncio.run(self._run_publish_loop())


# ---------------------------------------------------------------------------
# compute_order_book_delta – P0-005: old/new size semantics
# ---------------------------------------------------------------------------
def test_delta_captures_old_and_new_size() -> None:
    prev_bids = [[100.0, 5.0], [99.0, 3.0]]
    prev_asks = [[100.5, 4.0]]
    new_bids = [[100.0, 7.0], [98.0, 2.0]]  # 100.0 updated, 99.0 removed, 98.0 added
    new_asks = [[100.5, 4.0]]  # unchanged -> no ask deltas

    delta = ccxt_adapter.compute_order_book_delta(prev_bids, prev_asks, new_bids, new_asks)

    by_action = {d["action"]: d for d in delta["bids"]}
    assert set(by_action) == {"update", "remove", "add"}

    assert by_action["update"] == {
        "price": 100.0,
        "old_size": 5.0,
        "new_size": 7.0,
        "size": 7.0,
        "action": "update",
    }
    assert by_action["remove"] == {
        "price": 99.0,
        "old_size": 3.0,
        "new_size": 0.0,
        "size": 0.0,
        "action": "remove",
    }
    assert by_action["add"] == {
        "price": 98.0,
        "old_size": 0.0,
        "new_size": 2.0,
        "size": 2.0,
        "action": "add",
    }
    assert delta["asks"] == []


def test_delta_ignores_unchanged_levels_and_empty_books() -> None:
    prev_bids = [[100.0, 5.0]]
    prev_asks = [[100.5, 4.0]]

    assert ccxt_adapter.compute_order_book_delta(prev_bids, prev_asks, prev_bids, prev_asks) == {
        "bids": [],
        "asks": [],
    }
    assert ccxt_adapter.compute_order_book_delta([], [], [], []) == {"bids": [], "asks": []}


async def _run_delta_publish() -> None:
    calls = {"n": 0}

    class FakeExchange:
        rate_limit = 1000
        symbol = "BTC/USDT"

        async def load_markets(self) -> None:
            pass

        async def close(self) -> None:
            pass

        def set_sandbox_mode(self, enabled: bool) -> None:
            pass

        async def fetch_order_book(self, symbol: str, **kwargs: Any) -> dict[str, Any]:
            calls["n"] += 1
            if calls["n"] >= 3:
                return {
                    "timestamp": 1700000000000,
                    "bids": [[100.0, 6.0]],
                    "asks": [[100.5, 4.0]],
                    "symbol": "BTC/USDT",
                }
            return {
                "timestamp": 1700000000000,
                "bids": [[100.0, 5.0]],
                "asks": [[100.5, 4.0]],
                "symbol": "BTC/USDT",
            }

    def factory(config: CcxtVenueConfig) -> Any:
        return FakeExchange()

    bus = ObservationBus()
    config = CcxtVenueConfig(enable_websocket=False)
    adapter = CcxtObservationAdapter(config=config, bus=bus, exchange_factory=factory)
    await adapter.connect()
    await adapter.subscribe(["order_book"])
    try:
        # First poll publishes a snapshot (no previous state -> no delta).
        await adapter._poll_once()
        # Second poll with the same book -> no delta either.
        await adapter._poll_once()
        # Third poll with a changed book -> one delta.
        await adapter._poll_once()
    finally:
        await adapter.disconnect()

    published: list[ObservationEvent] = []
    while not bus._queue.empty():
        published.append(bus._queue.get_nowait())

    deltas = [e for e in published if e.payload.get("delta")]
    assert len(deltas) == 1
    delta_event = deltas[0]
    assert delta_event.payload["delta_seq"] == 1
    assert delta_event.payload["synthetic"] is True
    assert delta_event.payload["bids"] == [
        {
            "price": 100.0,
            "old_size": 5.0,
            "new_size": 6.0,
            "size": 6.0,
            "action": "update",
        }
    ]


def test_delta_event_published_as_synthetic() -> None:
    import asyncio

    asyncio.run(_run_delta_publish())
