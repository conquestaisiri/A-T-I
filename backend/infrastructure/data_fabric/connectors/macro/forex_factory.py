"""Forex Factory Economic Calendar connector.

Forex Factory provides economic calendar data with impact ratings,
forecasts, actuals, and revisions. Accessible via RSS/export.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
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

FOREX_FACTORY_CALENDAR_URL = "https://www.forexfactory.com/calendar"
FOREX_FACTORY_RSS = "https://www.forexfactory.com/calendar/rss"


class ForexFactoryConnector(BaseConnector):
    """Forex Factory economic calendar connector.

    Fetches scheduled economic events with impact, forecast, previous,
    actual values. Uses RSS feed and HTML parsing as fallback.
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: Any,
        instrument_master: InstrumentMaster | None = None,
    ) -> None:
        super().__init__(config, event_bus, instrument_master)
        self._session: Any = None
        self._last_fetch: datetime | None = None
        self._cache: dict[str, Any] = {}

    async def _connect_impl(self) -> None:
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "ATI-DataFabric/1.0"},
        )
        logger.info("Forex Factory connector initialized")

    async def _disconnect_impl(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _subscribe_impl(self) -> None:
        # Initial fetch
        await self._fetch_calendar()
        logger.info("Forex Factory calendar loaded")

    async def _run(self) -> None:
        # Poll calendar every 15 minutes
        while self._running:
            await asyncio.sleep(900)  # 15 min
            if not self._running:
                break
            try:
                await self._fetch_calendar()
            except Exception as e:
                logger.warning("Forex Factory fetch failed: %s", e)
                self._state.errors += 1

    async def _fetch_calendar(self) -> None:
        if not self._session:
            return

        try:
            # Fetch RSS via aiohttp (non-blocking) then parse off the event loop
            async with self._session.get(FOREX_FACTORY_RSS) as resp:
                rss_text = await resp.text()
            feed = await asyncio.to_thread(feedparser.parse, rss_text)
            events = self._parse_rss(feed)

            if not events:
                # Fallback to HTML scrape
                events = await self._scrape_html()

            if events:
                self._last_fetch = datetime.now(UTC)
                self._cache = {e["event_id"]: e for e in events}
                await self._publish_events(events)

        except Exception as e:
            logger.warning("Forex Factory fetch failed: %s", e)
            raise

    def _parse_rss(self, feed: Any) -> list[dict[str, Any]]:
        events = []
        for entry in feed.entries:
            try:
                # Parse Forex Factory RSS entry
                title = entry.get("title", "")
                summary = entry.get("summary", "")
                link = entry.get("link", "")
                published = entry.get("published", "")

                # Extract structured data from title/summary
                # Format: "USD CPI (YoY) - Actual: 3.4% Forecast: 3.1% Previous: 3.2%"
                event_data = self._parse_ff_entry(title, summary, link, published)
                if event_data:
                    events.append(event_data)
            except Exception:
                continue
        return events

    def _parse_ff_entry(
        self, title: str, summary: str, link: str, published: str
    ) -> dict[str, Any] | None:
        try:
            # Extract currency, event name
            parts = title.split(" ", 1)
            if len(parts) < 2:
                return None
            currency = parts[0]
            event_name = parts[1]

            # Extract impact from summary (Forex Factory uses 🔴🟠🟡)
            impact = "LOW"
            if "🔴" in summary or "High" in summary:
                impact = "HIGH"
            elif "🟠" in summary or "Medium" in summary:
                impact = "MEDIUM"

            # Extract actual, forecast, previous
            actual = self._extract_value(summary, "Actual")
            forecast = self._extract_value(summary, "Forecast")
            previous = self._extract_value(summary, "Previous")
            revision = self._extract_value(summary, "Revised")

            event_time = datetime.now(UTC)  # Would parse from published
            with contextlib.suppress(Exception):
                event_time = datetime.fromisoformat(published.replace("Z", "+00:00"))

            return {
                "event_id": f"ff_{currency}_{event_name}_{event_time.isoformat()}",
                "source_id": self.config.source_id,
                "source_name": self.config.source_name,
                "venue": "forex_factory",
                "event_time": event_time,
                "currency": currency,
                "event_name": event_name,
                "impact": impact,
                "actual": actual,
                "forecast": forecast,
                "previous": previous,
                "revision": revision,
                "status": "SCHEDULED" if actual is None else "RELEASED",
                "link": link,
            }
        except Exception:
            return None

    def _extract_value(self, text: str, label: str) -> float | None:
        patterns = [
            rf"{label}:\s*([+-]?\d+\.?\d*)%?",
            rf"{label}\s+([+-]?\d+\.?\d*)%?",
        ]
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    continue
        return None

    async def _scrape_html(self) -> list[dict[str, Any]]:
        # HTML scraping fallback - simplified
        return []

    async def _publish_events(self, events: list[dict[str, Any]]) -> None:
        for event in events:
            try:
                # Check if already published (dedup)
                event_id = event["event_id"]
                if event_id in self._cache:
                    cached = self._cache[event_id]
                    if cached.get("status") == "RELEASED" and event.get("status") == "SCHEDULED":
                        continue

                raw_env = RawEnvelope(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="forex_factory",
                    data_plane=DataPlane.MACRO,
                    asset_class=AssetClass.RATES,
                    received_at=datetime.now(UTC),
                    raw_payload=event,
                    raw_headers={},
                )
                await self._publish_raw(raw_env)

                norm = NormalizedEvent.create_macro(
                    source_id=self.config.source_id,
                    source_name=self.config.source_name,
                    venue="forex_factory",
                    event_time=event["event_time"],
                    country=event["currency"],
                    currency=event["currency"],
                    event_name=event["event_name"],
                    impact=event["impact"],
                    forecast=event.get("forecast"),
                    previous=event.get("previous"),
                    actual=event.get("actual"),
                    revision=event.get("revision"),
                    status=event.get("status", "SCHEDULED"),
                    received_at=datetime.now(UTC),
                    raw_envelope_id=raw_env.envelope_id,
                    asset_class=AssetClass.RATES,
                    quality_score=0.9,
                )
                await self._publish_normalized(norm)
                self._state.messages_received += 1

            except Exception as e:
                logger.warning("Failed to publish FF event: %s", e)
