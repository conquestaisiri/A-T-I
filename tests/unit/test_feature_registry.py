"""Unit tests for FeatureRegistry."""

from __future__ import annotations

import pytest
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.errors import DuplicateFeatureError, FeatureRegistrationError
from backend.domain.context.feature_registry import FeatureRegistry
from backend.domain.context.features.trend import TrendFeature


class AlphaFeature:
    name = "alpha"

    @staticmethod
    def compute(snapshot: ContextSnapshot) -> ContextFeature:
        return ContextFeature(
            name="alpha",
            value=1,
            computation_timestamp=snapshot.end_timestamp,
            execution_time=0.0,
        )


class BetaFeature:
    name = "beta"
    depends_on = ["alpha"]

    @staticmethod
    def compute(snapshot: ContextSnapshot) -> ContextFeature:
        return ContextFeature(
            name="beta",
            value=2,
            computation_timestamp=snapshot.end_timestamp,
            execution_time=0.0,
        )


class MissingNameFeature:
    @staticmethod
    def compute(snapshot: ContextSnapshot) -> ContextFeature:
        return ContextFeature(
            name="missing",
            value=0,
            computation_timestamp=snapshot.end_timestamp,
            execution_time=0.0,
        )


class TestFeatureRegistry:
    def test_register_and_lookup(self):
        registry = FeatureRegistry()
        registry.register(TrendFeature)
        assert registry.get_by_name("trend") is TrendFeature
        assert registry.get_by_name("unknown") is None

    def test_deterministic_ordering(self):
        registry = FeatureRegistry()
        registry.register(AlphaFeature)
        registry.register(BetaFeature)
        names = [cls.name for cls in registry.get_all()]
        assert names == ["alpha", "beta"]

    def test_duplicate_protection(self):
        registry = FeatureRegistry()
        registry.register(AlphaFeature)
        with pytest.raises(DuplicateFeatureError):
            registry.register(AlphaFeature)

    def test_missing_name_rejected(self):
        registry = FeatureRegistry()
        with pytest.raises(FeatureRegistrationError):
            registry.register(MissingNameFeature)

    def test_unknown_dependency_rejected(self):
        registry = FeatureRegistry()

        class BadDependencyFeature:
            name = "bad"
            depends_on = ["missing"]

            @staticmethod
            def compute(snapshot: ContextSnapshot) -> ContextFeature:
                return ContextFeature(
                    name="bad",
                    value=0,
                    computation_timestamp=snapshot.end_timestamp,
                    execution_time=0.0,
                )

        with pytest.raises(FeatureRegistrationError):
            registry.register(BadDependencyFeature)

    def test_clear_for_test_isolation(self):
        registry = FeatureRegistry()
        registry.register(AlphaFeature)
        registry.clear()
        assert registry.get_all() == []
