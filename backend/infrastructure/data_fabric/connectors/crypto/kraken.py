"""Kraken WebSocket v2 connector - public market data.

Kraken provides public WebSocket v2 for trades, tickers, order books,
and OHLC. No authentication required for market data.
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

KRAKEN_WS_URL = "wss://ws.kraken.com/v2"

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


class KrakenConnector(BaseConnector):
    """Kraken WebSocket v2 market data connector.

    Channels:
    - trade
    - ticker
    - book
    - ohlc
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._ws: Any = None
        self._ws_url = config.ws_url or KRAKEN_WS_URL
        self._symbols: list[str] = []
        self._channels: list[str] = []

    def _to_kraken_symbol(self, symbol: str) -> str:
        """Convert to Kraken format (e.g., BTC/USD -> BTC/USD)."""
        if "/" in symbol:
            return symbol.upper()
        if "-" in symbol:
            return symbol.replace("-", "/").upper()
        if symbol.endswith("USDT"):
            return symbol[:-4] + "/USDT"
        if symbol.endswith("USD"):
            return symbol[:-3] + "/USD"
        return symbol.upper()

    def _from_kraken_symbol(self, wsname: str) -> str:
        return wsname

    async def _connect_impl(self) -> None:
        self._symbols = [self._to_kraken_symbol(s) for s in self.config.symbols]
        if not self._symbols:
            self._symbols = ["BTC/USD"]

        self._channels = list(self.config.channels) or ["trade", "ticker", "book"]

        logger.info("Connecting to Kraken WebSocket v2: %s", self._ws_url)
        self._ws = await websockets.connect(
            self._ws_url,
            ping_interval=30,
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
                "method": "subscribe",
                "params": {
                    "channel": channel,
                    "symbol": self._symbols,
                },
            }
            await self._ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to Kraken: %s x %s", self._symbols, self._channels)

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
                    logger.warning("Failed to decode Kraken message")
                except Exception as e:
                    logger.exception("Error processing Kraken message: %s", e)
                    self._state.errors += 1

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Kraken WebSocket closed: %s", e)
            if self._running:
                self._state.connection_state = ConnectionState.DISCONNECTED
                await self._schedule_reconnect()
        except Exception as e:
            logger.exception("Kraken WebSocket error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        # Handle subscription confirmations
        if (
            data.get("method") == "subscribe"
            and data.get("result", {}).get("status") == "subscribed"
        ):
            logger.info("Kraken subscribed: %s", data.get("params", {}).get("channel"))
            return

        channel = data.get("channel")
        payload = data.get("data", [data])

        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="kraken",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.CRYPTO,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=data,
            raw_headers={},
            stream_id=channel,
        )
        await self._publish_raw(raw_env)

        for item in payload if isinstance(payload, list) else [payload]:
            symbol = item.get("symbol") or item.get("wsname")
            if not symbol:
                continue

            if channel == "trade":
                await self._normalize_trade(item, symbol, receive_time, raw_env.envelope_id)
            elif channel == "ticker":
                await self._normalize_ticker(item, symbol, receive_time, raw_env.envelope_id)
            elif channel == "book":
                await self._normalize_book(item, symbol, receive_time, raw_env.envelope_id)

    async def _normalize_trade(
        self, item: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            event_time = datetime.fromisoformat(item.get("timestamp", "").replace("Z", "+00:00"))
            price = float(item.get("price", 0))
            qty = float(item.get("qty", 0))
            side = item.get("side", "buy")
            trade_id = item.get("trade_id", 0)

            instrument = (
                self.instrument_master.get_by_venue_symbol("kraken", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"KRAKEN_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            norm = NormalizedEvent.create_trade(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="kraken",
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
            logger.warning("Failed to normalize Kraken trade: %s", e)

    async def _normalize_ticker(
        self, item: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            event_time = datetime.fromisoformat(item.get("timestamp", "").replace("Z", "+00:00"))
            bid = float(item.get("bid", 0)) if item.get("bid") else None
            ask = float(item.get("ask", 0)) if item.get("ask") else None
            if bid is None or ask is None:
                return

            instrument = (
                self.instrument_master.get_by_venue_symbol("kraken", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"KRAKEN_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            norm = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="kraken",
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
            logger.warning("Failed to normalize Kraken ticker: %s", e)

    @staticmethod
    def _parse_level(level: Any) -> list[float]:
        """Parse one book level: Kraken v2 dicts or legacy [price, qty] arrays."""
        if isinstance(level, dict):
            qty = level.get("qty", level.get("volume", 0.0)) or 0.0
            return [float(level.get("price", 0.0)), float(qty)]
        return [float(level[0]), float(level[1])]

    async def _normalize_book(
        self, item: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            raw_ts = item.get("timestamp")
            if raw_ts:
                event_time = datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00"))
            else:
                event_time = datetime.fromtimestamp(receive_time, tz=UTC)
            bids = [self._parse_level(b) for b in item.get("bids", [])[:10]]
            asks = [self._parse_level(a) for a in item.get("asks", [])[:10]]
            if not bids and not asks:
                return

            instrument = (
                self.instrument_master.get_by_venue_symbol("kraken", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"KRAKEN_{symbol}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                base, quote = _split_symbol_fallback(symbol)

            norm = NormalizedEvent(
                event_type="book",
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="kraken",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                event_time=event_time,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                payload={"bids": bids, "asks": asks},
                source_latency_ms=0,
                raw_envelope_id=raw_id,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.warning("Failed to normalize Kraken book: %s", e)
