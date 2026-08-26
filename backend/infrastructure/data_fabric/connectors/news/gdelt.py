"""GDELT 2.0 Global News connector.

GDELT monitors global news and provides machine-readable data.
Updates every 15 minutes. Useful for global event detection.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import zipfile
from datetime import UTC, datetime
from typing import Any

import aiohttp

from .....domain.data_fabric.enums import AssetClass, DataPlane
from .....domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from .....domain.data_fabric.instrument import InstrumentMaster
from .....domain.data_fabric.source import SourceConfig
from ..base import BaseConnector

logger = logging.getLogger(__name__)

GDELT_BASE_URL = "https://data.gdeltproject.org/gdeltv2"
GDELT_LASTUPDATE = f"{GDELT_BASE_URL}/lastupdate.txt"
GDELT_GKG_URL = f"{GDELT_BASE_URL}/gkg"


class GDELTConnector(BaseConnector):
    """GDELT 2.0 Global Knowledge Graph connector."""

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._session: Any = None
        self._last_timestamp: str | None = None

    async def _connect_impl(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60),
            headers={"User-Agent": "ATI-DataFabric/1.0"},
        )
        self._last_timestamp = await self._get_latest_timestamp()
        logger.info("GDELT connector initialized, latest: %s", self._last_timestamp)

    async def _disconnect_impl(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _subscribe_impl(self) -> None:
        await self._fetch_gkg()
        logger.info("GDELT initial fetch complete")

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(900)
            if not self._running:
                break
            try:
                await self._fetch_gkg()
            except Exception as e:
                logger.warning("GDELT fetch failed: %s", e)
                self._state.errors += 1

    async def _get_latest_timestamp(self) -> str | None:
        if not self._session:
            return None
        try:
            async with self._session.get(GDELT_LASTUPDATE) as resp:
                if resp.status == 200:
                    content = await resp.text()
                    lines = content.strip().split("\n")
                    for line in lines:
                        if ".gkg.csv.zip" in line:
                            parts = line.split()
                            if parts:
                                return str(parts[0])
        except Exception as e:
            logger.warning("Failed to get GDELT lastupdate: %s", e)
        return None

    async def _fetch_gkg(self) -> None:
        if not self._session or not self._last_timestamp:
            return

        url = f"{GDELT_GKG_URL}/{self._last_timestamp}.gkg.csv.zip"

        try:
            async with self._session.get(url) as resp:
                if resp.status == 304:
                    return
                if resp.status != 200:
                    new_ts = await self._get_latest_timestamp()
                    if new_ts and new_ts != self._last_timestamp:
                        self._last_timestamp = new_ts
                    return

                content = await resp.read()
                await self._process_gkg_zip(content)
                logger.info("Processed GDELT GKG: %s", self._last_timestamp)

        except Exception as e:
            logger.warning("GDELT fetch failed: %s", e)

    async def _process_gkg_zip(self, zip_content: bytes) -> None:
        try:
            with zipfile.ZipFile(io.BytesIO(zip_content)) as zf:
                for name in zf.namelist():
                    if name.endswith(".csv"):
                        with zf.open(name) as f:
                            content = f.read().decode("utf-8", errors="ignore")
                            await self._parse_gkg_csv(content)
        except Exception as e:
            logger.warning("Failed to process GDELT ZIP: %s", e)

    async def _parse_gkg_csv(self, csv_content: str) -> None:
        reader = csv.reader(io.StringIO(csv_content), delimiter="\t")
        for row in reader:
            if len(row) < 12:
                continue

            try:
                themes = row[7]
                locations = row[8]
                persons = row[9]
                organizations = row[10]
                tone = row[11]

                if not self._is_financially_relevant(themes, organizations):
                    continue

                assets, currencies = self._extract_assets_currencies(themes, locations)
                event_time = datetime.now(UTC)

                raw_env = RawEnvelope(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="gdelt",
                    data_plane=DataPlane.INTELLIGENCE,
                    asset_class=AssetClass.ALTERNATIVE,
                    received_at=datetime.now(UTC),
                    raw_payload={
                        "themes": themes,
                        "locations": locations,
                        "persons": persons,
                        "organizations": organizations,
                        "tone": tone,
                    },
                    raw_headers={},
                )
                await self._publish_raw(raw_env)

                norm = NormalizedEvent.create_news(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="gdelt",
                    event_time=event_time,
                    headline=f"GDELT: {themes[:200]}",
                    url="",
                    entities=[organizations, persons],
                    assets=assets,
                    currencies=currencies,
                    regions=locations.split(";")[:5],
                    category="global_event",
                    source_quality=0.7,
                    novelty_score=0.8,
                    relevance_score=0.6,
                    impact_score=0.5,
                    received_at=datetime.now(UTC),
                    raw_envelope_id=raw_env.envelope_id,
                    asset_class=AssetClass.ALTERNATIVE,
                )
                await self._publish_normalized(norm)
                self._state.messages_received += 1

            except Exception as e:
                logger.debug("GDELT row parse failed: %s", e)

    def _is_financially_relevant(self, themes: str, organizations: str) -> bool:
        financial_keywords = [
            "ECON",
            "FINA",
            "BANK",
            "MONETARY",
            "FISCAL",
            "TRADE",
            "STOCK",
            "MARKET",
            "CURRENCY",
            "EXCHANGE",
            "CRYPTO",
            "BITCOIN",
            "ETHEREUM",
            "CRYPTOCURRENCY",
            "BLOCKCHAIN",
            "CENTRAL_BANK",
            "FEDERAL_RESERVE",
            "ECB",
            "BANK_OF_",
        ]
        text = f"{themes} {organizations}".upper()
        return any(kw in text for kw in financial_keywords)

    def _extract_assets_currencies(
        self, themes: str, locations: str
    ) -> tuple[list[str], list[str]]:
        assets = []
        currencies = []

        currency_map = {
            "UNITED STATES": "USD",
            "USA": "USD",
            "EUROPEAN UNION": "EUR",
            "GERMANY": "EUR",
            "FRANCE": "EUR",
            "ITALY": "EUR",
            "SPAIN": "EUR",
            "UNITED KINGDOM": "GBP",
            "UK": "GBP",
            "BRITAIN": "GBP",
            "JAPAN": "JPY",
            "CHINA": "CNY",
            "CANADA": "CAD",
            "AUSTRALIA": "AUD",
            "NEW ZEALAND": "NZD",
            "SWITZERLAND": "CHF",
        }

        for loc in locations.split(";"):
            loc_upper = loc.strip().upper()
            if loc_upper in currency_map:
                currencies.append(currency_map[loc_upper])

        if "CRYPTOCURRENCY" in themes.upper() or "BITCOIN" in themes.upper():
            assets.append("BTC")
        if "ETHEREUM" in themes.upper():
            assets.append("ETH")

        return assets, list(set(currencies))
