# backend/infrastructure/data_fabric/connectors/crypto/mexc.py
"""MEXC WebSocket connector — full market data via wbs.mexc.com.

Streams: trades, tickers, order book depth, klines.
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
from .....domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from .....domain.data_fabric.instrument import InstrumentMaster
from .....domain.data_fabric.source import SourceConfig
from ..base import BaseConnector

logger = logging.getLogger(__name__)

MEXC_WS_URL = "wss://wbs.mexc.com/ws"

# MEXC uses different interval naming
_INTERVAL_MAP = {"1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "60m", "1d": "1d"}


def _split_symbol_fallback(symbol: str) -> tuple[str, str]:
    """Split a MEXC symbol like BTCUSDT into (base, quote)."""
    upper = symbol.upper()
    for quote in ("USDT", "USDC", "BTC", "ETH"):
        if upper.endswith(quote):
            return symbol[: -len(quote)], quote
    mid = len(symbol) // 2
    return symbol[:mid], symbol[mid:]


def _to_mexc_symbol(symbol: str) -> str:
    """Convert BTCUSDT or BTC_USDT to MEXC format (already spot format)."""
    return symbol.upper().replace("/", "_").replace("-", "_")


class MexcConnector(BaseConnector):
    """MEXC public WebSocket market data connector."""

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._ws: Any = None
        self._ws_url = config.ws_url or MEXC_WS_URL
        self._symbols: list[str] = []
        self._channels: list[str] = []

    def _build_channel(self, channel: str, symbol: str) -> str:
        """Build MEXC subscription channel string."""
        sym = _to_mexc_symbol(symbol)
        channel_map = {
            "trade": f"spot@public.deals.v3.api@{sym}",
            "ticker": f"spot@public.bookTicker.v3.api@{sym}",
            "book": f"spot@public.limit.depth.v3.api@{sym}@20",
            "candle": f"spot@public.kline.v3.api@{sym}@Min1",
        }
        return channel_map.get(channel, f"spot@public.deals.v3.api@{sym}")

    async def _connect_impl(self) -> None:
        self._symbols = list(self.config.symbols) or ["BTCUSDT"]
        self._channels = list(self.config.channels) or ["trade", "book"]

        logger.info("Connecting to MEXC WebSocket: %s", self._ws_url)
        self._ws = await websockets.connect(
            self._ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=10,
            max_size=2**20,
        )
        logger.info("MEXC WebSocket connected")

    async def _disconnect_impl(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _subscribe_impl(self) -> None:
        for symbol in self._symbols:
            for channel in self._channels:
                channel_name = self._build_channel(channel, symbol)
                sub_msg = {"method": "SUBSCRIPTION", "params": [channel_name]}
                await self._ws.send(json.dumps(sub_msg))
                logger.info("Subscribed to MEXC: %s", channel_name)

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
                    logger.warning("Failed to decode MEXC message")
                except Exception as e:
                    logger.exception("Error processing MEXC message: %s", e)
                    self._state.errors += 1
        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("MEXC WebSocket closed: %s", e)
            if self._running:
                self._state.connection_state = ConnectionState.DISCONNECTED
                await self._schedule_reconnect()
        except Exception as e:
            logger.exception("MEXC WebSocket error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        # Pong response
        if data.get("msg") == "PONG":
            return

        channel = data.get("channel", "")
        symbol_raw = data.get("symbol", "")

        # Extract symbol from channel if not in body
        if not symbol_raw and "@" in channel:
            parts = channel.split("@")
            if len(parts) >= 3:
                symbol_raw = parts[-1].replace("_", "")

        if not symbol_raw:
            return

        symbol = symbol_raw.upper()
        payload = data.get("data", data)

        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="mexc",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.CRYPTO,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=payload,
            raw_headers={},
            stream_id=channel,
        )
        await self._publish_raw(raw_env)

        if "deals" in channel or "trade" in channel:
            deals = payload if isinstance(payload, list) else [payload]
            for deal in deals:
                await self._normalize_trade(deal, symbol, receive_time, raw_env.envelope_id)
        elif "bookTicker" in channel or "ticker" in channel:
            await self._normalize_ticker(payload, symbol, receive_time, raw_env.envelope_id)
        elif "limit.depth" in channel or "depth" in channel:
            await self._normalize_book(payload, symbol, receive_time, raw_env.envelope_id)
        elif "kline" in channel:
            await self._normalize_kline(payload, symbol, receive_time, raw_env.envelope_id)

    def _get_base_quote(self, symbol: str) -> tuple[str, str]:
        instrument = (
            self.instrument_master.get_by_venue_symbol("mexc", symbol)
            if self.instrument_master
            else None
        )
        if instrument:
            return instrument.base_asset, instrument.quote_asset
        return _split_symbol_fallback(symbol)

    async def _normalize_trade(
        self, deal: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            price = float(deal.get("p", 0))
            qty = float(deal.get("v", 0))
            if price <= 0 or qty <= 0:
                return

            side = "buy" if deal.get("T", 1) == 1 else "sell"
            trade_id = deal.get("t", 0)
            ts_ms = deal.get("S", int(receive_time * 1000))
            event_time = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)

            base, quote = self._get_base_quote(symbol)
            norm = NormalizedEvent.create_trade(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="mexc",
                instrument_id=f"MEXC_{symbol}",
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
            logger.debug("MEXC trade normalize failed: %s", e)

    async def _normalize_ticker(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            bid = float(payload.get("bid", 0)) if payload.get("bid") else None
            ask = float(payload.get("ask", 0)) if payload.get("ask") else None
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                return

            base, quote = self._get_base_quote(symbol)
            norm = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="mexc",
                instrument_id=f"MEXC_{symbol}",
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=datetime.fromtimestamp(receive_time, tz=UTC),
                bid=bid,
                ask=ask,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.debug("MEXC ticker normalize failed: %s", e)

    async def _normalize_book(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            bids_raw = payload.get("bids", [])
            asks_raw = payload.get("asks", [])

            bids = []
            for b in bids_raw[:10]:
                if isinstance(b, (list, tuple)) and len(b) >= 2:
                    bids.append([float(b[0]), float(b[1])])
                elif isinstance(b, dict):
                    bids.append([float(b.get("price", 0)), float(b.get("quantity", 0))])

            asks = []
            for a in asks_raw[:10]:
                if isinstance(a, (list, tuple)) and len(a) >= 2:
                    asks.append([float(a[0]), float(a[1])])
                elif isinstance(a, dict):
                    asks.append([float(a.get("price", 0)), float(a.get("quantity", 0))])

            if not bids and not asks:
                return

            base, quote = self._get_base_quote(symbol)
            event_time = datetime.fromtimestamp(receive_time, tz=UTC)
            norm = NormalizedEvent(
                event_type="book",
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="mexc",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                event_time=event_time,
                received_at=event_time,
                instrument_id=f"MEXC_{symbol}",
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
            logger.debug("MEXC book normalize failed: %s", e)

    async def _normalize_kline(
        self, payload: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            k = payload if isinstance(payload, dict) else {}
            if not k.get("o"):
                return

            base, quote = self._get_base_quote(symbol)
            event_time = datetime.fromtimestamp(int(k.get("t", receive_time * 1000)) / 1000, tz=UTC)
            norm = NormalizedEvent.create_candle(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="mexc",
                instrument_id=f"MEXC_{symbol}",
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                open_=float(k.get("o", 0)),
                high=float(k.get("h", 0)),
                low=float(k.get("l", 0)),
                close=float(k.get("c", 0)),
                volume=float(k.get("v", 0)),
                interval=k.get("i", "1m"),
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.CRYPTO,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.debug("MEXC kline normalize failed: %s", e)


# ---------------------------------------------------------------------------
# REST API helpers (no WebSocket needed — public endpoints, no API key)
# ---------------------------------------------------------------------------

MEXC_REST_BASE = "https://api.mexc.com/api/v3"


async def fetch_klines(symbol: str, interval: str = "1h", limit: int = 200) -> list[dict[str, Any]]:
    """Fetch OHLCV candlesticks from MEXC REST API.

    Returns list of {time, open, high, low, close, volume} dicts.
    Uses httpx async client (already a dependency).
    """
    import httpx

    url = f"{MEXC_REST_BASE}/klines"
    params: dict[str, str | int] = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        raw = resp.json()

    candles = []
    for k in raw:
        candles.append(
            {
                "time": int(k[0]),
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            }
        )
    return candles


async def fetch_order_book(symbol: str, limit: int = 20) -> dict[str, Any]:
    """Fetch order book depth from MEXC REST API."""
    import httpx

    url = f"{MEXC_REST_BASE}/depth"
    params: dict[str, str | int] = {"symbol": symbol.upper(), "limit": limit}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result


async def fetch_ticker(symbol: str) -> dict[str, Any]:
    """Fetch current price from MEXC REST API."""
    import httpx

    url = f"{MEXC_REST_BASE}/ticker/price"
    params: dict[str, str] = {"symbol": symbol.upper()}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result


async def fetch_all_usdt_pairs() -> list[str]:
    """Fetch all USDT trading pairs from MEXC exchange info."""
    import httpx

    url = f"{MEXC_REST_BASE}/exchangeInfo"
    async with httpx.AsyncClient(timeout=20) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        info = resp.json()
    symbols = info.get("symbols", [])
    return [
        s["symbol"]
        for s in symbols
        if s.get("quoteAsset") == "USDT"
        and s.get("isSpotTradingAllowed", False)
        and s.get("status") in ("1", "ENABLED")
    ]


async def fetch_recent_trades(symbol: str, limit: int = 50) -> list[dict[str, Any]]:
    """Fetch recent trades from MEXC REST API."""
    import httpx

    url = f"{MEXC_REST_BASE}/trades"
    params: dict[str, str | int] = {"symbol": symbol.upper(), "limit": limit}
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params)
        resp.raise_for_status()
        result: list[dict[str, Any]] = resp.json()
        return result
