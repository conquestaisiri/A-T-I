# Shared, venue-agnostic configuration for CCXT adapters.
#
# Both ``CcxtObservationAdapter`` (observation layer) and ``CcxtOrderGateway``
# (execution layer) depend on this config. It lives in its own module so
# neither layer owns it and neither imports across the observation/execution
# boundary (ADR 0012, Integration Constitution §102-108).

from __future__ import annotations


class CcxtVenueConfig:
    """Immutable configuration for a CCXT-backed venue adapter.

    Attributes
    ----------
    venue_id:
        CCXT exchange id, e.g. ``"binance"``, ``"bybit"``, ``"okx"``.
    api_key:
        Venue API key. ``None`` limits the adapter to public data.
    secret:
        Venue API secret. ``None`` limits the adapter to public data.
    sandbox:
        Enable the venue's testnet / sandbox endpoints.
    rate_limit_buffer:
        Fraction of the venue's rate limit the adapter actually uses
        (0, 1]. Lower is safer; 0.8 leaves 20% headroom.
    default_symbol:
        Venue-specific symbol, e.g. ``"BTC/USDT"``.
    enable_websocket:
        Use CCXT ``watch_*`` streams when ``True``; fall back to REST polling
        otherwise.
    market_type:
        CCXT market universe for this venue: ``"spot"``, ``"swap"``
        (perpetual futures, e.g. Binance USDT-M via ``binanceusdm``),
        ``"future"`` (dated linear futures), or ``"delivery"`` (inverse
        futures). Drives the exchange ``defaultType`` option so the right
        market set is loaded.
    """

    __slots__ = (
        "venue_id",
        "api_key",
        "secret",
        "sandbox",
        "rate_limit_buffer",
        "default_symbol",
        "enable_websocket",
        "market_type",
    )

    _ALLOWED_MARKET_TYPES = frozenset({"spot", "swap", "future", "delivery"})

    def __init__(
        self,
        *,
        venue_id: str = "binance",
        api_key: str | None = None,
        secret: str | None = None,
        sandbox: bool = True,
        rate_limit_buffer: float = 0.8,
        default_symbol: str = "BTC/USDT",
        enable_websocket: bool = False,
        market_type: str = "spot",
    ) -> None:
        if not venue_id:
            raise ValueError("venue_id must be a non-empty string")
        if not 0.0 < rate_limit_buffer <= 1.0:
            raise ValueError("rate_limit_buffer must be in (0, 1]")
        if not default_symbol:
            raise ValueError("default_symbol must be a non-empty string")
        if market_type not in self._ALLOWED_MARKET_TYPES:
            allowed = ", ".join(sorted(self._ALLOWED_MARKET_TYPES))
            raise ValueError(f"market_type must be one of {allowed}, got {market_type!r}")
        self.venue_id = venue_id
        self.api_key = api_key
        self.secret = secret
        self.sandbox = sandbox
        self.rate_limit_buffer = rate_limit_buffer
        self.default_symbol = default_symbol
        self.enable_websocket = enable_websocket
        self.market_type = market_type
