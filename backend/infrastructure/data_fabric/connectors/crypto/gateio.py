# backend/infrastructure/data_fabric/connectors/crypto/gateio.py
"""Gate.io WebSocket connector — real-time crypto market data via v4 API.

Streams: trades, book_ticker (best bid/ask), order book depth, candlesticks.
No API key required for public market data.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import websockets

from .....domain.data_fabric.enums import AssetClass, ConnectionState, DataPlane
from .....domain.data_fabric.envelope import NormalizedEvent
from .....domain.data_fabric.instrument import InstrumentMaster
from .....domain.data_fabric.source import SourceConfig
from ..base import BaseConnector

logger = logging.getLogger(__name__)

GATEIO_WS_URL = "wss://api.gateio.ws/ws/v4/"


def _split_symbol_fallback(symbol: str) -> tuple[str, str]:
    upper = symbol.upper()
    for quote in ("USDT", "USDC", "BTC", "ETH"):
        if upper.endswith(quote):
            return symbol[: -len(quote)], quote
    mid = len(symbol) // 2
    return symbol[:mid], symbol[mid:]


def _to_gateio_pair(symbol: str) -> str:
    """Convert BTCUSDT to Gate.io format BTC_USDT."""
    upper = symbol.upper().replace("/", "_").replace("-", "_")
    if "_" in upper:
        return upper
    for quote in ("USDT", "USDC", "BTC", "ETH"):
        if upper.endswith(quote):
            return f"{upper[: -len(quote)]}_{quote}"
    return upper


class GateioConnector(BaseConnector):
    """Gate.io v4 public WebSocket market data connector."""

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._ws: Any = None
        self._ws_url = config.ws_url or GATEIO_WS_URL
        self._symbols: list[str] = []
        self._channels: list[str] = []
        self._sub_id = 0

    def _next_id(self) -> int:
        self._sub_id += 1
        return int(time.time()) + self._sub_id

    def _make_sub(self, channel: str, payload: list[str]) -> str:
        return json.dumps(
            {
                "time": self._next_id(),
                "channel": channel,
                "event": "subscribe",
                "payload": payload,
            }
        )

    async def _connect_impl(self) -> None:
        self._symbols = list(self.config.symbols) or ["BTC_USDT"]
        self._channels = list(self.config.channels) or ["trade", "book"]

        logger.info("Connecting to Gate.io WebSocket: %s", self._ws_url)
        self._ws = await websockets.connect(
            self._ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=10,
            max_size=2**20,
        )
        logger.info("Gate.io WebSocket connected")

    async def _disconnect_impl(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _subscribe_impl(self) -> None:
        for symbol in self._symbols:
            pair = _to_gateio_pair(symbol)
            for channel in self._channels:
                if channel == "trade":
                    await self._ws.send(self._make_sub("spot.trades", [pair]))
                elif channel == "ticker":
                    await self._ws.send(self._make_sub("spot.book_ticker", [pair]))
                elif channel == "book":
                    await self._ws.send(
                        self._make_sub(
                            "spot.book",
                            [pair],
                        )
                    )
                elif channel == "candle":
                    await self._ws.send(self._make_sub("spot.candlesticks", ["1m", pair]))
                logger.info("Subscribed to Gate.io: %s %s", channel, pair)

    async def _run(self) -> None:
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
                    logger.warning("Failed to decode Gate.io message")
                except Exception as e:
                    logger.exception("Error processing Gate.io message: %s", e)
                    self._state.errors += 1
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Gate.io WebSocket closed: %s", e)
            if self._running:
                self._state.connection_state = ConnectionState.DISCONNECTED
                await self._schedule_reconnect()
        except Exception as e:
            logger.exception("Gate.io WebSocket error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        channel = data.get("channel", "")
        event = data.get("event", "")
        result = data.get("result", {})

        if event in ("subscribe", "unsubscribe"):
            return

        if "trades" in channel:
            trades = result if isinstance(result, list) else [result]
            for t in trades:
                if isinstance(t, dict):
                    await self._normalize_trade(t, receive_time, raw_id=channel)
        elif "book_ticker" in channel:
            if isinstance(result, dict):
                await self._normalize_ticker(result, receive_time, raw_id=channel)
        elif channel == "spot.book":
            if isinstance(result, dict):
                await self._normalize_book(result, receive_time, raw_id=channel)
        elif "candlesticks" in channel:
            candles = result if isinstance(result, list) else [result]
            for c in candles:
                if isinstance(c, dict):
                    await self._normalize_kline(c, receive_time, raw_id=channel)

    def _parse_pair(self, currency_pair: str) -> tuple[str, str, str]:
        """Parse BTC_USDT -> (BTCUSDT, BTC, USDT)."""
        parts = currency_pair.split("_")
        if len(parts) == 2:
            base, quote = parts
            return f"{base}{quote}", base, quote
        base, quote = _split_symbol_fallback(currency_pair)
        return f"{base}{quote}", base, quote

    async def _normalize_trade(self, t: dict[str, Any], receive_time: float, raw_id: str) -> None:
        try:
            price = float(t.get("price", 0))
            amount = float(t.get("amount", 0))
            if price <= 0:
                return

            pair = t.get("currency_pair", "")
            symbol, base, quote = self._parse_pair(pair)
            side = t.get("side", "buy")
            trade_id = t.get("id", "")
            ts = float(t.get("create_time", receive_time))
            event_time = datetime.fromtimestamp(ts, tz=UTC)

            norm = NormalizedEvent.create_trade(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="gateio",
                instrument_id=f"GATEIO_{symbol}",
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                price=price,
                quantity=amount,
                side=side,
                trade_id=str(trade_id),
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.debug("Gate.io trade normalize failed: %s", e)

    async def _normalize_ticker(
        self, payload: dict[str, Any], receive_time: float, raw_id: str
    ) -> None:
        try:
            bid = float(payload.get("b", 0)) if payload.get("b") else None
            ask = float(payload.get("a", 0)) if payload.get("a") else None
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                return

            pair = payload.get("currency_pair", "")
            symbol, base, quote = self._parse_pair(pair)
            event_time = datetime.fromtimestamp(receive_time, tz=UTC)

            norm = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="gateio",
                instrument_id=f"GATEIO_{symbol}",
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                bid=bid,
                ask=ask,
                received_at=event_time,
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.debug("Gate.io ticker normalize failed: %s", e)

    async def _normalize_book(
        self, payload: dict[str, Any], receive_time: float, raw_id: str
    ) -> None:
        try:
            bids_raw = payload.get("bids", [])
            asks_raw = payload.get("asks", [])
            bids = [[float(b[0]), float(b[1])] for b in bids_raw[:10] if len(b) >= 2]
            asks = [[float(a[0]), float(a[1])] for a in asks_raw[:10] if len(a) >= 2]
            if not bids and not asks:
                return

            pair = payload.get("currency_pair", "") or payload.get("s", "")
            symbol, base, quote = self._parse_pair(pair)
            event_time = datetime.fromtimestamp(receive_time, tz=UTC)

            norm = NormalizedEvent(
                event_type="book",
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="gateio",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                event_time=event_time,
                received_at=event_time,
                instrument_id=f"GATEIO_{symbol}",
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                payload={"bids": bids, "asks": asks},
                source_latency_ms=0.0,
                raw_envelope_id=raw_id,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.debug("Gate.io book normalize failed: %s", e)

    async def _normalize_kline(self, c: dict[str, Any], receive_time: float, raw_id: str) -> None:
        try:
            close = float(c.get("c", 0))
            if close <= 0:
                return
            pair = c.get("n", c.get("currency_pair", ""))
            symbol, base, quote = self._parse_pair(pair)
            ts = float(c.get("t", receive_time))
            event_time = datetime.fromtimestamp(ts, tz=UTC)

            norm = NormalizedEvent.create_candle(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="gateio",
                instrument_id=f"GATEIO_{symbol}",
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                open_=float(c.get("o", 0)),
                high=float(c.get("h", 0)),
                low=float(c.get("l", 0)),
                close=close,
                volume=float(c.get("v", 0)),
                interval=c.get("interval", "1m"),
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.debug("Gate.io kline normalize failed: %s", e)
