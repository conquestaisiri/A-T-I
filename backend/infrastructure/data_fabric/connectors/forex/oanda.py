"""OANDA v20 Practice Streaming connector.

OANDA provides a continuous pricing stream for forex instruments.
Requires a free Practice account and API token.
"""

from __future__ import annotations

import asyncio
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

OANDA_PRACTICE_STREAM = "https://stream-fxpractice.oanda.com/v3/accounts"
OANDA_PRACTICE_REST = "https://api-fxpractice.oanda.com/v3"


class OANDAConnector(BaseConnector):
    """OANDA Practice streaming pricing connector.

    Provides continuous price updates (up to 4/sec per instrument) +
    heartbeats every 5 seconds.
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
        self._account_id: str = ""
        self._api_token: str = ""
        self._instruments: list[str] = []

    async def _connect_impl(self) -> None:
        # Get credentials from environment
        auth = self.config.get_all_auth()
        self._api_token = auth.get("api_token") or os.getenv("OANDA_API_TOKEN") or ""
        self._account_id = auth.get("account_id") or os.getenv("OANDA_ACCOUNT_ID") or ""

        if not self._api_token or not self._account_id:
            raise ValueError("OANDA_API_TOKEN and OANDA_ACCOUNT_ID must be set in environment")

        # Build instrument list
        self._instruments = list(self.config.symbols) or [
            "EUR_USD",
            "GBP_USD",
            "USD_JPY",
            "USD_CHF",
            "AUD_USD",
            "USD_CAD",
            "NZD_USD",
            "EUR_GBP",
            "EUR_JPY",
            "GBP_JPY",
        ]

        self._session = aiohttp.ClientSession(
            headers={"Authorization": f"Bearer {self._api_token}"},
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10, sock_read=60),
        )

        # Test REST connection
        async with self._session.get(f"{OANDA_PRACTICE_REST}/accounts/{self._account_id}") as resp:
            if resp.status != 200:
                raise ValueError(f"OANDA account check failed: {resp.status}")

        logger.info("OANDA REST connection verified for account %s", self._account_id)

    async def _disconnect_impl(self) -> None:
        if self._stream_response:
            self._stream_response.close()
            self._stream_response = None
        if self._session:
            await self._session.close()
            self._session = None

    async def _subscribe_impl(self) -> None:
        if self._session is None:
            raise RuntimeError("OANDA session not initialized")
        # Start streaming
        instruments_param = ",".join(self._instruments)
        url = f"{OANDA_PRACTICE_STREAM}/{self._account_id}/pricing/stream"
        params = {"instruments": instruments_param}

        logger.info("Starting OANDA pricing stream for %d instruments", len(self._instruments))

        self._stream_response = await self._session.get(url, params=params)
        if self._stream_response.status != 200:
            text = await self._stream_response.text()
            raise ValueError(f"OANDA stream failed: {self._stream_response.status} - {text}")

        logger.info("OANDA pricing stream started for %s", self._instruments)

    async def _run(self) -> None:
        if not self._stream_response:
            return

        try:
            async for line in self._stream_response.content:
                if not self._running:
                    break
                text_line = line.decode("utf-8").strip()
                if not text_line:
                    continue

                receive_time = time.time()
                try:
                    data = json.loads(text_line)
                    await self._process_message(data, receive_time)
                except json.JSONDecodeError:
                    continue
                except Exception as e:
                    logger.exception("Error processing OANDA message: %s", e)
                    self._state.errors += 1

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.exception("OANDA stream error: %s", e)
            if self._running:
                self._state.errors += 1
                await self._schedule_reconnect()

    async def _process_message(self, data: dict[str, Any], receive_time: float) -> None:
        # OANDA stream sends: {"type": "PRICE", "price": {...}} or {"type": "HEARTBEAT", ...}
        msg_type = data.get("type")

        raw_env = RawEnvelope(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue="oanda",
            data_plane=DataPlane.MARKET,
            asset_class=AssetClass.FOREX,
            received_at=datetime.fromtimestamp(receive_time, tz=UTC),
            raw_payload=data,
            raw_headers={},
            stream_id=msg_type,
        )
        await self._publish_raw(raw_env)

        if msg_type == "PRICE":
            price_data = data.get("price")
            if isinstance(price_data, dict):
                await self._normalize_price(price_data, receive_time, raw_env.envelope_id)
        elif msg_type == "HEARTBEAT":
            logger.debug("OANDA heartbeat: %s", data.get("time"))

    async def _normalize_price(
        self, price_data: dict[str, Any], receive_time: float, raw_id: str
    ) -> None:
        try:
            symbol = price_data.get("instrument", "")
            event_time = datetime.fromisoformat(price_data.get("time", "").replace("Z", "+00:00"))

            bids = price_data.get("bids", [])
            asks = price_data.get("asks", [])

            bid = float(bids[0]["price"]) if bids else None
            ask = float(asks[0]["price"]) if asks else None
            if bid is None or ask is None:
                return

            instrument = (
                self.instrument_master.get_by_venue_symbol("oanda", symbol)
                if self.instrument_master
                else None
            )
            instrument_id = instrument.instrument_id if instrument else f"OANDA_{symbol}"
            base = instrument.base_asset if instrument else symbol.split("_")[0]
            quote = instrument.quote_asset if instrument else symbol.split("_")[1]

            # Quote event
            norm = NormalizedEvent.create_quote(
                source_id=self.config.source_id,
                source_name=self.config.source_name,
                venue="oanda",
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
            logger.warning("Failed to normalize OANDA price: %s", e)
