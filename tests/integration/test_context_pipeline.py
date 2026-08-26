"""Integration tests for the full Context Builder pipeline."""

from __future__ import annotations

from backend.application.context.bootstrap import build_context_pipeline_from_config
from backend.application.context_builder_impl import MARKET_CONTEXT_CREATED
from backend.infrastructure.event_bus.in_memory_event_bus import InMemoryEventBus

from tests.conftest import build_price_series_events, context_semantic_dict


class TestContextPipelineIntegration:
    def test_full_pipeline_from_config(self, make_trade_event):
        bus = InMemoryEventBus()
        builder, window_manager, feature_engine, _, settings = build_context_pipeline_from_config(
            "config/context.yaml", bus
        )

        assert settings.window_duration.total_seconds() == 900000
        assert window_manager is not None
        assert feature_engine is not None

        events = build_price_series_events(
            make_trade_event,
            [100, 101, 102, 103, 104, 105, 106, 107, 108, 109, 110, 111],
        )
        for event in events:
            context = builder.handle(event)

        assert len(context.features) >= 4
        published = bus.published_events()
        assert published[-1][0] == MARKET_CONTEXT_CREATED

    def test_replay_identical_events_produce_identical_contexts(self, make_trade_event):
        prices = [100 + i for i in range(15)]

        def run_pipeline() -> list[dict]:
            bus = InMemoryEventBus()
            builder, _, _, _, _ = build_context_pipeline_from_config("config/context.yaml", bus)
            events = build_price_series_events(make_trade_event, prices)
            contexts = [builder.handle(event) for event in events]
            return [context_semantic_dict(ctx) for ctx in contexts]

        first_run = run_pipeline()
        second_run = run_pipeline()
        assert first_run == second_run
