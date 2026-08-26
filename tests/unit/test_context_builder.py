"""Unit tests for ContextBuilderImpl."""

from __future__ import annotations

import pytest
from backend.application.context_builder_impl import (
    MARKET_CONTEXT_CREATED,
    ContextBuilderImpl,
)
from backend.application.feature_engine_impl import FeatureEngineImpl
from backend.application.window_manager_impl import InMemoryWindowManager
from backend.domain.context.feature_registry import FeatureRegistry
from backend.domain.context.features import ALL_FEATURES
from backend.infrastructure.event_bus.in_memory_event_bus import InMemoryEventBus

from tests.conftest import build_price_series_events, context_semantic_dict


class TestContextBuilder:
    def test_event_pipeline_creates_market_context(self, make_trade_event, test_settings):
        bus = InMemoryEventBus()
        window_manager = InMemoryWindowManager(test_settings)
        registry = FeatureRegistry()
        for feature in ALL_FEATURES:
            registry.register(feature)
        feature_engine = FeatureEngineImpl(registry, test_settings)
        builder = ContextBuilderImpl(window_manager, feature_engine, bus)

        events = build_price_series_events(
            make_trade_event,
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110],
        )
        context = None
        for event in events:
            context = builder.handle(event)

        assert context is not None
        assert context.created_at == events[-1].timestamp
        feature_names = {name for name, _ in context.features}
        assert "trend" in feature_names
        assert "momentum" in feature_names

    def test_event_bus_publication(self, make_trade_event, test_settings):
        bus = InMemoryEventBus()
        window_manager = InMemoryWindowManager(test_settings)
        registry = FeatureRegistry()
        registry.register(ALL_FEATURES[0])
        feature_engine = FeatureEngineImpl(registry, test_settings)
        builder = ContextBuilderImpl(window_manager, feature_engine, bus)

        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        builder.handle(events[-1])

        published = bus.published_events()
        assert len(published) == 1
        event_name, payload = published[0]
        assert event_name == MARKET_CONTEXT_CREATED
        assert payload.symbol == "BTCUSDT"
        assert payload.context is not None

    def test_missing_symbol_raises(self, make_trade_event, test_settings):
        bus = InMemoryEventBus()
        builder, _, _, _ = __import__(
            "backend.application.context.bootstrap", fromlist=["build_context_pipeline"]
        ).build_context_pipeline(test_settings, bus)

        broken = make_trade_event().model_copy(update={"payload": {"price": 1.0}})
        with pytest.raises(KeyError):
            builder.handle(broken)

    def test_replay_produces_identical_semantic_context(self, make_trade_event, test_settings):
        prices = [100 + i for i in range(12)]

        def run_once() -> dict:
            bus = InMemoryEventBus()
            builder, _, _, _ = __import__(
                "backend.application.context.bootstrap",
                fromlist=["build_context_pipeline"],
            ).build_context_pipeline(test_settings, bus)
            events = build_price_series_events(make_trade_event, prices)
            for event in events:
                context = builder.handle(event)
            return context_semantic_dict(context)

        assert run_once() == run_once()
