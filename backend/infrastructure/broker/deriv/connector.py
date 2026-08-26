"""Deriv WebSocket connector - public market data streams.

Deriv (formerly Binary.com) provides WebSocket streaming for forex,
crypto, indices, and synthetic indices. Supports Nigeria explicitly.
Demo accounts available via API.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

import websockets

from ....domain.data_fabric.enums import AssetClass, ConnectionState, DataPlane
from ....domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from ....domain.data_fabric.instrument import InstrumentMaster
from ....domain.data_fabric.source import SourceConfig
from ....infrastructure.data_fabric.connectors.base import BaseConnector

logger = logging.getLogger(__name__)

DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"  # Public app_id


class DerivConnector(BaseConnector):
    """Deriv WebSocket market data connector.

    Channels:
    - ticks: real-time tick data
    - candles: OHLC candles
    - active_symbols: available symbols
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._ws: Any = None
        self._ws_url = config.ws_url or DERIV_WS_URL
        self._symbols: list[str] = []
        self._channels: list[str] = []
        self._api_token: str = ""
        self._req_id = 0

    async def _connect_impl(self) -> None:
        # Get API token from environment if provided
        auth = self.config.get_all_auth()
        self._api_token = auth.get("api_token") or ""

        self._symbols = list(self.config.symbols) or [
            "R_10",
            "R_25",
            "R_50",
            "R_75",
            "R_100",  # Volatility indices
            "frxEURUSD",
            "frxGBPUSD",
            "frxUSDJPY",
            "frxAUDUSD",  # Forex
            "cryBTCUSD",
            "cryETHUSD",  # Crypto
        ]

        self._channels = list(self.config.channels) or ["ticks"]

        logger.info(
            "Connecting to Deriv WebSocket: %s (%d symbols)", self._ws_url, len(self._symbols)
        )

        self._ws = await websockets.connect(
            self._ws_url,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=10,
            max_size=2**20,
        )

        # Authorize if token provided
        if self._api_token:
            await self._send({"authorize": self._api_token})

    async def _disconnect_impl(self) -> None:
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _subscribe_impl(self) -> None:
        for symbol in self._symbols:
            for channel in self._channels:
                await self._subscribe_symbol(symbol, channel)

        logger.info("Subscribed to Deriv: %s x %s", self._symbols, self._channels)

    async def _subscribe_symbol(self, symbol: str, channel: str) -> None:
        self._req_id += 1
        req: dict[str, Any] = {
            "req_id": self._req_id,
            "subscribe": 1,
        }
        if channel == "ticks":
            req["ticks"] = symbol
        elif channel == "candles":
            # Deriv API: candles come from ticks_history; the granularity
            # parameter is seconds (60 = 1m). "interval" is rejected.
            req["ticks_history"] = symbol
            req["style"] = "candles"
            req["granularity"] = 60
            req["end"] = "latest"
        await self._send(req)

    async def _send(self, msg: dict[str, Any]) -> None:
        if self._ws:
            await self._ws.send(json.dumps(msg))

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
                    logger.warning("Failed to decode Deriv message")
                except Exception as e:
                    logger.exception("Error processing Deriv message: %s", e)
                    self._state.errors += 1

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning("Deriv WebSocket closed: %s", e)
            if self._running:
                self._state.connection_state = ConnectionState.DISCONNECTED
                await self._schedule_reconnect()
        except Exception as e:
            logger.exception("Deriv WebSocket error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        msg_type = data.get("msg_type")

        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="deriv",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.FOREX,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=data,
            raw_headers={},
            stream_id=msg_type,
        )
        await self._publish_raw(raw_env)

        if msg_type == "tick":
            await self._normalize_tick(data, receive_time, raw_env.envelope_id)
        elif msg_type == "candles":
            await self._normalize_candles(data, receive_time, raw_env.envelope_id)
        elif msg_type == "authorize":
            logger.info("Deriv authorized: %s", data.get("authorize", {}).get("loginid"))
        elif msg_type == "subscription":
            logger.info("Deriv subscription confirmed: %s", data.get("subscription", {}))
        elif "error" in data:
            logger.warning("Deriv error: %s", data["error"])

    async def _normalize_tick(self, data: dict[str, Any], receive_time: float, raw_id: str) -> None:
        """Normalize Deriv tick to NormalizedEvent."""
        try:
            tick = data.get("tick", {})
            symbol = tick.get("symbol", "")
            event_time = datetime.fromtimestamp(tick.get("epoch", 0), tz=UTC)
            price = float(tick.get("quote", 0))

            instrument = (
                self.instrument_master.get_by_venue_symbol("deriv", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"DERIV_{symbol}"
            base = (
                instrument.base_asset if instrument else symbol[:3] if len(symbol) >= 3 else symbol
            )
            quote = (
                instrument.quote_asset if instrument else symbol[3:] if len(symbol) >= 6 else "USD"
            )

            norm = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="deriv",
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                bid=price,
                ask=price,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.FOREX,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1

        except Exception as e:
            logger.warning("Failed to normalize Deriv tick: %s", e)

    async def _normalize_candles(
        self, data: dict[str, Any], receive_time: float, raw_id: str
    ) -> None:
        """Normalize Deriv candles to NormalizedEvent."""
        try:
            candles = data.get("candles", [])
            for candle in candles:
                symbol = data.get("echo_req", {}).get("ticks_history", "")
                event_time = datetime.fromtimestamp(candle.get("epoch", 0), tz=UTC)
                open_ = float(candle.get("open", 0))
                high = float(candle.get("high", 0))
                low = float(candle.get("low", 0))
                close = float(candle.get("close", 0))
                volume = float(candle.get("volume", 0))

                instrument = (
                    self.instrument_master.get_by_venue_symbol("deriv", symbol)
                    if self.instrument_master
                    else None
                )
                instrument_id = instrument.instrument_id if instrument else f"DERIV_{symbol}"
                base = (
                    instrument.base_asset
                    if instrument
                    else symbol[:3]
                    if len(symbol) >= 3
                    else symbol
                )
                quote = (
                    instrument.quote_asset
                    if instrument
                    else symbol[3:]
                    if len(symbol) >= 6
                    else "USD"
                )

                norm = NormalizedEvent.create_candle(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="deriv",
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
                    interval="1m",
                    received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                    raw_envelope_id=raw_id,
                    asset_class=AssetClass.FOREX,
                )
                await self._publish_normalized(norm)
                self._state.messages_received += 1

        except Exception as e:
            logger.warning("Failed to normalize Deriv candles: %s", e)
