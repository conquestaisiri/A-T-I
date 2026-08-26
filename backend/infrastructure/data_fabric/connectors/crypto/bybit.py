"""Bybit WebSocket v5 connector - public market data.

Bybit provides public WebSocket v5 for trades, tickers, order books,
and klines. No authentication required for market data.
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

BYBIT_WS_SPOT = "wss://stream.bybit.com/v5/public/spot"
BYBIT_WS_LINEAR = "wss://stream.bybit.com/v5/public/linear"
BYBIT_WS_INVERSE = "wss://stream.bybit.com/v5/public/inverse"
BYBIT_WS_OPTION = "wss://stream.bybit.com/v5/public/option"

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
    mid = len(symbol) // 2
    return symbol[:mid], symbol[mid:]


class BybitConnector(BaseConnector):
    """Bybit WebSocket v5 market data connector.

    Channels:
    - publicTrade
    - tickers
    - orderbook.1 / orderbook.50 / orderbook.200
    - kline.1 / kline.5 / kline.15 / kline.60 / kline.D / kline.W
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._ws: Any = None
        self._ws_url = config.ws_url or BYBIT_WS_SPOT
        self._symbols: list[str] = []
        self._channels: list[str] = []

    def _to_bybit_symbol(self, symbol: str) -> str:
        """Convert to Bybit format (e.g., BTC/USD -> BTCUSDT for linear)."""
        if "/" in symbol:
            return symbol.replace("/", "")
        if "-" in symbol:
            return symbol.replace("-", "")
        return symbol.upper()

    def _from_bybit_symbol(self, symbol: str) -> str:
        return symbol

    def _get_ws_url(self) -> str:
        market_type = self.config.metadata.get("market_type", "spot")
        url_map = {
            "linear": BYBIT_WS_LINEAR,
            "inverse": BYBIT_WS_INVERSE,
            "option": BYBIT_WS_OPTION,
        }
        return url_map.get(market_type, BYBIT_WS_SPOT)

    async def _connect_impl(self) -> None:
        self._symbols = [self._to_bybit_symbol(s) for s in self.config.symbols]
        if not self._symbols:
            self._symbols = ["BTCUSDT"]

        self._channels = list(self.config.channels) or ["publicTrade", "tickers", "orderbook.1"]

        self._ws_url = self._get_ws_url()
        logger.info("Connecting to Bybit WebSocket v5: %s", self._ws_url)
        self._ws = await websockets.connect(
            self._ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=10,
        )

    async def _disconnect_impl(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _subscribe_impl(self) -> None:
        for channel in self._channels:
            subscribe_msg = {
                "op": "subscribe",
                "args": [f"{channel}.{symbol}" for symbol in self._symbols],
            }
            await self._ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to Bybit: %s x %s", self._symbols, self._channels)

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
                    logger.warning("Failed to decode Bybit message")
                except Exception as e:
                    logger.exception("Error processing Bybit message: %s", e)
                    self._state.errors += 1

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Bybit WebSocket closed: %s", e)
            if self._running:
                self._state.connection_state = ConnectionState.DISCONNECTED
                await self._schedule_reconnect()
        except Exception as e:
            logger.exception("Bybit WebSocket error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        # Handle subscription confirmations
        if data.get("op") == "subscribe" and data.get("success"):
            logger.info("Bybit subscribed: %s", data.get("args"))
            return

        topic = data.get("topic", "")
        payload = data.get("data", data)

        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="bybit",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.CRYPTO,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=data,
            raw_headers={},
            stream_id=topic,
        )
        await self._publish_raw(raw_env)

        if topic.startswith("publicTrade"):
            await self._normalize_trade(payload, receive_time, raw_id=raw_env.envelope_id)
        elif topic.startswith("tickers"):
            await self._normalize_ticker(payload, receive_time, raw_id=raw_env.envelope_id)
        elif topic.startswith("orderbook"):
            await self._normalize_book(payload, receive_time, raw_id=raw_env.envelope_id)

    async def _normalize_trade(
        self, payload: dict[str, Any], receive_time: float, raw_id: str
    ) -> None:
        trades = payload if isinstance(payload, list) else [payload]
        for trade in trades:
            try:
                symbol = trade.get("s", "")
                event_time = datetime.fromtimestamp(trade.get("T", 0) / 1000, tz=UTC)
                price = float(trade.get("p", 0))
                qty = float(trade.get("v", 0))
                side = trade.get("S", "buy").lower()
                trade_id = trade.get("i", 0)

                instrument = (
                    self.instrument_master.get_by_venue_symbol("bybit", symbol)
                    if self.instrument_master
                    else None
                )
                instrument_id = instrument.instrument_id if instrument else f"BYBIT_{symbol}"
                if instrument:
                    base = instrument.base_asset
                    quote = instrument.quote_asset
                else:
                    base, quote = _split_symbol_fallback(symbol)

                norm = NormalizedEvent.create_trade(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="bybit",
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
                await self._publish_normalized(norm)
                self._state.messages_received += 1
            except Exception as e:
                logger.warning("Failed to normalize Bybit trade: %s", e)

    async def _normalize_ticker(
        self, payload: dict[str, Any], receive_time: float, raw_id: str
    ) -> None:
        tickers = payload if isinstance(payload, list) else [payload]
        for ticker in tickers:
            try:
                symbol = ticker.get("symbol", "")
                event_time = datetime.fromtimestamp(ticker.get("timestamp", 0) / 1000, tz=UTC)
                bid = float(ticker.get("bid1Price", 0)) if ticker.get("bid1Price") else None
                ask = float(ticker.get("ask1Price", 0)) if ticker.get("ask1Price") else None
                if bid is None or ask is None:
                    continue

                instrument = (
                    self.instrument_master.get_by_venue_symbol("bybit", symbol)
                    if self.instrument_master
                    else None
                )
                instrument_id = instrument.instrument_id if instrument else f"BYBIT_{symbol}"
                if instrument:
                    base = instrument.base_asset
                    quote = instrument.quote_asset
                else:
                    base, quote = _split_symbol_fallback(symbol)

                norm = NormalizedEvent.create_quote(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="bybit",
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
                await self._publish_normalized(norm)
                self._state.messages_received += 1
            except Exception as e:
                logger.warning("Failed to normalize Bybit ticker: %s", e)

    async def _normalize_book(
        self, payload: dict[str, Any], receive_time: float, raw_id: str
    ) -> None:
        try:
            symbol = payload.get("s", "")
            event_time = datetime.fromtimestamp(payload.get("cts", 0) / 1000, tz=UTC)
            bids = [[float(b[0]), float(b[1])] for b in payload.get("b", [])[:10]]
            asks = [[float(a[0]), float(a[1])] for a in payload.get("a", [])[:10]]

            instrument = (
                self.instrument_master.get_by_venue_symbol("bybit", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"BYBIT_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            norm = NormalizedEvent(
                event_type="book",
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="bybit",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                event_time=event_time,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                payload={"bids": bids, "asks": asks, "seq": payload.get("seq")},
                source_latency_ms=0,
                raw_envelope_id=raw_id,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.warning("Failed to normalize Bybit book: %s", e)
