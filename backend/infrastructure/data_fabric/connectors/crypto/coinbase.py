"""Coinbase Advanced Trade WebSocket connector.

Coinbase provides public WebSocket market data for trades, tickers,
order books, and candles. No authentication required for market data.
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

COINBASE_WS_URL = "wss://advanced-trade-ws.coinbase.com"

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


class CoinbaseConnector(BaseConnector):
    """Coinbase Advanced Trade WebSocket market data connector.

    Channels:
    - trades
    - ticker
    - level2 (order book)
    - candles
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._ws: Any = None
        self._ws_url = config.ws_url or COINBASE_WS_URL
        self._product_ids: list[str] = []
        self._channels: list[str] = []

    def _to_coinbase_symbol(self, symbol: str) -> str:
        """Convert canonical symbol to Coinbase product ID (e.g., BTC-USD)."""
        if "-" in symbol:
            return symbol.upper()
        if "/" in symbol:
            return symbol.replace("/", "-").upper()
        # Assume BTCUSDT format
        if symbol.endswith("USDT"):
            return symbol[:-4] + "-USD"
        if symbol.endswith("USD"):
            return symbol[:-3] + "-USD"
        return symbol

    def _from_coinbase_symbol(self, product_id: str) -> str:
        """Convert Coinbase product ID to canonical symbol."""
        return product_id

    async def _connect_impl(self) -> None:
        self._product_ids = [self._to_coinbase_symbol(s) for s in self.config.symbols]
        if not self._product_ids:
            self._product_ids = ["BTC-USD"]

        self._channels = list(self.config.channels) or ["trades", "ticker", "level2"]

        logger.info("Connecting to Coinbase WebSocket: %s", self._ws_url)
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
        subscribe_msg = {
            "type": "subscribe",
            "product_ids": self._product_ids,
            "channels": self._channels,
        }
        await self._ws.send(json.dumps(subscribe_msg))
        logger.info("Subscribed to Coinbase: %s x %s", self._product_ids, self._channels)

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
                    logger.warning("Failed to decode Coinbase message")
                except Exception as e:
                    logger.exception("Error processing Coinbase message: %s", e)
                    self._state.errors += 1

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Coinbase WebSocket closed: %s", e)
            if self._running:
                self._state.connection_state = ConnectionState.DISCONNECTED
                await self._schedule_reconnect()
        except Exception as e:
            logger.exception("Coinbase WebSocket error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        channel = data.get("channel")
        events = data.get("events", [data])

        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="coinbase",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.CRYPTO,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=data,
            raw_headers={},
            stream_id=channel,
        )
        await self._publish_raw(raw_env)

        for event in events:
            product_id = event.get("product_id") or data.get("product_id")
            if not product_id:
                continue

            if channel == "trades":
                await self._normalize_trades(event, product_id, receive_time, raw_env.envelope_id)
            elif channel == "ticker" or channel == "ticker_batch":
                await self._normalize_ticker(event, product_id, receive_time, raw_env.envelope_id)
            elif channel == "level2":
                await self._normalize_book(event, product_id, receive_time, raw_env.envelope_id)

    async def _normalize_trades(
        self, event: dict[str, Any], product_id: str, receive_time: float, raw_id: str
    ) -> None:
        trades = event.get("trades", [event])
        for trade in trades:
            try:
                event_time = datetime.fromisoformat(trade.get("time", "").replace("Z", "+00:00"))
                price = float(trade.get("price", 0))
                qty = float(trade.get("size", 0))
                side = trade.get("side", "buy")
                trade_id = trade.get("trade_id", 0)

                instrument = (
                    self.instrument_master.get_by_venue_symbol("coinbase", product_id)
                    if self.instrument_master
                    else None
                )
                instrument_id = instrument.instrument_id if instrument else f"COINBASE_{product_id}"
                if instrument:
                    base = instrument.base_asset
                    quote = instrument.quote_asset
                else:
                    if "-" in product_id:
                        base, quote = product_id.split("-", 1)
                    elif "/" in product_id:
                        base, quote = product_id.split("/", 1)
                    else:
                        base, quote = _split_symbol_fallback(product_id)

                norm = NormalizedEvent.create_trade(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="coinbase",
                    instrument_id=instrument_id,
                    symbol=product_id,
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
                logger.warning("Failed to normalize Coinbase trade: %s", e)

    async def _normalize_ticker(
        self, event: dict[str, Any], product_id: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            event_time = datetime.fromisoformat(event.get("time", "").replace("Z", "+00:00"))
            bid = float(event.get("best_bid", 0)) if event.get("best_bid") else None
            ask = float(event.get("best_ask", 0)) if event.get("best_ask") else None
            if bid is None or ask is None:
                return

            instrument = (
                self.instrument_master.get_by_venue_symbol("coinbase", product_id)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"COINBASE_{product_id}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                if "-" in product_id:
                    base, quote = product_id.split("-", 1)
                elif "/" in product_id:
                    base, quote = product_id.split("/", 1)
                else:
                    base, quote = _split_symbol_fallback(product_id)

            norm = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="coinbase",
                instrument_id=instrument_id,
                symbol=product_id,
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
            logger.warning("Failed to normalize Coinbase ticker: %s", e)

    async def _normalize_book(
        self, event: dict[str, Any], product_id: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            event_time = datetime.fromisoformat(event.get("time", "").replace("Z", "+00:00"))
            updates = event.get("updates", [])
            bids = [
                [float(u["price_level"]), float(u["new_quantity"])]
                for u in updates
                if u["side"] == "bid"
            ][:10]
            asks = [
                [float(u["price_level"]), float(u["new_quantity"])]
                for u in updates
                if u["side"] == "ask"
            ][:10]

            instrument = (
                self.instrument_master.get_by_venue_symbol("coinbase", product_id)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"COINBASE_{product_id}"
            if instrument:
                base = instrument.base_asset
                quote = instrument.quote_asset
            else:
                if "-" in product_id:
                    base, quote = product_id.split("-", 1)
                elif "/" in product_id:
                    base, quote = product_id.split("/", 1)
                else:
                    base, quote = _split_symbol_fallback(product_id)

            norm = NormalizedEvent(
                event_type="book",
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="coinbase",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                event_time=event_time,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                instrument_id=instrument_id,
                symbol=product_id,
                base_asset=base,
                quote_asset=quote,
                payload={"bids": bids, "asks": asks},
                source_latency_ms=0,
                raw_envelope_id=raw_id,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.warning("Failed to normalize Coinbase book: %s", e)
