"""Unit tests for BinanceAdapter normalisation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.domain.observation.event import ObservationEventType
from backend.infrastructure.observation.binance_adapter import BinanceAdapter
from backend.infrastructure.observation.observation_bus import ObservationBus


def _make_adapter() -> BinanceAdapter:
    return BinanceAdapter(
        source_id="binance_usdt_futures",
        source_name="Binance USDT Futures",
        symbol="btcusdt",
        bus=ObservationBus(),
    )


VALID_TRADE = {
    "e": "trade",
    "E": 1700000000000,
    "s": "BTCUSDT",
    "t": 12345,
    "p": "0.001",
    "q": "100",
    "b": 88,
    "a": 50,
    "T": 1699999999000,
    "m": True,
    "M": True,
}


class TestBinanceAdapterNormalize:
    def test_valid_trade_payload(self):
        adapter = _make_adapter()
        event = adapter.normalize(VALID_TRADE)

        assert event.source_id == "binance_usdt_futures"
        assert event.source_name == "Binance USDT Futures"
        assert event.event_type == ObservationEventType.TRADE
        assert event.payload["symbol"] == "BTCUSDT"
        assert event.payload["price"] == 0.001
        assert event.payload["quantity"] == 100.0
        assert event.payload["is_market_maker"] is True

    def test_timestamp_converted_to_utc_datetime(self):
        adapter = _make_adapter()
        event = adapter.normalize(VALID_TRADE)
        expected = datetime.fromtimestamp(1700000000, tz=UTC)
        assert event.timestamp == expected
        assert event.timestamp.tzinfo is not None
        assert event.timestamp.utcoffset() == timedelta(0)

    def test_symbol_lowercased_at_construction(self):
        adapter = BinanceAdapter(
            source_id="s",
            source_name="n",
            symbol="BTCUSDT",
            bus=ObservationBus(),
        )
        assert adapter.symbol == "btcusdt"

    def test_missing_required_field_raises_value_error(self):
        adapter = _make_adapter()
        payload = dict(VALID_TRADE)
        del payload["p"]
        with pytest.raises(ValueError):
            adapter.normalize(payload)

    def test_missing_event_time_raises_value_error(self):
        adapter = _make_adapter()
        payload = dict(VALID_TRADE)
        del payload["E"]
        with pytest.raises(ValueError):
            adapter.normalize(payload)

    def test_numeric_strings_are_parsed(self):
        adapter = _make_adapter()
        event = adapter.normalize(VALID_TRADE)
        assert isinstance(event.payload["price"], float)
        assert isinstance(event.payload["quantity"], float)

    def test_serialisable(self):
        adapter = _make_adapter()
        event = adapter.normalize(VALID_TRADE)
        assert "timestamp" in event.model_dump_json()
