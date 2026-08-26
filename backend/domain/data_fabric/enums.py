"""Enumerations for the Data Fabric."""

from __future__ import annotations

import enum


class AssetClass(enum.StrEnum):
    """Asset class classification."""

    CRYPTO = "crypto"
    FOREX = "forex"
    EQUITY = "equity"
    COMMODITY = "commodity"
    RATES = "rates"
    INDEX = "index"
    DERIVATIVE = "derivative"
    ALTERNATIVE = "alternative"
    UNKNOWN = "unknown"


class DataPlane(enum.StrEnum):
    """The four major data planes in ATI."""

    MARKET = "market"  # Market data: trades, quotes, order books, candles
    INTELLIGENCE = "intelligence"  # News, events, sentiment, entities
    MACRO = "macro"  # Economic releases, central bank, calendar
    ALTERNATIVE = "alternative"  # On-chain, COT, positioning, correlations


class SourceTier(enum.StrEnum):
    """Source quality hierarchy.

    TIER_1: Official government / central bank / exchange official
    TIER_2: Established financial publication
    TIER_3: Established specialist publication
    TIER_4: Aggregators
    TIER_5: Social media / unknown source
    """

    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"
    TIER_4 = "tier_4"
    TIER_5 = "tier_5"


class ConnectionState(enum.StrEnum):
    """Connection lifecycle states."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    RECONNECTING = "reconnecting"
    DEGRADED = "degraded"  # Connected but issues (latency, gaps)
    STALE = "stale"  # Connected but no fresh data
    ERROR = "error"  # Error state, needs intervention


class FreshnessState(enum.StrEnum):
    """Data freshness classification."""

    LIVE = "live"  # Fresh data flowing
    DEGRADED = "degraded"  # Some latency/gaps but usable
    STALE = "stale"  # No fresh data within threshold
    DISCONNECTED = "disconnected"  # Source offline
    UNKNOWN = "unknown"  # Not yet determined
