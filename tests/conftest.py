"""Shared fixtures for Sprint 4A tests."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta

import pytest
from backend.application.context.bootstrap import build_context_pipeline
from backend.application.interfaces.context_settings import ContextSettings, FeatureSettings
from backend.domain.observation.event import ObservationEvent, ObservationEventType


@pytest.fixture
def base_time() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0)


@pytest.fixture
def make_trade_event(base_time: datetime) -> Callable[..., ObservationEvent]:
    def _factory(
        *,
        symbol: str = "BTCUSDT",
        price: float = 100.0,
        quantity: float = 1.0,
        offset_seconds: int = 0,
        trade_id: int = 1,
    ) -> ObservationEvent:
        ts = base_time + timedelta(seconds=offset_seconds)
        return ObservationEvent(
            source_id="binance",
            source_name="Binance",
            event_type=ObservationEventType.TRADE,
            timestamp=ts,
            payload={
                "symbol": symbol,
                "trade_id": trade_id,
                "price": price,
                "quantity": quantity,
                "trade_time": ts,
                "is_market_maker": False,
            },
        )

    return _factory


@pytest.fixture
def make_order_book_event(base_time: datetime) -> Callable[..., ObservationEvent]:
    def _factory(
        *,
        symbol: str = "BTCUSDT",
        offset_seconds: int = 0,
    ) -> ObservationEvent:
        ts = base_time + timedelta(seconds=offset_seconds)
        return ObservationEvent(
            source_id="binance",
            source_name="Binance",
            event_type=ObservationEventType.ORDER_BOOK,
            timestamp=ts,
            payload={
                "symbol": symbol,
                "bids": [[100.0, 5.0], [99.5, 3.0], [99.0, 2.0]],
                "asks": [[100.5, 4.0], [101.0, 6.0], [101.5, 1.0]],
            },
        )

    return _factory


@pytest.fixture
def test_settings() -> ContextSettings:
    return ContextSettings(
        window_duration=timedelta(minutes=5),
        features={
            "trend": FeatureSettings(
                enabled=True, parameters={"lookback": 5, "flat_threshold_pct": 0.05}
            ),
            "momentum": FeatureSettings(enabled=True, parameters={"lookback": 3}),
            "volatility": FeatureSettings(
                enabled=True, parameters={"lookback": 10, "min_samples": 3}
            ),
            "volume": FeatureSettings(enabled=True, parameters={"lookback": 5}),
            "liquidity": FeatureSettings(
                enabled=True, parameters={"depth_levels": 2, "lookback": 5}
            ),
        },
    )


@pytest.fixture
def context_pipeline(test_settings: ContextSettings):
    return build_context_pipeline(test_settings)


def build_price_series_events(
    make_trade_event: Callable[..., ObservationEvent],
    prices: list[float],
) -> list[ObservationEvent]:
    return [
        make_trade_event(price=price, quantity=1.0, offset_seconds=i, trade_id=i + 1)
        for i, price in enumerate(prices)
    ]


def context_semantic_dict(context) -> dict:
    """Serialise a MarketContext excluding non-deterministic execution timings."""
    return {
        "created_at": context.created_at.isoformat(timespec="milliseconds"),
        "snapshot": {
            "start": context.snapshot.start_timestamp.isoformat(timespec="milliseconds"),
            "end": context.snapshot.end_timestamp.isoformat(timespec="milliseconds"),
            "event_count": len(context.snapshot.events),
        },
        "features": {name: feature.value for name, feature in context.features},
    }
