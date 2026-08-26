# backend/application/interfaces/context_settings.py
"""Configuration settings required by the Context Builder components.

All values are immutable and validated at startup via ``context.yaml``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class FeatureSettings:
    """Per-feature configuration entry.

    Attributes
    ----------
    enabled: bool
        Whether the feature should be executed.
    parameters: Mapping[str, Any]
        Feature-specific parameters loaded from configuration.
    """

    enabled: bool = True
    parameters: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextSettings:
    """Immutable configuration for context processing.

    Attributes
    ----------
    window_duration: timedelta
        Length of the rolling time window for each symbol.
    features: Mapping[str, FeatureSettings]
        Per-feature enable flags and parameters keyed by feature name.
    """

    window_duration: timedelta
    features: Mapping[str, FeatureSettings] = field(default_factory=dict)

    def is_feature_enabled(self, name: str) -> bool:
        """Return whether a feature is enabled.

        Features not listed in configuration default to disabled. An unlisted
        feature must never silently activate (audit §15; task queue rule
        ``unlisted_features_default_enabled: false``).
        """
        if name not in self.features:
            return False
        return self.features[name].enabled

    def feature_parameters(self, name: str) -> Mapping[str, Any]:
        """Return parameters for the named feature, or an empty mapping."""
        if name not in self.features:
            return {}
        return self.features[name].parameters
