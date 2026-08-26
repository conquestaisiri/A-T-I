"""Official Central Bank RSS connectors.

Federal Reserve, ECB, BLS provide official RSS feeds for releases.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import UTC, datetime
from typing import Any

import aiohttp
import feedparser

from .....domain.data_fabric.enums import AssetClass, DataPlane
from .....domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from .....domain.data_fabric.instrument import InstrumentMaster
from .....domain.data_fabric.source import SourceConfig
from ..base import BaseConnector

logger = logging.getLogger(__name__)

# Central Bank RSS Feeds
CENTRAL_BANK_FEEDS: dict[str, dict[str, Any]] = {
    "fed": {
        "name": "Federal Reserve",
        "currency": "USD",
        "feeds": {
            "press_releases": "https://www.federalreserve.gov/feeds/press_releases.xml",
            "monetary_policy": "https://www.federalreserve.gov/feeds/monetary_policy.xml",
            "speeches": "https://www.federalreserve.gov/feeds/speeches.xml",
            "testimony": "https://www.federalreserve.gov/feeds/testimony.xml",
            "data": "https://www.federalreserve.gov/feeds/data.xml",
        },
    },
    "ecb": {
        "name": "European Central Bank",
        "currency": "EUR",
        "feeds": {
            "press_releases": "https://www.ecb.europa.eu/home/html/rss.en.html",
            "speeches": "https://www.ecb.europa.eu/home/html/rss_speeches.en.html",
            "statistics": "https://www.ecb.europa.eu/home/html/rss_statistics.en.html",
            "market_ops": "https://www.ecb.europa.eu/home/html/rss_market_operations.en.html",
        },
    },
    "bls": {
        "name": "Bureau of Labor Statistics",
        "currency": "USD",
        "feeds": {
            "releases": "https://www.bls.gov/feed/releases.rss",
            "cpi": "https://www.bls.gov/feed/cpi.rss",
            "employment": "https://www.bls.gov/feed/employment.rss",
        },
    },
    "boe": {
        "name": "Bank of England",
        "currency": "GBP",
        "feeds": {
            "releases": "https://www.bankofengland.co.uk/rss/boe_releases.xml",
            "speeches": "https://www.bankofengland.co.uk/rss/boe_speeches.xml",
        },
    },
}


class CentralBankConnector(BaseConnector):
    """Aggregated central bank RSS connector.

    Monitors official RSS feeds from Fed, ECB, BLS, BoE for
    monetary policy, economic releases, speeches.
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._session: Any = None
        self._feeds_config = config.metadata.get("feeds", CENTRAL_BANK_FEEDS)
        self._last_etags: dict[str, str] = {}

    async def _connect_impl(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "ATI-DataFabric/1.0"},
        )
        logger.info(
            "Central Bank RSS connector initialized for %d sources", len(self._feeds_config)
        )

    async def _disconnect_impl(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _subscribe_impl(self) -> None:
        # Initial fetch all feeds
        for bank_id, bank_config in self._feeds_config.items():
            for feed_name, feed_url in bank_config.get("feeds", {}).items():
                await self._fetch_feed(bank_id, bank_config, feed_name, feed_url)
        logger.info("Central Bank RSS feeds initialized")

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(300)  # 5 min
            if not self._running:
                break
            try:
                for bank_id, bank_config in self._feeds_config.items():
                    for feed_name, feed_url in bank_config.get("feeds", {}).items():
                        await self._fetch_feed(bank_id, bank_config, feed_name, feed_url)
            except Exception as e:
                logger.warning("Central bank feed fetch failed: %s", e)
                self._state.errors += 1

    async def _fetch_feed(
        self, bank_id: str, bank_config: dict[str, Any], feed_name: str, feed_url: str
    ) -> None:
        if not self._session:
            return

        try:
            headers = {}
            etag = self._last_etags.get(f"{bank_id}:{feed_name}")
            if etag:
                headers["If-None-Match"] = etag

            async with self._session.get(feed_url, headers=headers) as resp:
                if resp.status == 304:
                    return
                if resp.status != 200:
                    return

                new_etag = resp.headers.get("ETag")
                if new_etag:
                    self._last_etags[f"{bank_id}:{feed_name}"] = new_etag

                content = await resp.text()
                feed = feedparser.parse(content)
                await self._process_feed(bank_id, bank_config, feed_name, feed)

        except Exception as e:
            logger.warning("Central bank feed %s:%s failed: %s", bank_id, feed_name, e)

    async def _process_feed(
        self, bank_id: str, bank_config: dict[str, Any], feed_name: str, feed: Any
    ) -> None:
        currency = bank_config.get("currency", "USD")
        bank_name = bank_config.get("name", bank_id)

        for entry in feed.entries:
            try:
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                published = entry.get("published", "")

                # Skip if no financial relevance
                if not self._is_financially_relevant(title, summary):
                    continue

                event_time = datetime.now(UTC)
                with contextlib.suppress(Exception):
                    event_time = datetime.fromisoformat(published.replace("Z", "+00:00"))

                # Determine impact
                impact = self._classify_impact(title, summary, bank_id)

                raw_env = RawEnvelope(
                    source_id=self.config.source_id,
                    source_name=f"{bank_name} - {feed_name}",
                    venue=bank_id,
                    data_plane=DataPlane.MACRO,
                    asset_class=AssetClass.RATES,
                    received_at=datetime.now(UTC),
                    raw_payload={
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "published": published,
                    },
                    raw_headers={},
                    stream_id=f"{bank_id}:{feed_name}",
                )
                await self._publish_raw(raw_env)

                # Classify as macro event or news
                if self._is_scheduled_release(title, summary):
                    norm = NormalizedEvent.create_macro(
                        source_id=self.config.source_id,
                        source_name=bank_name,
                        venue=bank_id,
                        event_time=event_time,
                        country=currency,
                        currency=currency,
                        event_name=title[:200],
                        impact=impact,
                        forecast=None,
                        previous=None,
                        actual=None,
                        revision=None,
                        status="RELEASED",
                        received_at=datetime.now(UTC),
                        raw_envelope_id=raw_env.envelope_id,
                        asset_class=AssetClass.RATES,
                        quality_score=0.95,
                    )
                else:
                    norm = NormalizedEvent.create_news(
                        source_id=self.config.source_id,
                        source_name=bank_name,
                        venue=bank_id,
                        event_time=event_time,
                        headline=title,
                        url=link,
                        entities=[bank_name, currency],
                        assets=[currency],
                        currencies=[currency],
                        regions=[currency],
                        category="central_bank",
                        source_quality=0.95,
                        novelty_score=0.8,
                        relevance_score=0.9,
                        impact_score=0.8 if impact == "HIGH" else 0.5,
                        received_at=datetime.now(UTC),
                        raw_envelope_id=raw_env.envelope_id,
                        asset_class=AssetClass.RATES,
                    )

                await self._publish_normalized(norm)
                self._state.messages_received += 1

            except Exception as e:
                logger.warning("Failed to process CB entry: %s", e)

    def _is_financially_relevant(self, title: str, summary: str) -> bool:
        keywords = [
            "rate",
            "interest",
            "inflation",
            "cpi",
            "pce",
            "employment",
            "unemployment",
            "payroll",
            "gdp",
            "monetary",
            "policy",
            "speech",
            "testimony",
            "minutes",
            "decision",
            "statement",
            "inflation",
            "outlook",
            "forecast",
            "projection",
        ]
        text = f"{title} {summary}".lower()
        return any(kw in text for kw in keywords)

    def _classify_impact(self, title: str, summary: str, bank_id: str) -> str:
        text = f"{title} {summary}".lower()
        high_keywords = [
            "rate decision",
            "fomc",
            "monetary policy",
            "interest rate",
            "cpi",
            "employment",
            "nfp",
        ]
        if any(kw in text for kw in high_keywords):
            return "HIGH"
        medium_keywords = ["speech", "testimony", "minutes", "outlook", "forecast"]
        if any(kw in text for kw in medium_keywords):
            return "MEDIUM"
        return "LOW"

    def _is_scheduled_release(self, title: str, summary: str) -> bool:
        text = f"{title} {summary}".lower()
        return any(
            kw in text
            for kw in ["release", "report", "data", "cpi", "pce", "employment", "payroll", "gdp"]
        )


# Convenience function to create all central bank configs
def create_central_bank_configs() -> list[SourceConfig]:
    """Create SourceConfig for each central bank."""
    from .....domain.data_fabric.enums import SourceTier
    from .....domain.data_fabric.source import (
        AuthType,
        SourceConfig,
        TransportType,
    )

    configs = []
    for bank_id, bank_config in CENTRAL_BANK_FEEDS.items():
        configs.append(
            SourceConfig(
                source_id=f"cb_{bank_id}",
                source_name=bank_config["name"],
                data_plane=DataPlane.MACRO,
                asset_class=AssetClass.RATES,
                venue=bank_id,
                transport=TransportType.RSS,
                auth_type=AuthType.NONE,
                base_url="",
                channels=tuple(bank_config["feeds"].keys()),
                source_tier=SourceTier.TIER_1,
                priority=10,
                # Connector contract: metadata["feeds"] maps
                # {bank_id: {"name": ..., "feeds": {...}}} — pass the full
                # bank entry so _subscribe_impl/_run can call .get("feeds").
                metadata={"feeds": {bank_id: bank_config}},
            )
        )
    return configs
