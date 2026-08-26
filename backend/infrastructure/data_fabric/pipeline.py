"""News processing pipeline: ingestion -> entity extraction -> asset mapping -> impact scoring."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import asdict
from typing import Any

from ...domain.data_fabric.envelope import NormalizedEvent

logger = logging.getLogger(__name__)


class NewsPipeline:
    """News processing pipeline with entity extraction and impact scoring."""

    def __init__(self) -> None:
        self._asset_patterns = self._build_asset_patterns()
        self._currency_patterns = self._build_currency_patterns()
        self._entity_patterns = self._build_entity_patterns()
        self._impact_keywords = self._build_impact_keywords()
        self._source_quality = self._build_source_quality()

    def _build_asset_patterns(self) -> dict[str, list[str]]:
        return {
            "BTC": [r"\bbitcoin\b", r"\bbtc\b", r"\bxbt\b"],
            "ETH": [r"\bethereum\b", r"\beth\b"],
            "SOL": [r"\bsolana\b", r"\bsol\b"],
            "BNB": [r"\bbinance\s*coin\b", r"\bbnb\b"],
            "XRP": [r"\bripple\b", r"\bxrp\b"],
            "ADA": [r"\bcardano\b", r"\bada\b"],
            "DOGE": [r"\bdogecoin\b", r"\bdoge\b"],
            "MATIC": [r"\bpolygon\b", r"\bmatic\b"],
            "DOT": [r"\bpolkadot\b", r"\bdot\b"],
            "AVAX": [r"\bavalanche\b", r"\bavax\b"],
            "LINK": [r"\bchainlink\b", r"\blink\b"],
            "UNI": [r"\buniswap\b", r"\buni\b"],
            "ATOM": [r"\bcosmos\b", r"\batom\b"],
            "NEAR": [r"\bnear\b"],
            "ARB": [r"\barbitrum\b", r"\barb\b"],
            "OP": [r"\boptimism\b", r"\bop\b"],
        }

    def _build_currency_patterns(self) -> dict[str, list[str]]:
        return {
            "USD": [r"\busd\b", r"\bus\s*dollar\b", r"\bdollar\b", r"\$\b"],
            "EUR": [r"\beur\b", r"\beuro\b", r"\b€\b"],
            "GBP": [r"\bgbp\b", r"\bpound\b", r"\b£\b"],
            "JPY": [r"\bjpy\b", r"\byen\b", r"\b¥\b"],
            "CHF": [r"\bchf\b", r"\bswiss\s*franc\b"],
            "CAD": [r"\bcad\b", r"\bcanadian\s*dollar\b"],
            "AUD": [r"\baud\b", r"\baustralian\s*dollar\b"],
            "NZD": [r"\bnzd\b", r"\bnew\s*zealand\s*dollar\b"],
            "CNY": [r"\bcny\b", r"\byuan\b", r"\brmb\b"],
        }

    def _build_entity_patterns(self) -> dict[str, list[str]]:
        return {
            "FED": [r"\bfed\b", r"\bfederal\s*reserve\b", r"\bfomc\b", r"\bpowell\b"],
            "ECB": [r"\becb\b", r"\beuropean\s*central\s*bank\b", r"\blagarde\b"],
            "BOE": [r"\bboe\b", r"\bbank\s*of\s*england\b", r"\bbailey\b"],
            "BOJ": [r"\boj\b", r"\bbank\s*of\s*japan\b", r"\bueda\b"],
            "BIS": [r"\bbis\b", r"\bbank\s*for\s*international\s*settlements\b"],
            "IMF": [r"\bimf\b", r"\binternational\s*monetary\s*fund\b"],
            "SEC": [r"\bsec\b", r"\bsecurities\s*exchange\s*commission\b", r"\bgensler\b"],
            "CFTC": [r"\bcftc\b", r"\bcommodity\s*futures\s*trading\s*commission\b"],
            "BINANCE": [r"\bbinance\b", r"\bcz\b", r"\bchangpeng\b"],
            "COINBASE": [r"\bcoinbase\b", r"\barmstrong\b"],
            "KRAKEN": [r"\bkraken\b"],
            "BYBIT": [r"\bbybit\b"],
            "TETHER": [r"\btether\b", r"\busdt\b"],
            "CIRCLE": [r"\bcircle\b", r"\busdc\b"],
        }

    def _build_impact_keywords(self) -> dict[str, list[str]]:
        return {
            "HIGH": [
                "etf",
                "sec",
                "regulation",
                "ban",
                "hack",
                "exploit",
                "crash",
                "surge",
                "record",
                "adoption",
                "institutional",
                "rate decision",
                "fomc",
                "cpi",
                "inflation",
                "employment",
                "nfp",
                "payroll",
                "gdp",
                "monetary policy",
                "interest rate",
                "liquidation",
                "bankruptcy",
                "insolvency",
                "bailout",
            ],
            "MEDIUM": [
                "partnership",
                "launch",
                "upgrade",
                "fork",
                "listing",
                "integration",
                "acquisition",
                "merger",
                "funding",
                "investment",
                "speech",
                "testimony",
                "minutes",
                "outlook",
                "forecast",
                "mainnet",
                "testnet",
                "airdrop",
                "staking",
                "governance",
            ],
            "LOW": [
                "analysis",
                "opinion",
                "interview",
                "report",
                "research",
                "price",
                "prediction",
                "technical",
                "chart",
                "pattern",
            ],
        }

    def _build_source_quality(self) -> dict[str, float]:
        return {
            "fed": 0.95,
            "ecb": 0.95,
            "boe": 0.95,
            "boj": 0.95,
            "bls": 0.95,
            "cftc": 0.95,
            "forex_factory": 0.85,
            "cointelegraph": 0.8,
            "theblock": 0.85,
            "bitcoinmagazine": 0.75,
            "bitcoincom": 0.7,
            "gdelt": 0.7,
            "binance": 0.9,
            "coinbase": 0.9,
            "kraken": 0.9,
            "bybit": 0.85,
            "oanda": 0.9,
            "fxcm": 0.85,
        }

    def process(self, event: NormalizedEvent) -> NormalizedEvent:
        """Process a news event through the pipeline."""
        if event.event_type != "news":
            return event

        text = f"{event.payload.get('headline', '')} {event.payload.get('url', '')}".lower()

        # Extract entities
        entities = self._extract_entities(text)
        # Map to assets
        assets = self._map_to_assets(text)
        # Map to currencies
        currencies = self._map_to_currencies(text)
        # Estimate impact
        impact_score = self._estimate_impact(text)
        # Source quality
        source_quality = self._get_source_quality(event.source_id)

        # Update payload
        new_payload = dict(event.payload)
        new_payload.update(
            {
                "entities": list(set(entities + event.payload.get("entities", []))),
                "assets": list(set(assets + event.payload.get("assets", []))),
                "currencies": list(set(currencies + event.payload.get("currencies", []))),
                "impact_score": max(impact_score, event.payload.get("impact_score", 0)),
                "source_quality": max(source_quality, event.payload.get("source_quality", 0)),
                "news_processed": True,
            }
        )

        return NormalizedEvent(
            **{k: v for k, v in asdict(event).items() if k != "payload"},
            payload=new_payload,
        )

    def _extract_entities(self, text: str) -> list[str]:
        entities = []
        for entity, patterns in self._entity_patterns.items():
            if any(re.search(p, text) for p in patterns):
                entities.append(entity)
        return entities

    def _map_to_assets(self, text: str) -> list[str]:
        assets = []
        for asset, patterns in self._asset_patterns.items():
            if any(re.search(p, text) for p in patterns):
                assets.append(asset)
        return assets

    def _map_to_currencies(self, text: str) -> list[str]:
        currencies = []
        for currency, patterns in self._currency_patterns.items():
            if any(re.search(p, text) for p in patterns):
                currencies.append(currency)
        return currencies

    def _estimate_impact(self, text: str) -> float:
        for level, keywords in self._impact_keywords.items():
            if any(kw in text for kw in keywords):
                return {"HIGH": 0.8, "MEDIUM": 0.5, "LOW": 0.3}[level]
        return 0.3

    def _get_source_quality(self, source_id: str) -> float:
        for key, quality in self._source_quality.items():
            if key in source_id.lower():
                return quality
        return 0.5


class NewsPipelineService:
    """Service that runs the news pipeline on events from the bus."""

    def __init__(self, pipeline: NewsPipeline | None = None) -> None:
        self._pipeline = pipeline or NewsPipeline()
        self._running = False
        self._task: asyncio.Task[Any] | None = None

    async def start(self, event_bus: Any) -> None:
        self._running = True
        self._task = asyncio.create_task(self._process_loop(event_bus))

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _process_loop(self, event_bus: Any) -> None:
        async for event in event_bus.subscribe():
            if not self._running:
                break
            if event.event_type == "news":
                # Skip already-processed events (this service re-publishes its
                # own enriched output to the bus; with fan-out it would
                # otherwise receive and re-process its own events forever).
                if event.payload.get("news_processed"):
                    continue
                processed = self._pipeline.process(event)
                # Re-publish processed event
                await event_bus.publish(processed)
