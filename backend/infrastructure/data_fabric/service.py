"""Data Fabric Integration - wires the complete data fabric into ATI."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from ...domain.data_fabric.enums import AssetClass, DataPlane, SourceTier
from ...domain.data_fabric.instrument import InstrumentMaster, create_default_instrument_master
from ...domain.data_fabric.source import AuthType, SourceConfig, SourceRegistry, TransportType
from ...infrastructure.data_fabric.connectors.crypto.binance import BinanceConnector
from ...infrastructure.data_fabric.connectors.crypto.bybit import BybitConnector
from ...infrastructure.data_fabric.connectors.crypto.coinbase import CoinbaseConnector
from ...infrastructure.data_fabric.connectors.crypto.gateio import GateioConnector
from ...infrastructure.data_fabric.connectors.crypto.kraken import KrakenConnector
from ...infrastructure.data_fabric.connectors.crypto.mexc import MexcConnector
from ...infrastructure.data_fabric.connectors.forex.fxcm import FXCMConnector
from ...infrastructure.data_fabric.connectors.forex.oanda import OANDAConnector
from ...infrastructure.data_fabric.connectors.macro.central_banks import (
    CentralBankConnector,
    create_central_bank_configs,
)
from ...infrastructure.data_fabric.connectors.macro.forex_factory import ForexFactoryConnector
from ...infrastructure.data_fabric.connectors.news.gdelt import GDELTConnector
from ...infrastructure.data_fabric.connectors.news.rss_news import RSSNewsConnector
from ...infrastructure.data_fabric.event_bus import EnhancedEventBus
from ...infrastructure.data_fabric.pipeline import NewsPipelineService
from ...infrastructure.data_fabric.quality_monitor import DataQualityService
from ...infrastructure.data_fabric.replay import ReplayManager

logger = logging.getLogger(__name__)


class DataFabricService:
    """Main service that manages the entire ATI Data Fabric."""

    def __init__(
        self,
        db_path: str = "data/trading_intelligence.db",
        bus_maxsize: int = 10000,
    ) -> None:
        self._db_path = db_path
        self._bus_maxsize = bus_maxsize

        # Core components
        self._event_bus = EnhancedEventBus(maxsize=bus_maxsize, db_path=db_path)
        self._instrument_master = create_default_instrument_master()
        self._source_registry = SourceRegistry()
        self._connectors: list[Any] = []
        self._running = False

        # Services
        self._quality_service = DataQualityService(self._event_bus)
        self._news_pipeline = NewsPipelineService()
        self._replay_manager = ReplayManager(self._event_bus)

        # State
        self._tasks: list[asyncio.Task[Any]] = []

    @property
    def event_bus(self) -> EnhancedEventBus:
        return self._event_bus

    @property
    def instrument_master(self) -> InstrumentMaster:
        return self._instrument_master

    @property
    def source_registry(self) -> SourceRegistry:
        return self._source_registry

    @property
    def quality_service(self) -> DataQualityService:
        return self._quality_service

    @property
    def news_pipeline(self) -> NewsPipelineService:
        return self._news_pipeline

    @property
    def replay_manager(self) -> ReplayManager:
        return self._replay_manager

    def register_source(self, config: SourceConfig) -> None:
        """Register a data source configuration."""
        self._source_registry.register(config)

    def create_connector(self, config: SourceConfig) -> Any:
        """Create a connector instance for a source config."""
        venue = config.venue or config.source_id

        if venue == "binance":
            return BinanceConnector(config, self._event_bus, self._instrument_master)
        elif venue == "coinbase":
            return CoinbaseConnector(config, self._event_bus, self._instrument_master)
        elif venue == "kraken":
            return KrakenConnector(config, self._event_bus, self._instrument_master)
        elif venue == "gateio":
            return GateioConnector(config, self._event_bus, self._instrument_master)
        elif venue == "mexc":
            return MexcConnector(config, self._event_bus, self._instrument_master)
        elif venue == "bybit":
            return BybitConnector(config, self._event_bus, self._instrument_master)
        elif venue == "oanda":
            return OANDAConnector(config, self._event_bus, self._instrument_master)
        elif venue == "fxcm":
            return FXCMConnector(config, self._event_bus, self._instrument_master)
        elif venue == "deriv":
            from backend.infrastructure.broker.deriv.connector import DerivConnector

            return DerivConnector(config, self._event_bus, self._instrument_master)
        elif venue == "forex_factory":
            return ForexFactoryConnector(config, self._event_bus, self._instrument_master)
        elif venue in ("fed", "ecb", "bls", "boe"):
            return CentralBankConnector(config, self._event_bus, self._instrument_master)
        elif venue == "gdelt":
            return GDELTConnector(config, self._event_bus, self._instrument_master)
        elif venue in ("cointelegraph", "theblock", "bitcoinmagazine", "bitcoincom", "crypto_rss"):
            return RSSNewsConnector(config, self._event_bus, self._instrument_master)
        else:
            raise ValueError(f"Unknown venue: {venue}")

    async def start(self) -> None:
        """Start the entire data fabric."""
        if self._running:
            return

        logger.info("Starting ATI Data Fabric...")

        # Start quality monitoring
        await self._quality_service.start()

        # Start news pipeline
        await self._news_pipeline.start(self._event_bus)

        # Create and start all registered connectors
        for config in self._source_registry.all_sources():
            if not config.enabled:
                continue
            try:
                connector = self.create_connector(config)
                self._connectors.append(connector)
                await connector.start()
                logger.info("Started connector: %s (%s)", config.source_name, config.venue)
            except Exception as e:
                logger.error("Failed to start connector %s: %s", config.source_name, e)

        # Start health monitoring task
        self._tasks.append(asyncio.create_task(self._health_monitor()))

        self._running = True
        logger.info("ATI Data Fabric started with %d connectors", len(self._connectors))

    async def stop(self) -> None:
        """Stop the entire data fabric."""
        if not self._running:
            return

        logger.info("Stopping ATI Data Fabric...")

        self._running = False

        # Cancel monitoring tasks
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Stop all connectors
        for connector in self._connectors:
            try:
                await connector.stop()
            except Exception as e:
                logger.warning("Error stopping connector: %s", e)

        # Stop services
        await self._quality_service.stop()
        await self._news_pipeline.stop()

        logger.info("ATI Data Fabric stopped")

    async def _health_monitor(self) -> None:
        """Periodic health monitoring and reporting."""
        while self._running:
            await asyncio.sleep(60)
            if not self._running:
                break

            try:
                # Report health to event bus stats
                stats = self._event_bus.get_stats()
                logger.debug("Data Fabric health: %s", stats)

                # Record events for quality monitoring
                for _health in self._event_bus.get_all_health():
                    pass  # Quality monitoring is automatic via event bus

            except Exception as e:
                logger.warning("Health monitor error: %s", e)

    def get_connector(self, source_id: str) -> Any | None:
        """Get a connector by source ID."""
        for conn in self._connectors:
            if conn.config.source_id == source_id:
                return conn
        return None

    def get_all_connectors(self) -> list[Any]:
        return list(self._connectors)

    def get_status(self) -> dict[str, Any]:
        """Get comprehensive fabric status."""
        return {
            "running": self._running,
            "connectors": [
                {
                    "source_id": c.config.source_id,
                    "source_name": c.config.source_name,
                    "venue": c.config.venue,
                    "running": c.is_running,
                    "health": c.get_health().to_dict(),
                }
                for c in self._connectors
            ],
            "event_bus": self._event_bus.get_stats(),
            "quality": self._quality_service.get_all_metrics(),
            "instruments": len(self._instrument_master),
            "sources_registered": len(self._source_registry),
        }


def build_data_fabric_from_env(
    db_path: str = "data/trading_intelligence.db",
    bus_maxsize: int = 10000,
) -> DataFabricService:
    """Build a DataFabricService with standard sources from environment.

    Reads configuration from environment variables:
    - OANDA_API_TOKEN, OANDA_ACCOUNT_ID
    - FXCM_API_TOKEN
    - CCXT_ENABLED (for crypto)
    """
    import os

    fabric = DataFabricService(db_path=db_path, bus_maxsize=bus_maxsize)

    # === CRYPTO CONNECTORS ===
    if os.getenv("CCXT_ENABLED", "false").lower() == "true":
        crypto_symbols = os.getenv(
            "CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT"
        ).split(",")
        crypto_channels = os.getenv("CRYPTO_CHANNELS", "trade,ticker,book,candle").split(",")

        # Gate.io (primary — verified reachable, full depth + trades + tickers)
        fabric.register_source(
            SourceConfig(
                source_id="gateio",
                source_name="Gate.io",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                venue="gateio",
                transport=TransportType.WEBSOCKET,
                auth_type=AuthType.NONE,
                ws_url="wss://api.gateio.ws/ws/v4/",
                symbols=tuple(crypto_symbols),
                channels=tuple(crypto_channels),
                source_tier=SourceTier.TIER_2,
                priority=1,
            )
        )

        # MEXC (secondary — reachable, subscription may be geo-blocked)
        fabric.register_source(
            SourceConfig(
                source_id="mexc",
                source_name="MEXC",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                venue="mexc",
                transport=TransportType.WEBSOCKET,
                auth_type=AuthType.NONE,
                ws_url="wss://wbs.mexc.com/ws",
                symbols=tuple(crypto_symbols),
                channels=tuple(crypto_channels),
                source_tier=SourceTier.TIER_2,
                priority=5,
            )
        )

        # Binance
        fabric.register_source(
            SourceConfig(
                source_id="binance",
                source_name="Binance",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                venue="binance",
                transport=TransportType.WEBSOCKET,
                auth_type=AuthType.NONE,
                ws_url="wss://stream.binance.com:9443/stream",
                symbols=tuple(crypto_symbols),
                channels=tuple(crypto_channels),
                source_tier=SourceTier.TIER_2,
                priority=10,
            )
        )

        # Coinbase
        fabric.register_source(
            SourceConfig(
                source_id="coinbase",
                source_name="Coinbase Advanced Trade",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                venue="coinbase",
                transport=TransportType.WEBSOCKET,
                auth_type=AuthType.NONE,
                ws_url="wss://advanced-trade-ws.coinbase.com",
                symbols=tuple(crypto_symbols),
                channels=tuple(crypto_channels),
                source_tier=SourceTier.TIER_2,
                priority=10,
            )
        )

        # Kraken
        fabric.register_source(
            SourceConfig(
                source_id="kraken",
                source_name="Kraken",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                venue="kraken",
                transport=TransportType.WEBSOCKET,
                auth_type=AuthType.NONE,
                ws_url="wss://ws.kraken.com/v2",
                symbols=tuple(crypto_symbols),
                channels=tuple(crypto_channels),
                source_tier=SourceTier.TIER_2,
                priority=10,
            )
        )

        # Bybit
        fabric.register_source(
            SourceConfig(
                source_id="bybit",
                source_name="Bybit",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.CRYPTO,
                venue="bybit",
                transport=TransportType.WEBSOCKET,
                auth_type=AuthType.NONE,
                ws_url="wss://stream.bybit.com/v5/public/spot",
                symbols=tuple(crypto_symbols),
                channels=tuple(crypto_channels),
                source_tier=SourceTier.TIER_2,
                priority=10,
            )
        )

    # === FOREX CONNECTORS ===
    if os.getenv("OANDA_API_TOKEN") and os.getenv("OANDA_ACCOUNT_ID"):
        forex_symbols = os.getenv(
            "FOREX_SYMBOLS",
            "EUR_USD,GBP_USD,USD_JPY,USD_CHF,AUD_USD,USD_CAD,NZD_USD,EUR_GBP,EUR_JPY,GBP_JPY",
        ).split(",")
        fabric.register_source(
            SourceConfig(
                source_id="oanda",
                source_name="OANDA Practice",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.FOREX,
                venue="oanda",
                transport=TransportType.REST_POLL,  # Uses streaming via REST
                auth_type=AuthType.BEARER_TOKEN,
                auth_env_vars={"api_token": "OANDA_API_TOKEN", "account_id": "OANDA_ACCOUNT_ID"},
                base_url="https://stream-fxpractice.oanda.com/v3",
                symbols=tuple(forex_symbols),
                source_tier=SourceTier.TIER_1,
                priority=5,
            )
        )

    if os.getenv("FXCM_API_TOKEN"):
        forex_symbols = os.getenv(
            "FOREX_SYMBOLS",
            "EUR/USD,GBP/USD,USD/JPY,USD/CHF,AUD/USD,USD/CAD,NZD/USD,EUR/GBP,EUR/JPY,GBP/JPY",
        ).split(",")
        fabric.register_source(
            SourceConfig(
                source_id="fxcm",
                source_name="FXCM Demo",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.FOREX,
                venue="fxcm",
                transport=TransportType.REST_POLL,
                auth_type=AuthType.BEARER_TOKEN,
                auth_env_vars={"api_token": "FXCM_API_TOKEN"},
                base_url="https://api-demo.fxcm.com/trading-api/v1",
                symbols=tuple(forex_symbols),
                source_tier=SourceTier.TIER_2,
                priority=10,
            )
        )

    # === DERIV CONNECTOR (Nigeria-supported, free demo via API) ===
    if os.getenv("DERIV_API_TOKEN") or os.getenv("DERIV_APP_ID"):
        deriv_symbols = os.getenv(
            "DERIV_SYMBOLS",
            "frxEURUSD,frxGBPUSD,frxUSDJPY,frxAUDUSD,frxUSDCAD,frxNZDUSD,frxEURGBP,frxEURJPY,frxGBPJPY,R_10,R_25,R_50,R_75,R_100,cryBTCUSD,cryETHUSD",
        ).split(",")
        deriv_channels = os.getenv("DERIV_CHANNELS", "ticks,candles").split(",")

        fabric.register_source(
            SourceConfig(
                source_id="deriv",
                source_name="Deriv",
                data_plane=DataPlane.MARKET,
                asset_class=AssetClass.FOREX,
                venue="deriv",
                transport=TransportType.WEBSOCKET,
                auth_type=AuthType.BEARER_TOKEN if os.getenv("DERIV_API_TOKEN") else AuthType.NONE,
                auth_env_vars={"api_token": "DERIV_API_TOKEN"}
                if os.getenv("DERIV_API_TOKEN")
                else {},
                ws_url="wss://ws.derivws.com/websockets/v3?app_id=1089",
                symbols=tuple(deriv_symbols),
                channels=tuple(deriv_channels),
                source_tier=SourceTier.TIER_2,
                priority=10,
            )
        )

    # === MACRO / CALENDAR ===
    fabric.register_source(
        SourceConfig(
            source_id="forex_factory",
            source_name="Forex Factory Calendar",
            data_plane=DataPlane.MACRO,
            asset_class=AssetClass.RATES,
            venue="forex_factory",
            transport=TransportType.RSS,
            auth_type=AuthType.NONE,
            base_url="https://www.forexfactory.com/calendar",
            source_tier=SourceTier.TIER_2,
            priority=15,
        )
    )

    # Central Banks
    for cb_config in create_central_bank_configs():
        fabric.register_source(cb_config)

    # === NEWS / INTELLIGENCE ===
    fabric.register_source(
        SourceConfig(
            source_id="gdelt",
            source_name="GDELT Global Knowledge Graph",
            data_plane=DataPlane.INTELLIGENCE,
            asset_class=AssetClass.ALTERNATIVE,
            venue="gdelt",
            transport=TransportType.REST_POLL,
            auth_type=AuthType.NONE,
            base_url="http://data.gdeltproject.org/gdeltv2",
            source_tier=SourceTier.TIER_3,
            priority=20,
        )
    )

    # Crypto RSS News
    fabric.register_source(
        SourceConfig(
            source_id="crypto_rss",
            source_name="Crypto RSS Aggregator",
            data_plane=DataPlane.INTELLIGENCE,
            asset_class=AssetClass.CRYPTO,
            venue="crypto_rss",
            transport=TransportType.RSS,
            auth_type=AuthType.NONE,
            base_url="",
            source_tier=SourceTier.TIER_3,
            priority=20,
            metadata={
                "feeds": {
                    "cointelegraph": {
                        "name": "Cointelegraph",
                        "feeds": {
                            "general": "https://cointelegraph.com/rss",
                            "bitcoin": "https://cointelegraph.com/rss/tag/bitcoin",
                            "ethereum": "https://cointelegraph.com/rss/tag/ethereum",
                            "regulation": "https://cointelegraph.com/rss/tag/regulation",
                        },
                    },
                    "theblock": {
                        "name": "The Block",
                        "feeds": {"news": "https://www.theblock.co/rss.xml"},
                    },
                    "bitcoinmagazine": {
                        "name": "Bitcoin Magazine",
                        "feeds": {"general": "https://bitcoinmagazine.com/.rss/full/"},
                    },
                    "bitcoincom": {
                        "name": "Bitcoin.com News",
                        "feeds": {"general": "https://news.bitcoin.com/feed/"},
                    },
                }
            },
        )
    )

    return fabric


async def run_data_fabric_standalone(
    db_path: str = "data/trading_intelligence.db",
    bus_maxsize: int = 10000,
) -> None:
    """Run the data fabric as a standalone service."""
    fabric = build_data_fabric_from_env(db_path=db_path, bus_maxsize=bus_maxsize)
    await fabric.start()

    logger.info("Data Fabric running. Press Ctrl+C to stop.")
    try:
        while True:
            await asyncio.sleep(10)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        await fabric.stop()
