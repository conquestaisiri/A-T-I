"""Source configuration and registry for the Data Fabric."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .enums import AssetClass, DataPlane, SourceTier


class TransportType(enum.StrEnum):
    """Transport mechanism for the source."""

    WEBSOCKET = "websocket"
    REST_POLL = "rest_poll"
    RSS = "rss"
    SSE = "sse"
    GRPC = "grpc"
    TCP = "tcp"
    UDP = "udp"


class AuthType(enum.StrEnum):
    """Authentication type required by the source."""

    NONE = "none"
    API_KEY = "api_key"
    API_KEY_SECRET = "api_key_secret"
    BEARER_TOKEN = "bearer_token"
    OAUTH2 = "oauth2"
    CERTIFICATE = "certificate"
    BASIC_AUTH = "basic_auth"


@dataclass(frozen=True, slots=True)
class SourceConfig:
    """Immutable configuration for a data source.

    This is the contract that defines how ATI connects to and consumes
    a specific external data provider.
    """

    source_id: str
    source_name: str
    data_plane: DataPlane
    asset_class: AssetClass
    venue: str | None = None

    transport: TransportType = TransportType.WEBSOCKET
    auth_type: AuthType = AuthType.NONE
    auth_env_vars: dict[str, str] = field(default_factory=dict)

    base_url: str = ""
    ws_url: str | None = None
    symbols: tuple[str, ...] = ()
    channels: tuple[str, ...] = ()
    rate_limit_per_minute: int | None = None

    source_tier: SourceTier = SourceTier.TIER_3
    timeout_seconds: float = 30.0
    max_reconnect_attempts: int = 10
    reconnect_base_delay_seconds: float = 1.0
    reconnect_max_delay_seconds: float = 60.0
    heartbeat_interval_seconds: float = 30.0
    stale_after_seconds: float = 60.0

    enabled: bool = True
    priority: int = 100
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id:
            object.__setattr__(self, "source_id", str(uuid4())[:8])
        if not self.source_name:
            object.__setattr__(self, "source_name", self.source_id)

    def get_auth_value(self, env_var: str) -> str | None:
        """Get authentication value from environment variable."""
        import os

        return os.getenv(env_var)

    def get_all_auth(self) -> dict[str, str]:
        """Get all configured auth values from environment."""
        import os

        result = {}
        for key, env_var in self.auth_env_vars.items():
            value = os.getenv(env_var)
            if value:
                result[key] = value
        return result


class SourceRegistry:
    """Registry of all configured data sources.

    Provides lookup by source_id, venue, asset_class, data_plane.
    Thread-safe for read operations.
    """

    def __init__(self) -> None:
        self._sources: dict[str, SourceConfig] = {}
        self._by_venue: dict[str, list[SourceConfig]] = {}
        self._by_asset_class: dict[AssetClass, list[SourceConfig]] = {}
        self._by_data_plane: dict[DataPlane, list[SourceConfig]] = {}

    def register(self, config: SourceConfig) -> None:
        """Register a source configuration."""
        if config.source_id in self._sources:
            raise ValueError(f"Source already registered: {config.source_id}")
        self._sources[config.source_id] = config

        if config.venue:
            self._by_venue.setdefault(config.venue, []).append(config)
        self._by_asset_class.setdefault(config.asset_class, []).append(config)
        self._by_data_plane.setdefault(config.data_plane, []).append(config)

    def unregister(self, source_id: str) -> None:
        """Unregister a source."""
        config = self._sources.pop(source_id, None)
        if config is None:
            return
        for venue_list in self._by_venue.values():
            if config in venue_list:
                venue_list.remove(config)
        for asset_list in self._by_asset_class.values():
            if config in asset_list:
                asset_list.remove(config)
        for plane_list in self._by_data_plane.values():
            if config in plane_list:
                plane_list.remove(config)

    def get(self, source_id: str) -> SourceConfig | None:
        """Get source by ID."""
        return self._sources.get(source_id)

    def get_by_venue(self, venue: str) -> list[SourceConfig]:
        """Get all sources for a venue."""
        return list(self._by_venue.get(venue, []))

    def get_by_asset_class(self, asset_class: AssetClass) -> list[SourceConfig]:
        """Get all sources for an asset class."""
        return list(self._by_asset_class.get(asset_class, []))

    def get_by_data_plane(self, data_plane: DataPlane) -> list[SourceConfig]:
        """Get all sources for a data plane."""
        return list(self._by_data_plane.get(data_plane, []))

    def get_enabled(self, data_plane: DataPlane | None = None) -> list[SourceConfig]:
        """Get all enabled sources, optionally filtered by data plane."""
        sources: list[SourceConfig] = list(self._sources.values())
        if data_plane is not None:
            sources = [s for s in sources if s.data_plane == data_plane]
        return [s for s in sources if s.enabled]

    def all_sources(self) -> list[SourceConfig]:
        """Get all registered sources."""
        return list(self._sources.values())

    def __len__(self) -> int:
        return len(self._sources)

    def __contains__(self, source_id: str) -> bool:
        return source_id in self._sources
