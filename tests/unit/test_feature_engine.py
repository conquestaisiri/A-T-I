"""Unit tests for FeatureEngineImpl."""

from __future__ import annotations

import logging

from backend.application.feature_engine_impl import FeatureEngineImpl
from backend.application.interfaces.context_settings import ContextSettings, FeatureSettings
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.feature_registry import FeatureRegistry
from backend.domain.context.features.trend import TrendFeature

from tests.conftest import build_price_series_events


class FailingFeature:
    name = "failing"

    @staticmethod
    def compute(snapshot: ContextSnapshot) -> ContextFeature:
        raise RuntimeError("boom")


class WarmUpFeature:
    name = "warming_up"

    @staticmethod
    def compute(snapshot: ContextSnapshot) -> ContextFeature:
        raise ValueError("WarmUpFeature requires at least 2 price observations")


class TestFeatureEngine:
    def test_successful_execution(self, make_trade_event, test_settings: ContextSettings):
        registry = FeatureRegistry()
        registry.register(TrendFeature)
        engine = FeatureEngineImpl(registry, test_settings)

        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))
        result = engine.run(snapshot)

        assert len(result.features) == 1
        assert result.features[0].name == "trend"
        assert result.health.successful_features == 1
        assert result.health.failed_features == 0

    def test_failure_isolation(self, make_trade_event, test_settings: ContextSettings):
        settings = ContextSettings(
            window_duration=test_settings.window_duration,
            features={
                **test_settings.features,
                "failing": FeatureSettings(enabled=True, parameters={}),
            },
        )
        registry = FeatureRegistry()
        registry.register(TrendFeature)
        registry.register(FailingFeature)
        engine = FeatureEngineImpl(registry, settings)

        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))
        result = engine.run(snapshot)

        assert {feature.name for feature in result.features} == {"trend"}
        assert result.health.total_features == 2
        assert result.health.successful_features == 1
        assert result.health.failed_features == 1
        assert "failing" in result.health.errors

    def test_warmup_value_error_logged_without_traceback(
        self, make_trade_event, test_settings: ContextSettings, caplog
    ):
        settings = ContextSettings(
            window_duration=test_settings.window_duration,
            features={
                **test_settings.features,
                "warming_up": FeatureSettings(enabled=True, parameters={}),
            },
        )
        registry = FeatureRegistry()
        registry.register(TrendFeature)
        registry.register(WarmUpFeature)
        engine = FeatureEngineImpl(registry, settings)

        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))

        with caplog.at_level(logging.WARNING, logger="backend.application.feature_engine_impl"):
            result = engine.run(snapshot)

        assert result.health.failed_features == 1
        assert "warming_up" in result.health.errors
        warn_logs = [
            r for r in caplog.records if r.name == "backend.application.feature_engine_impl"
        ]
        assert warn_logs and all(r.levelno == logging.WARNING for r in warn_logs)
        assert all("Traceback" not in r.exc_text for r in warn_logs if r.exc_text)

    def test_disabled_features_skipped(self, make_trade_event):
        settings = ContextSettings(
            window_duration=__import__("datetime").timedelta(minutes=5),
            features={
                "trend": FeatureSettings(
                    enabled=False, parameters={"lookback": 5, "flat_threshold_pct": 0.05}
                )
            },
        )
        registry = FeatureRegistry()
        registry.register(TrendFeature)
        engine = FeatureEngineImpl(registry, settings)

        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))
        result = engine.run(snapshot)

        assert result.features == []
        assert result.health.total_features == 0

    def test_execution_timing_recorded(self, make_trade_event, test_settings: ContextSettings):
        registry = FeatureRegistry()
        registry.register(TrendFeature)
        engine = FeatureEngineImpl(registry, test_settings)

        events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
        snapshot = ContextSnapshot.from_events(tuple(events))
        result = engine.run(snapshot)

        assert "trend" in result.health.execution_times
        assert result.health.execution_times["trend"] >= 0.0
