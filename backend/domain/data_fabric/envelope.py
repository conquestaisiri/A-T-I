"""Data fabric envelopes: raw preservation + normalized events."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from .enums import AssetClass, DataPlane


@dataclass(frozen=True, slots=True)
class RawEnvelope:
    """Raw provider message preserved exactly as received.

    This is the immutable record of what the external source sent us.
    Never modify, never normalize - preserve for replay and audit.
    """

    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_id: str = ""
    source_name: str = ""
    venue: str | None = None
    data_plane: DataPlane = DataPlane.MARKET
    asset_class: AssetClass = AssetClass.UNKNOWN

    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    source_timestamp: datetime | None = None  # timestamp from provider, if available

    raw_payload: dict[str, Any] = field(default_factory=dict)
    raw_headers: dict[str, str] = field(default_factory=dict)
    raw_bytes: bytes | None = None

    sequence: int | None = None
    stream_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "envelope_id": self.envelope_id,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "venue": self.venue,
            "data_plane": self.data_plane.value,
            "asset_class": self.asset_class.value,
            "received_at": self.received_at.isoformat(),
            "source_timestamp": self.source_timestamp.isoformat()
            if self.source_timestamp
            else None,
            "raw_payload": self.raw_payload,
            "raw_headers": self.raw_headers,
            "sequence": self.sequence,
            "stream_id": self.stream_id,
        }
        if self.raw_bytes is not None:
            result["raw_bytes_base64"] = self.raw_bytes.hex()
        return result


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """Normalized event - canonical internal representation.

    All external sources normalize to this format. Downstream consumers
    (features, intelligence, risk, UI) only see NormalizedEvent.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""  # trade | quote | book | candle | news | macro | sentiment
    source_id: str = ""
    source_name: str = ""
    venue: str | None = None
    data_plane: DataPlane = DataPlane.MARKET
    asset_class: AssetClass = AssetClass.UNKNOWN

    # Timestamps - the critical clock sync chain
    event_time: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )  # when event occurred at source
    source_timestamp: datetime | None = None  # provider's timestamp (if different from event_time)
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))  # when ATI received it
    ingested_at: datetime = field(default_factory=lambda: datetime.now(UTC))  # when persisted
    processed_at: datetime | None = None  # when feature/intelligence processed it

    # Instrument identity
    instrument_id: str = ""  # canonical instrument ID
    symbol: str = ""  # venue-specific symbol
    base_asset: str = ""
    quote_asset: str = ""

    # Payload (event-type specific, JSON-serializable)
    payload: dict[str, Any] = field(default_factory=dict)

    # Sequence & ordering
    sequence: int | None = None
    stream_id: str | None = None

    # Quality & provenance
    source_latency_ms: float = 0.0  # received_at - event_time
    ingestion_latency_ms: float = 0.0  # ingested_at - received_at
    processing_latency_ms: float = 0.0  # processed_at - ingested_at
    quality_score: float = 1.0
    raw_envelope_id: str | None = None  # link to raw envelope

    # Market data specific
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    quantity: float | None = None
    side: str | None = None  # buy | sell

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "source_id": self.source_id,
            "source_name": self.source_name,
            "venue": self.venue,
            "data_plane": self.data_plane.value,
            "asset_class": self.asset_class.value,
            "event_time": self.event_time.isoformat(),
            "source_timestamp": self.source_timestamp.isoformat()
            if self.source_timestamp
            else None,
            "received_at": self.received_at.isoformat(),
            "ingested_at": self.ingested_at.isoformat(),
            "processed_at": self.processed_at.isoformat() if self.processed_at else None,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "payload": self.payload,
            "sequence": self.sequence,
            "stream_id": self.stream_id,
            "source_latency_ms": self.source_latency_ms,
            "ingestion_latency_ms": self.ingestion_latency_ms,
            "processing_latency_ms": self.processing_latency_ms,
            "quality_score": self.quality_score,
            "raw_envelope_id": self.raw_envelope_id,
            "price": self.price,
            "bid": self.bid,
            "ask": self.ask,
            "quantity": self.quantity,
            "side": self.side,
        }

    @classmethod
    def create_trade(
        cls,
        *,
        source_id: str,
        source_name: str,
        venue: str,
        instrument_id: str,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        event_time: datetime,
        price: float,
        quantity: float,
        side: str,
        trade_id: str | int,
        received_at: datetime | None = None,
        raw_envelope_id: str | None = None,
        asset_class: AssetClass = AssetClass.CRYPTO,
        quality_score: float = 1.0,
        **extra_payload: Any,
    ) -> NormalizedEvent:
        """Factory for trade events."""
        received = received_at or datetime.now(UTC)
        return cls(
            event_type="trade",
            source_id=source_id,
            source_name=source_name,
            venue=venue,
            asset_class=asset_class,
            event_time=event_time,
            received_at=received,
            instrument_id=instrument_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price=price,
            quantity=quantity,
            side=side,
            payload={
                "trade_id": trade_id,
                **extra_payload,
            },
            source_latency_ms=(received - event_time).total_seconds() * 1000,
            quality_score=quality_score,
            raw_envelope_id=raw_envelope_id,
        )

    @classmethod
    def create_quote(
        cls,
        *,
        source_id: str,
        source_name: str,
        venue: str,
        instrument_id: str,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        event_time: datetime,
        bid: float,
        ask: float,
        received_at: datetime | None = None,
        raw_envelope_id: str | None = None,
        asset_class: AssetClass = AssetClass.CRYPTO,
        quality_score: float = 1.0,
        **extra_payload: Any,
    ) -> NormalizedEvent:
        """Factory for quote/ticker events."""
        received = received_at or datetime.now(UTC)
        mid = (bid + ask) / 2.0 if bid and ask else None
        return cls(
            event_type="quote",
            source_id=source_id,
            source_name=source_name,
            venue=venue,
            asset_class=asset_class,
            event_time=event_time,
            received_at=received,
            instrument_id=instrument_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            bid=bid,
            ask=ask,
            price=mid,
            payload=extra_payload,
            source_latency_ms=(received - event_time).total_seconds() * 1000,
            quality_score=quality_score,
            raw_envelope_id=raw_envelope_id,
        )

    @classmethod
    def create_candle(
        cls,
        *,
        source_id: str,
        source_name: str,
        venue: str,
        instrument_id: str,
        symbol: str,
        base_asset: str,
        quote_asset: str,
        event_time: datetime,  # candle open time
        open_: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        interval: str,  # 1m, 5m, 1h, 1d, etc.
        received_at: datetime | None = None,
        raw_envelope_id: str | None = None,
        asset_class: AssetClass = AssetClass.CRYPTO,
        quality_score: float = 1.0,
        **extra_payload: Any,
    ) -> NormalizedEvent:
        """Factory for candle/OHLCV events."""
        received = received_at or datetime.now(UTC)
        return cls(
            event_type="candle",
            source_id=source_id,
            source_name=source_name,
            venue=venue,
            asset_class=asset_class,
            event_time=event_time,
            received_at=received,
            instrument_id=instrument_id,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            price=close,
            payload={
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "interval": interval,
                **extra_payload,
            },
            source_latency_ms=(received - event_time).total_seconds() * 1000,
            quality_score=quality_score,
            raw_envelope_id=raw_envelope_id,
        )

    @classmethod
    def create_news(
        cls,
        *,
        source_id: str,
        source_name: str,
        venue: str | None,
        event_time: datetime,
        headline: str,
        url: str,
        entities: list[str],
        assets: list[str],
        currencies: list[str],
        regions: list[str],
        category: str,
        source_quality: float,
        novelty_score: float,
        relevance_score: float,
        impact_score: float,
        received_at: datetime | None = None,
        raw_envelope_id: str | None = None,
        asset_class: AssetClass = AssetClass.UNKNOWN,
        **extra_payload: Any,
    ) -> NormalizedEvent:
        """Factory for news/intelligence events."""
        received = received_at or datetime.now(UTC)
        return cls(
            event_type="news",
            source_id=source_id,
            source_name=source_name,
            venue=venue,
            data_plane=DataPlane.INTELLIGENCE,
            asset_class=asset_class,
            event_time=event_time,
            received_at=received,
            instrument_id="",  # news can relate to multiple instruments
            symbol="",
            base_asset="",
            quote_asset="",
            payload={
                "headline": headline,
                "url": url,
                "entities": entities,
                "assets": assets,
                "currencies": currencies,
                "regions": regions,
                "category": category,
                "source_quality": source_quality,
                "novelty_score": novelty_score,
                "relevance_score": relevance_score,
                "impact_score": impact_score,
                **extra_payload,
            },
            source_latency_ms=(received - event_time).total_seconds() * 1000,
            quality_score=source_quality,
            raw_envelope_id=raw_envelope_id,
        )

    @classmethod
    def create_macro(
        cls,
        *,
        source_id: str,
        source_name: str,
        venue: str | None,
        event_time: datetime,  # scheduled release time
        country: str,
        currency: str,
        event_name: str,
        impact: str,  # HIGH | MEDIUM | LOW
        forecast: float | None,
        previous: float | None,
        actual: float | None,
        revision: float | None,
        status: str,  # SCHEDULED | RELEASED | REVISED
        received_at: datetime | None = None,
        raw_envelope_id: str | None = None,
        asset_class: AssetClass = AssetClass.RATES,
        quality_score: float = 1.0,
        **extra_payload: Any,
    ) -> NormalizedEvent:
        """Factory for macroeconomic calendar events."""
        received = received_at or datetime.now(UTC)
        surprise = None
        if actual is not None and forecast is not None:
            surprise = actual - forecast
        return cls(
            event_type="macro",
            source_id=source_id,
            source_name=source_name,
            venue=venue,
            data_plane=DataPlane.MACRO,
            asset_class=asset_class,
            event_time=event_time,
            received_at=received,
            instrument_id="",
            symbol=currency,
            base_asset=currency,
            quote_asset="",
            payload={
                "country": country,
                "currency": currency,
                "event_name": event_name,
                "impact": impact,
                "forecast": forecast,
                "previous": previous,
                "actual": actual,
                "revision": revision,
                "surprise": surprise,
                "status": status,
                **extra_payload,
            },
            source_latency_ms=(received - event_time).total_seconds() * 1000,
            quality_score=quality_score,
            raw_envelope_id=raw_envelope_id,
        )
