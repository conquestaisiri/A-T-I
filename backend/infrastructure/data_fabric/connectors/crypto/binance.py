"""Binance WebSocket connector - public market data streams.

Binance provides public WebSocket streams for trades, tickers, order books,
klines, and more. No authentication required for market data.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import websockets

from .....domain.data_fabric.enums import AssetClass, ConnectionState, DataPlane
from .....domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from .....domain.data_fabric.instrument import InstrumentMaster
from .....domain.data_fabric.source import SourceConfig
from ..base import BaseConnector

logger = logging.getLogger(__name__)

BINANCE_WS_BASE = "wss://stream.binance.com:9443/ws"
BINANCE_WS_COMBINED = "wss://stream.binance.com:9443/stream"

_KNOWN_QUOTES = ("USDT", "USDC", "BUSD", "EUR", "USD", "GBP", "JPY", "BTC", "ETH")


def _split_symbol_fallback(symbol: str) -> tuple[str, str]:
    if "/" in symbol:
        b, q = symbol.split("/", 1)
        return b, q
    if "-" in symbol:
        b, q = symbol.split("-", 1)
        return b, q
    upper = symbol.upper()
    for q in _KNOWN_QUOTES:
        if upper.endswith(q):
            return symbol[: -len(q)], q
    # Fallback: first half / second half
    mid = len(symbol) // 2
    return symbol[:mid], symbol[mid:]


class BinanceConnector(BaseConnector):
    """Binance public WebSocket market data connector.

    Streams supported:
    - Trade streams: <symbol>@trade
    - Ticker streams: <symbol>@ticker
    - Order book: <symbol>@depth<levels> (5, 10, 20)
    - Kline/candle: <symbol>@kline_<interval>
    - Aggregate trade: <symbol>@aggTrade
    - Mini ticker: <symbol>@miniTicker
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._ws: Any = None
        self._ws_url = config.ws_url or BINANCE_WS_COMBINED
        self._streams: list[str] = []
        self._listen_key: str | None = None

    def _build_stream_name(self, symbol: str, channel: str) -> str:
        """Convert canonical symbol to Binance stream name."""
        # Binance uses lowercase symbols without separator for spot
        binance_symbol = symbol.replace("/", "").replace("-", "").replace("_", "").lower()

        channel_map = {
            "trade": f"{binance_symbol}@trade",
            "ticker": f"{binance_symbol}@ticker",
            "book": f"{binance_symbol}@depth10",
            "candle": f"{binance_symbol}@kline_1m",
            "aggtrade": f"{binance_symbol}@aggTrade",
        }
        return channel_map.get(channel, f"{binance_symbol}@trade")

    async def _connect_impl(self) -> None:
        """Establish WebSocket connection to Binance."""
        # Build stream list from configured symbols and channels
        self._streams = []
        for symbol in self.config.symbols:
            for channel in self.config.channels:
                stream = self._build_stream_name(symbol, channel)
                self._streams.append(stream)

        if not self._streams:
            # Default to BTCUSDT trade if nothing configured
            self._streams = ["btcusdt@trade"]

        # Use combined stream endpoint for multiple streams.
        # Combined streams use the query form ``?streams=s1/s2`` (the path form
        # ``/stream/s1`` returns HTTP 404).
        stream_path = "/".join(self._streams)
        base = self._ws_url.rstrip("/")
        url = f"{base}?streams={stream_path}"

        logger.info("Connecting to Binance WebSocket: %s (%d streams)", url, len(self._streams))

        self._ws = await websockets.connect(
            url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=10,
            max_size=2**20,
        )

    async def _disconnect_impl(self) -> None:
        """Close WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _subscribe_impl(self) -> None:
        """Subscriptions are done via URL path for combined stream."""
        logger.info("Subscribed to Binance streams: %s", self._streams)

    async def _run(self) -> None:
        """Main message processing loop."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                if not self._running:
                    break

                receive_time = time.time()
                try:
                    data = json.loads(message)
                    await self._process_message(data, receive_time)
                except json.JSONDecodeError:
                    logger.warning("Failed to decode Binance message: %s", message[:200])
                except Exception as e:
                    logger.exception("Error processing Binance message: %s", e)
                    self._state.errors += 1

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Binance WebSocket closed: %s", e)
            if self._running:
                self._state.connection_state = ConnectionState.DISCONNECTED
                await self._schedule_reconnect()
        except Exception as e:
            logger.exception("Binance WebSocket error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        """Process a single WebSocket message."""
        # Combined stream wraps data in {"stream": "...", "data": {...}}
        stream_name = data.get("stream")
        payload = data.get("data", data)

        # Extract symbol from stream name
        symbol = ""
        if stream_name:
            symbol = stream_name.split("@")[0].upper()

        # Preserve raw envelope
        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="binance",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.CRYPTO,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=payload,
            raw_headers={},
            stream_id=stream_name,
        )
        await self._publish_raw(raw_env)

        # Normalize based on stream type
        if stream_name and "@trade" in stream_name:
            await self._normalize_trade(payload, symbol, receive_time, raw_env.envelope_id)
        elif stream_name and "@ticker" in stream_name:
            await self._normalize_ticker(payload, symbol, receive_time, raw_env.envelope_id)
        elif stream_name and "@depth" in stream_name:
            await self._normalize_book(payload, symbol, receive_time, raw_env.envelope_id)
        elif stream_name and "@kline" in stream_name:
            await self._normalize_kline(payload, symbol, receive_time, raw_env.envelope_id)
        elif stream_name and "@aggTrade" in stream_name:
            await self._normalize_agg_trade(payload, symbol, receive_time, raw_env.envelope_id)

    async def _normalize_trade(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        """Normalize trade event."""
        try:
            event_time = datetime.fromtimestamp(payload.get("T", 0) / 1000, tz=UTC)
            price = float(payload.get("p", 0))
            qty = float(payload.get("q", 0))
            side = "buy" if payload.get("m", True) is False else "sell"  # m = isBuyerMaker
            trade_id = payload.get("t", 0)

            instrument = (
                self.instrument_master.get_by_venue_symbol("binance", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"BINANCE_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            event = NormalizedEvent.create_trade(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="binance",
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                price=price,
                quantity=qty,
                side=side,
                trade_id=trade_id,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(event)
            self._state.messages_received += 1

        except Exception as e:
            logger.warning("Failed to normalize Binance trade: %s", e)

    async def _normalize_ticker(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        """Normalize ticker event."""
        try:
            event_time = datetime.fromtimestamp(payload.get("E", 0) / 1000, tz=UTC)
            bid = float(payload.get("b", 0)) if payload.get("b") else None
            ask = float(payload.get("a", 0)) if payload.get("a") else None
            if bid is None or ask is None:
                return

            instrument = (
                self.instrument_master.get_by_venue_symbol("binance", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"BINANCE_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            event = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="binance",
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                bid=bid,
                ask=ask,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(event)
            self._state.messages_received += 1

        except Exception as e:
            logger.warning("Failed to normalize Binance ticker: %s", e)

    async def _normalize_book(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        """Normalize order book event."""
        try:
            event_time = datetime.fromtimestamp(payload.get("E", 0) / 1000, tz=UTC)
            bids = [[float(p), float(q)] for p, q in payload.get("b", [])[:10]]
            asks = [[float(p), float(q)] for p, q in payload.get("a", [])[:10]]

            instrument = (
                self.instrument_master.get_by_venue_symbol("binance", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"BINANCE_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            event = NormalizedEvent(
                event_type="book",
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="binance",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                event_time=event_time,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                payload={
                    "bids": bids,
                    "asks": asks,
                    "last_update_id": payload.get("u"),
                },
                source_latency_ms=(receive_time - event_time.timestamp()) * 1000,
                raw_envelope_id=raw_id,
            )
            await self._publish_normalized(event)
            self._state.messages_received += 1

        except Exception as e:
            logger.warning("Failed to normalize Binance book: %s", e)

    async def _normalize_kline(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        """Normalize kline/candle event."""
        try:
            k = payload.get("k", {})
            if not k.get("x"):  # Not closed yet
                return

            event_time = datetime.fromtimestamp(k.get("t", 0) / 1000, tz=UTC)
            open_ = float(k.get("o", 0))
            high = float(k.get("h", 0))
            low = float(k.get("l", 0))
            close = float(k.get("c", 0))
            volume = float(k.get("v", 0))
            interval = k.get("i", "1m")

            instrument = (
                self.instrument_master.get_by_venue_symbol("binance", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"BINANCE_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            event = NormalizedEvent.create_candle(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="binance",
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                open_=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                interval=interval,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(event)
            self._state.messages_received += 1

        except Exception as e:
            logger.warning("Failed to normalize Binance kline: %s", e)

    async def _normalize_agg_trade(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        """Normalize aggregate trade event."""
        # Similar to trade but with aggregate info
        await self._normalize_trade(payload, symbol, receive_time, raw_id)
