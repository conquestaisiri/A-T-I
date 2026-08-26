"""Instrument master - canonical instrument identity across venues.

Solves the symbol mapping problem: BTCUSDT (Binance), BTC-USD (Coinbase),
BTC/USD (Kraken), XBT/USD (Kraken futures) all represent the same underlying
BTC/USD spot instrument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import AssetClass


@dataclass(frozen=True, slots=True)
class Instrument:
    """Canonical instrument identity.

    One Instrument = one tradable asset with a canonical identity.
    Multiple venue-specific symbols map to the same Instrument.
    """

    instrument_id: str
    canonical_symbol: str
    base_asset: str
    quote_asset: str
    asset_class: AssetClass
    contract_type: str = "spot"  # spot | future | swap | option
    tick_size: float | None = None
    lot_size: float | None = None
    quote_precision: int = 8
    status: str = "active"  # active | delisted | suspended
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.instrument_id:
            raise ValueError("instrument_id is required")
        if not self.base_asset or not self.quote_asset:
            raise ValueError("base_asset and quote_asset are required")


class InstrumentMaster:
    """Central registry of all instruments and their venue mappings.

    Provides:
    - Canonical instrument lookup
    - Venue symbol -> instrument mapping
    - Instrument -> venue symbols mapping
    - Cross-venue consensus price calculation support
    """

    def __init__(self) -> None:
        self._instruments: dict[str, Instrument] = {}
        self._venue_symbol_to_instrument: dict[tuple[str, str], Instrument] = {}
        self._instrument_to_venue_symbols: dict[str, dict[str, str]] = {}

    def register(self, instrument: Instrument, venue_symbols: dict[str, str] | None = None) -> None:
        """Register an instrument with its venue-specific symbols.

        Args:
            instrument: The canonical instrument
            venue_symbols: Mapping of venue -> venue-specific symbol
        """
        if instrument.instrument_id in self._instruments:
            raise ValueError(f"Instrument already registered: {instrument.instrument_id}")

        self._instruments[instrument.instrument_id] = instrument
        self._instrument_to_venue_symbols[instrument.instrument_id] = venue_symbols or {}

        if venue_symbols:
            for venue, venue_symbol in venue_symbols.items():
                self._venue_symbol_to_instrument[(venue, venue_symbol)] = instrument

    def get_instrument(self, instrument_id: str) -> Instrument | None:
        """Get instrument by canonical ID."""
        return self._instruments.get(instrument_id)

    def get_by_venue_symbol(self, venue: str, venue_symbol: str) -> Instrument | None:
        """Resolve venue-specific symbol to canonical instrument."""
        return self._venue_symbol_to_instrument.get((venue, venue_symbol))

    def get_venue_symbols(self, instrument_id: str) -> dict[str, str]:
        """Get all venue-specific symbols for an instrument."""
        return dict(self._instrument_to_venue_symbols.get(instrument_id, {}))

    def get_venue_symbol(self, instrument_id: str, venue: str) -> str | None:
        """Get the venue-specific symbol for a given venue."""
        return self._instrument_to_venue_symbols.get(instrument_id, {}).get(venue)

    def find_instruments(
        self,
        *,
        asset_class: AssetClass | None = None,
        base_asset: str | None = None,
        quote_asset: str | None = None,
        status: str = "active",
    ) -> list[Instrument]:
        """Find instruments matching criteria."""
        results = []
        for inst in self._instruments.values():
            if inst.status != status:
                continue
            if asset_class and inst.asset_class != asset_class:
                continue
            if base_asset and inst.base_asset != base_asset:
                continue
            if quote_asset and inst.quote_asset != quote_asset:
                continue
            results.append(inst)
        return results

    def all_instruments(self) -> list[Instrument]:
        """Get all registered instruments."""
        return list(self._instruments.values())

    def __len__(self) -> int:
        return len(self._instruments)

    def __contains__(self, instrument_id: str) -> bool:
        return instrument_id in self._instruments


# Default crypto instrument definitions
DEFAULT_CRYPTO_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "BTC_USD_SPOT": {
        "instrument_id": "BTC_USD_SPOT",
        "canonical_symbol": "BTC/USD",
        "base_asset": "BTC",
        "quote_asset": "USD",
        "asset_class": AssetClass.CRYPTO,
        "contract_type": "spot",
        "tick_size": 0.01,
        "lot_size": 0.00001,
        "venue_symbols": {
            "binance": "BTCUSDT",
            "binance_us": "BTCUSDT",
            "coinbase": "BTC-USD",
            "coinbase_advanced": "BTC-USD",
            "kraken": "BTC/USD",
            "bybit": "BTCUSDT",
            "okx": "BTC-USDT",
            "deribit": "BTC-PERPETUAL",
        },
    },
    "ETH_USD_SPOT": {
        "instrument_id": "ETH_USD_SPOT",
        "canonical_symbol": "ETH/USD",
        "base_asset": "ETH",
        "quote_asset": "USD",
        "asset_class": AssetClass.CRYPTO,
        "contract_type": "spot",
        "tick_size": 0.01,
        "lot_size": 0.0001,
        "venue_symbols": {
            "binance": "ETHUSDT",
            "coinbase": "ETH-USD",
            "kraken": "ETH/USD",
            "bybit": "ETHUSDT",
        },
    },
    "SOL_USD_SPOT": {
        "instrument_id": "SOL_USD_SPOT",
        "canonical_symbol": "SOL/USD",
        "base_asset": "SOL",
        "quote_asset": "USD",
        "asset_class": AssetClass.CRYPTO,
        "contract_type": "spot",
        "tick_size": 0.001,
        "lot_size": 0.01,
        "venue_symbols": {
            "binance": "SOLUSDT",
            "coinbase": "SOL-USD",
            "kraken": "SOL/USD",
            "bybit": "SOLUSDT",
        },
    },
    "BNB_USD_SPOT": {
        "instrument_id": "BNB_USD_SPOT",
        "canonical_symbol": "BNB/USD",
        "base_asset": "BNB",
        "quote_asset": "USD",
        "asset_class": AssetClass.CRYPTO,
        "contract_type": "spot",
        "tick_size": 0.001,
        "lot_size": 0.01,
        "venue_symbols": {
            "binance": "BNBUSDT",
            "coinbase": "BNB-USD",
            "bybit": "BNBUSDT",
        },
    },
    "XRP_USD_SPOT": {
        "instrument_id": "XRP_USD_SPOT",
        "canonical_symbol": "XRP/USD",
        "base_asset": "XRP",
        "quote_asset": "USD",
        "asset_class": AssetClass.CRYPTO,
        "contract_type": "spot",
        "tick_size": 0.0001,
        "lot_size": 1.0,
        "venue_symbols": {
            "binance": "XRPUSDT",
            "coinbase": "XRP-USD",
            "kraken": "XRP/USD",
            "bybit": "XRPUSDT",
        },
    },
}

DEFAULT_FOREX_INSTRUMENTS: dict[str, dict[str, Any]] = {
    "EUR_USD": {
        "instrument_id": "EUR_USD",
        "canonical_symbol": "EUR/USD",
        "base_asset": "EUR",
        "quote_asset": "USD",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.00001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "EUR_USD",
            "fxcm": "EUR/USD",
            "dukascopy": "EURUSD",
        },
    },
    "GBP_USD": {
        "instrument_id": "GBP_USD",
        "canonical_symbol": "GBP/USD",
        "base_asset": "GBP",
        "quote_asset": "USD",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.00001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "GBP_USD",
            "fxcm": "GBP/USD",
            "dukascopy": "GBPUSD",
        },
    },
    "USD_JPY": {
        "instrument_id": "USD_JPY",
        "canonical_symbol": "USD/JPY",
        "base_asset": "USD",
        "quote_asset": "JPY",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "USD_JPY",
            "fxcm": "USD/JPY",
            "dukascopy": "USDJPY",
        },
    },
    "USD_CHF": {
        "instrument_id": "USD_CHF",
        "canonical_symbol": "USD/CHF",
        "base_asset": "USD",
        "quote_asset": "CHF",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.00001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "USD_CHF",
            "fxcm": "USD/CHF",
            "dukascopy": "USDCHF",
        },
    },
    "AUD_USD": {
        "instrument_id": "AUD_USD",
        "canonical_symbol": "AUD/USD",
        "base_asset": "AUD",
        "quote_asset": "USD",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.00001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "AUD_USD",
            "fxcm": "AUD/USD",
            "dukascopy": "AUDUSD",
        },
    },
    "USD_CAD": {
        "instrument_id": "USD_CAD",
        "canonical_symbol": "USD/CAD",
        "base_asset": "USD",
        "quote_asset": "CAD",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.00001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "USD_CAD",
            "fxcm": "USD/CAD",
            "dukascopy": "USDCAD",
        },
    },
    "NZD_USD": {
        "instrument_id": "NZD_USD",
        "canonical_symbol": "NZD/USD",
        "base_asset": "NZD",
        "quote_asset": "USD",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.00001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "NZD_USD",
            "fxcm": "NZD/USD",
            "dukascopy": "NZDUSD",
        },
    },
    "EUR_GBP": {
        "instrument_id": "EUR_GBP",
        "canonical_symbol": "EUR/GBP",
        "base_asset": "EUR",
        "quote_asset": "GBP",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.00001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "EUR_GBP",
            "fxcm": "EUR/GBP",
            "dukascopy": "EURGBP",
        },
    },
    "EUR_JPY": {
        "instrument_id": "EUR_JPY",
        "canonical_symbol": "EUR/JPY",
        "base_asset": "EUR",
        "quote_asset": "JPY",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "EUR_JPY",
            "fxcm": "EUR/JPY",
            "dukascopy": "EURJPY",
        },
    },
    "GBP_JPY": {
        "instrument_id": "GBP_JPY",
        "canonical_symbol": "GBP/JPY",
        "base_asset": "GBP",
        "quote_asset": "JPY",
        "asset_class": AssetClass.FOREX,
        "contract_type": "spot",
        "tick_size": 0.001,
        "lot_size": 1000.0,
        "venue_symbols": {
            "oanda": "GBP_JPY",
            "fxcm": "GBP/JPY",
            "dukascopy": "GBPJPY",
        },
    },
}


def create_default_instrument_master() -> InstrumentMaster:
    """Create an InstrumentMaster pre-populated with standard crypto + forex."""
    master = InstrumentMaster()

    for data in DEFAULT_CRYPTO_INSTRUMENTS.values():
        venue_symbols = data.pop("venue_symbols", {})
        instrument = Instrument(**data)
        master.register(instrument, venue_symbols)

    for data in DEFAULT_FOREX_INSTRUMENTS.values():
        venue_symbols = data.pop("venue_symbols", {})
        instrument = Instrument(**data)
        master.register(instrument, venue_symbols)

    return master
