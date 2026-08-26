# Binance adapter implementation for the Observation Layer.
# This adapter connects to Binance's public trade websocket for a given symbol,
# normalises incoming messages into the domain ObservationEvent, and publishes
# them on the ObservationBus.

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any

import websockets
from websockets.asyncio.client import ClientConnection

from ...domain.observation.adapter_interface import ObservationAdapter
from ...domain.observation.event import ObservationEvent, ObservationEventType
from ...infrastructure.observation.observation_bus import ObservationBus

logger = logging.getLogger(__name__)


class BinanceAdapter(ObservationAdapter):
    """Concrete adapter for Binance trade streams.

    Parameters
    ----------
    source_id: str
        Identifier from the Source Registry (e.g., "binance_usdt_futures").
    source_name: str
        Human readable name.
    symbol: str
        Trading pair symbol in lower‑case, e.g., "btcusdt".
    bus: ObservationBus
        Bus instance to publish normalised events.
    ws_url: str, optional
        WebSocket URL; defaults to Binance public trade stream.
    """

    def __init__(
        self,
        source_id: str,
        source_name: str,
        symbol: str,
        bus: ObservationBus,
        ws_url: str | None = None,
    ) -> None:
        self.source_id = source_id
        self.source_name = source_name
        self.symbol = symbol.lower()
        self.bus = bus
        self.ws_url = ws_url or f"wss://stream.binance.com:9443/ws/{self.symbol}@trade"
        self._ws: ClientConnection | None = None
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()
        self._reconnect_attempts = 0
        # Back‑off parameters – could be externalised later.
        self._max_backoff = 30  # seconds
        self._initial_backoff = 1  # seconds

    async def connect(self) -> None:
        """Establish the websocket connection with exponential back‑off."""
        backoff = self._initial_backoff
        while not self._stop.is_set():
            try:
                logger.info("Connecting to Binance WS %s", self.ws_url)
                self._ws = await websockets.connect(self.ws_url, ping_interval=None)
                self._connected.set()
                self._reconnect_attempts = 0
                logger.info("Connected to Binance WS for %s", self.symbol)
                return
            except Exception as exc:  # pragma: no cover – connection failures are rare in tests
                self._connected.clear()
                self._reconnect_attempts += 1
                logger.warning(
                    "Binance connection failed (attempt %d): %s – retry in %s seconds",
                    self._reconnect_attempts,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, self._max_backoff)

    async def disconnect(self) -> None:
        """Close the websocket and signal stop."""
        self._stop.set()
        if self._ws is not None:
            await self._ws.close()
        self._connected.clear()
        logger.info("BinanceAdapter disconnected")

    async def subscribe(self, event_types: list[str]) -> None:
        """Binance public trade stream is a single‑type feed; this is a no‑op.
        The method exists to satisfy the interface and to allow future extensions
        (e.g., ticker, depth).
        """
        # No explicit subscription message required for the trade endpoint.
        logger.debug("BinanceAdapter subscribe called with %s – no action needed", event_types)
        return

    def normalize(self, raw: dict[str, Any]) -> ObservationEvent:
        """Transform Binance trade payload into ObservationEvent.

        Binance trade payload example::
            {
                "e": "trade",     // Event type
                "E": 123456789,    // Event time (ms)
                "s": "BTCUSDT",   // Symbol
                "t": 12345,        // Trade ID
                "p": "0.001",     // Price
                "q": "100",       // Quantity
                "b": 88,           // Buyer order ID
                "a": 50,           // Seller order ID
                "T": 123456785,    // Trade time (ms)
                "m": true,         // Is buyer the market maker?
                "M": true          // Ignore
            }
        """
        try:
            # Ensure required keys exist
            required = {"e", "E", "s", "p", "q", "T"}
            if not required.issubset(raw.keys()):
                raise ValueError("Missing required fields in Binance trade payload")

            event = ObservationEvent(
                source_id=self.source_id,
                source_name=self.source_name,
                event_type=ObservationEventType.TRADE,
                timestamp=datetime.fromtimestamp(raw["E"] / 1000.0, tz=UTC),
                payload={
                    "symbol": raw["s"],
                    "trade_id": raw["t"],
                    "price": float(raw["p"]),
                    "quantity": float(raw["q"]),
                    "buyer_order_id": raw["b"],
                    "seller_order_id": raw["a"],
                    "trade_time": datetime.fromtimestamp(raw["T"] / 1000.0, tz=UTC),
                    "is_market_maker": raw["m"],
                },
            )
            return event
        except Exception as exc:
            logger.exception("Failed to normalize Binance payload: %s", exc)
            raise

    async def _receive_loop(self) -> None:
        """Continuously receive messages, normalize, and publish."""
        while not self._stop.is_set():
            if not self._connected.is_set():
                await self.connect()
                continue
            if self._ws is None:
                await self.connect()
                continue
            try:
                raw_msg = await self._ws.recv()
                data = json.loads(raw_msg)
                event = self.normalize(data)
                await self.bus.publish(event)
            except websockets.ConnectionClosed:
                logger.warning("Binance websocket closed – reconnecting")
                self._connected.clear()
                await self.connect()
            except Exception as exc:  # pragma: no cover
                logger.error("Error in Binance receive loop: %s", exc)
                # Continue receiving – a single malformed message should not stop the loop.

    async def health(self) -> dict[str, Any]:
        """Return a minimal health snapshot."""
        return {
            "connected": self._connected.is_set(),
            "stop_requested": self._stop.is_set(),
            "reconnect_attempts": self._reconnect_attempts,
        }

    async def start(self) -> None:
        """Convenience helper to run the full adapter lifecycle.
        This method is intended for the runnable script – it connects and starts the
        receive loop until cancelled.
        """
        await self.connect()
        # No explicit subscribe needed for trade stream.
        await self._receive_loop()

    async def stop(self) -> None:
        """Stop the adapter gracefully."""
        await self.disconnect()
