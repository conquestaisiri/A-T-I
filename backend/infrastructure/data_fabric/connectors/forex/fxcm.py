"""FXCM Demo Streaming connector.

FXCM provides streaming market data via their REST API with Server-Sent Events.
Requires a free Demo account and API token.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .....domain.data_fabric.enums import AssetClass, DataPlane
from .....domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from .....domain.data_fabric.instrument import InstrumentMaster
from .....domain.data_fabric.source import SourceConfig
from ..base import BaseConnector

logger = logging.getLogger(__name__)

FXCM_DEMO_STREAM = "https://api-demo.fxcm.com/trading-api/v1/quotes/stream"
FXCM_DEMO_REST = "https://api-demo.fxcm.com/trading-api/v1"


class FXCMConnector(BaseConnector):
    """FXCM Demo streaming pricing connector.

    Provides continuous price updates via Server-Sent Events.
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._session: aiohttp.ClientSession | None = None
        self._stream_response: aiohttp.ClientResponse | None = None
        self._api_token: str = ""
        self._symbols: list[str] = []

    async def _connect_impl(self) -> None:
        auth = self.config.get_all_auth()
        self._api_token = auth.get("api_token") or os.getenv("FXCM_API_TOKEN") or ""

        if not self._api_token:
            raise ValueError("FXCM_API_TOKEN must be set in environment")

        self._symbols = list(self.config.symbols) or [
            "EUR/USD",
            "GBP/USD",
            "USD/JPY",
            "USD/CHF",
            "AUD/USD",
            "USD/CAD",
            "NZD/USD",
            "EUR/GBP",
        ]

        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=90),
        )

        # Test REST connection
        async with self._session.get(f"{FXCM_DEMO_REST}/accounts") as resp:
            if resp.status != 200:
                raise ValueError(f"FXCM account check failed: {resp.status}")

        logger.info("FXCM REST connection verified")

    async def _disconnect_impl(self) -> None:
        if self._stream_response:
            self._stream_response.close()
            self._stream_response = None
        if self._session:
            await self._session.close()
            self._session = None

    async def _subscribe_impl(self) -> None:
        if self._session is None:
            raise RuntimeError("FXCM session not initialized")
        symbols_param = ",".join(self._symbols)
        url = FXCM_DEMO_STREAM
        params = {"symbols": symbols_param}

        logger.info("Starting FXCM price stream for %d instruments", len(self._symbols))

        self._stream_response = await self._session.get(url, params=params)
        if self._stream_response.status != 200:
            text = await self._stream_response.text()
            raise ValueError(f"FXCM stream failed: {self._stream_response.status} - {text}")

        logger.info("FXCM price stream started for %s", self._symbols)

    async def _run(self) -> None:
        if not self._stream_response:
            return

        try:
            async for line in self._stream_response.content:
                if not self._running:
                    break
                text_line = line.decode("utf-8").strip()
                if not text_line or text_line.startswith(":"):
                    continue

                receive_time = time.time()
                try:
                    data = json.loads(text_line)
                    await self._process_message(data, receive_time)
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.exception("Error processing FXCM message: %s", e)
                    self._state.errors += 1

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("FXCM stream error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        # FXCM SSE format varies; handle common structures
        symbol = data.get("Symbol") or data.get("symbol")
        if not symbol:
            return

        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="fxcm",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.FOREX,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=data,
            raw_headers={},
        )
        await self._publish_raw(raw_env)

        await self._normalize_quote(data, symbol, receive_time, raw_id=raw_env.envelope_id)

    async def _normalize_quote(
        self, data: dict[str, Any], symbol: str, receive_time: float, raw_id: str
    ) -> None:
        try:
            raw_ts = data.get("Updated") or data.get("Time") or data.get("timestamp")
            event_time: datetime | None = None
            if raw_ts is None:
                event_time = datetime.fromtimestamp(receive_time, tz=UTC)
            elif isinstance(raw_ts, str):
                # Try ISO 8601 first (e.g. "2026-08-20T10:00:00Z")
                with contextlib.suppress(Exception):
                    event_time = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
                if event_time is None:
                    with contextlib.suppress(Exception):
                        num = float(raw_ts)
                        if num > 1e11:  # milliseconds
                            num /= 1000
                        event_time = datetime.fromtimestamp(num, tz=UTC)
                if event_time is None:
                    event_time = datetime.fromtimestamp(receive_time, tz=UTC)
            elif isinstance(raw_ts, (int, float)):
                num = float(raw_ts)
                if num > 1e11:  # milliseconds
                    num /= 1000
                with contextlib.suppress(Exception):
                    event_time = datetime.fromtimestamp(num, tz=UTC)
                if event_time is None:
                    event_time = datetime.fromtimestamp(receive_time, tz=UTC)
            else:
                event_time = datetime.fromtimestamp(receive_time, tz=UTC)
            assert event_time is not None
            bid = float(data.get("Bid") or data.get("bid", 0)) or None
            ask = float(data.get("Ask") or data.get("ask", 0)) or None
            if bid is None or ask is None:
                return

            instrument = (
                self.instrument_master.get_by_venue_symbol("fxcm", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"FXCM_{symbol}"
            base = instrument.base_asset if instrument else symbol.split("/")[0]
            quote = instrument.quote_asset if instrument else symbol.split("/")[1]

            norm = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="fxcm",
                instrument_id=instrument_id,
                symbol=symbol,
                base_asset=base,
                quote_asset=quote,
                event_time=event_time,
                bid=bid,
                ask=ask,
                received_at=datetime.fromtimestamp(receive_time, tz=UTC),
                raw_envelope_id=raw_id,
                asset_class=AssetClass.FOREX,
            )
            await self._publish_normalized(norm)
            self._state.messages_received += 1
        except Exception as e:
            logger.warning("Failed to normalize FXCM quote: %s", e)
