"""Crypto/Financial RSS News connector.

Aggregates RSS feeds from Cointelegraph, The Block, Bitcoin Magazine,
Bitcoin.com, and other permitted sources.
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

CRYPTO_RSS_FEEDS = {
    "cointelegraph": {
        "name": "Cointelegraph",
        "feeds": {
            "general": "https://cointelegraph.com/rss",
            "bitcoin": "https://cointelegraph.com/rss/tag/bitcoin",
            "ethereum": "https://cointelegraph.com/rss/tag/ethereum",
            "regulation": "https://cointelegraph.com/rss/tag/regulation",
            "defi": "https://cointelegraph.com/rss/tag/defi",
            "market_analysis": "https://cointelegraph.com/rss/category/market-analysis",
        },
    },
    "theblock": {
        "name": "The Block",
        "feeds": {
            "news": "https://www.theblock.co/rss.xml",
        },
    },
    "bitcoinmagazine": {
        "name": "Bitcoin Magazine",
        "feeds": {
            "general": "https://bitcoinmagazine.com/.rss/full/",
        },
    },
    "bitcoincom": {
        "name": "Bitcoin.com News",
        "feeds": {
            "general": "https://news.bitcoin.com/feed/",
        },
    },
}


class RSSNewsConnector(BaseConnector):
    """Aggregated RSS news connector for crypto/financial sources."""

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._session: Any = None
        self._feeds_config = config.metadata.get("feeds", CRYPTO_RSS_FEEDS)
        self._last_etags: dict[str, str] = {}
        self._seen_guids: dict[str, None] = {}
        self._seen_guids_max = 10000

    async def _connect_impl(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "ATI-DataFabric/1.0"},
        )
        logger.info("RSS News connector initialized for %d sources", len(self._feeds_config))

    async def _disconnect_impl(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _subscribe_impl(self) -> None:
        for source_id, source_config in self._feeds_config.items():
            for feed_name, feed_url in source_config.get("feeds", {}).items():
                await self._fetch_feed(source_id, source_config, feed_name, feed_url)
        logger.info("RSS News feeds initialized")

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(120)  # 2 min
            if not self._running:
                break
            try:
                for source_id, source_config in self._feeds_config.items():
                    for feed_name, feed_url in source_config.get("feeds", {}).items():
                        await self._fetch_feed(source_id, source_config, feed_name, feed_url)
            except Exception as e:
                logger.warning("RSS News fetch failed: %s", e)
                self._state.errors += 1

    async def _fetch_feed(
        self, source_id: str, source_config: dict[str, Any], feed_name: str, feed_url: str
    ) -> None:
        if not self._session:
            return

        try:
            headers = {}
            etag = self._last_etags.get(f"{source_id}:{feed_name}")
            if etag:
                headers["If-None-Match"] = etag

            async with self._session.get(feed_url, headers=headers) as resp:
                if resp.status == 304:
                    return
                if resp.status != 200:
                    return

                new_etag = resp.headers.get("ETag")
                if new_etag:
                    self._last_etags[f"{source_id}:{feed_name}"] = new_etag

                content = await resp.text()
                feed = feedparser.parse(content)
                await self._process_feed(source_id, source_config, feed_name, feed)

        except Exception as e:
            logger.warning("RSS feed %s:%s failed: %s", source_id, feed_name, e)

    async def _process_feed(
        self, source_id: str, source_config: dict[str, Any], feed_name: str, feed: Any
    ) -> None:
        source_name = source_config.get("name", source_id)

        for entry in feed.entries:
            try:
                guid = entry.get("guid") or entry.get("link") or entry.get("id")
                if guid in self._seen_guids:
                    continue
                self._seen_guids[guid] = None
                if len(self._seen_guids) > self._seen_guids_max:
                    # Evict oldest (LRU approx via insertion order)
                    oldest = next(iter(self._seen_guids))
                    del self._seen_guids[oldest]

                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                published = entry.get("published", "")

                # Check crypto relevance
                if not self._is_crypto_relevant(title, summary):
                    continue

                event_time = datetime.now(UTC)
                with contextlib.suppress(Exception):
                    event_time = datetime.fromisoformat(published.replace("Z", "+00:00"))

                raw_env = RawEnvelope(
                    source_id=self.config.source_id,
                    source_name=f"{source_name} - {feed_name}",
                    venue=source_id,
                    data_plane=DataPlane.INTELLIGENCE,
                    asset_class=AssetClass.CRYPTO,
                    received_at=datetime.now(UTC),
                    raw_payload={
                        "title": title,
                        "summary": summary,
                        "link": link,
                        "published": published,
                    },
                    raw_headers={},
                    stream_id=f"{source_id}:{feed_name}",
                )
                await self._publish_raw(raw_env)

                entities, assets, currencies = self._extract_entities_assets(title, summary)
                impact = self._estimate_impact(title, summary)

                norm = NormalizedEvent.create_news(
                    source_id=self.config.source_id,
                    source_name=source_name,
                    venue=source_id,
                    event_time=event_time,
                    headline=title,
                    url=link,
                    entities=entities,
                    assets=assets,
                    currencies=currencies,
                    regions=[],
                    category="crypto_news",
                    source_quality=0.8,
                    novelty_score=0.8,
                    relevance_score=0.85,
                    impact_score=impact,
                    received_at=datetime.now(UTC),
                    raw_envelope_id=raw_env.envelope_id,
                    asset_class=AssetClass.CRYPTO,
                )
                await self._publish_normalized(norm)
                self._state.messages_received += 1

            except Exception as e:
                logger.warning("Failed to process RSS entry: %s", e)

    def _is_crypto_relevant(self, title: str, summary: str) -> bool:
        keywords = [
            "bitcoin",
            "btc",
            "ethereum",
            "eth",
            "crypto",
            "blockchain",
            "defi",
            "nft",
            "web3",
            "binance",
            "coinbase",
            "kraken",
            "regulation",
            "sec",
            "etf",
            "mining",
            "staking",
            "altcoin",
            "solana",
            "cardano",
            "polkadot",
            "avalanche",
        ]
        text = f"{title} {summary}".lower()
        return any(kw in text for kw in keywords)

    def _extract_entities_assets(
        self, title: str, summary: str
    ) -> tuple[list[str], list[str], list[str]]:
        text = f"{title} {summary}".lower()
        entities = []
        assets = []
        currencies = []

        asset_map = {
            "bitcoin": "BTC",
            "btc": "BTC",
            "ethereum": "ETH",
            "eth": "ETH",
            "solana": "SOL",
            "sol": "SOL",
            "binance coin": "BNB",
            "bnb": "BNB",
            "ripple": "XRP",
            "xrp": "XRP",
            "cardano": "ADA",
            "ada": "ADA",
            "dogecoin": "DOGE",
            "doge": "DOGE",
            "polygon": "MATIC",
            "matic": "MATIC",
            "polkadot": "DOT",
            "dot": "DOT",
            "avalanche": "AVAX",
            "avax": "AVAX",
        }

        for kw, asset in asset_map.items():
            if kw in text:
                assets.append(asset)
                entities.append(kw.title())

        if "bitcoin" in text or "btc" in text:
            currencies.append("USD")
        if "ethereum" in text or "eth" in text:
            currencies.append("USD")

        return list(set(entities)), list(set(assets)), list(set(currencies))

    def _estimate_impact(self, title: str, summary: str) -> float:
        text = f"{title} {summary}".lower()
        high_keywords = [
            "etf",
            "sec",
            "regulation",
            "ban",
            "hack",
            "crash",
            "surge",
            "record",
            "adoption",
            "institutional",
        ]
        medium_keywords = ["partnership", "launch", "upgrade", "fork", "listing", "integration"]
        if any(kw in text for kw in high_keywords):
            return 0.8
        if any(kw in text for kw in medium_keywords):
            return 0.5
        return 0.3
