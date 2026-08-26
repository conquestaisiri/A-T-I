# backend/infrastructure/config/context_loader.py
"""Load and validate context.yaml into immutable ContextSettings."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from pathlib import Path
from typing import Any

import yaml

from backend.application.interfaces.context_settings import ContextSettings, FeatureSettings
from backend.domain.context.errors import ConfigurationError
from backend.domain.context.features import ALL_FEATURES, FEATURE_PARAMETER_SCHEMAS

# Every registered feature must be explicitly configured (task P0-003). The
# known set is derived from the domain registry so configuration can never
# silently drift from the set of features the engine actually runs.
KNOWN_FEATURES = frozenset(getattr(cls, "name", "") for cls in ALL_FEATURES)


def load_context_settings(path: str | Path) -> ContextSettings:
    """Load context configuration from a YAML file.

    Parameters
    ----------
    path: str | Path
        Path to ``context.yaml``.

    Returns
    -------
    ContextSettings
        Validated immutable settings.

    Raises
    ------
    ConfigurationError
        If the file is missing, malformed, or contains invalid values.
    """
    config_path = Path(path)
    if not config_path.is_file():
        raise ConfigurationError(f"Context configuration file not found: {config_path}")

    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Failed to parse context YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigurationError("Context configuration root must be a mapping")

    window_duration = _parse_window_duration(raw.get("window"))
    features = _parse_features(raw.get("features"))

    return ContextSettings(window_duration=window_duration, features=features)


def _parse_window_duration(window_section: Any) -> timedelta:
    if not isinstance(window_section, dict):
        raise ConfigurationError("'window' section must be a mapping")

    duration_seconds = window_section.get("duration_seconds")
    if not isinstance(duration_seconds, int) or duration_seconds <= 0:
        raise ConfigurationError("'window.duration_seconds' must be a positive integer")

    return timedelta(seconds=duration_seconds)


def _parse_features(features_section: Any) -> Mapping[str, FeatureSettings]:
    if not isinstance(features_section, dict):
        raise ConfigurationError("'features' section must be a mapping")

    provided = set(features_section.keys())

    unknown = provided - KNOWN_FEATURES
    if unknown:
        raise ConfigurationError(f"Unknown feature configuration entries: {sorted(unknown)}")

    missing = KNOWN_FEATURES - provided
    if missing:
        raise ConfigurationError(
            "Missing configuration for registered feature(s): "
            f"{sorted(missing)} — every registered feature must be explicitly configured"
        )

    parsed: dict[str, FeatureSettings] = {}
    for name, entry in features_section.items():
        if not isinstance(entry, dict):
            raise ConfigurationError(f"Feature '{name}' configuration must be a mapping")

        enabled = entry.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigurationError(f"Feature '{name}.enabled' must be a boolean")

        parameters = entry.get("parameters", {})
        if not isinstance(parameters, dict):
            raise ConfigurationError(f"Feature '{name}.parameters' must be a mapping")

        _validate_feature_parameters(name, parameters)
        parsed[name] = FeatureSettings(enabled=enabled, parameters=dict(parameters))

    return parsed


def _validate_feature_parameters(name: str, parameters: Mapping[str, Any]) -> None:
    """Validate feature-specific parameters against the declarative schema."""
    schema = FEATURE_PARAMETER_SCHEMAS.get(name, {})

    for key, value in parameters.items():
        spec = schema.get(key)
        if spec is None:
            raise ConfigurationError(
                f"Feature '{name}.parameters.{key}' is not a recognised parameter"
            )

        kind = spec["kind"]
        if kind == "int":
            if isinstance(value, bool) or not isinstance(value, int) or value < spec["min"]:
                raise ConfigurationError(
                    f"Feature '{name}.parameters.{key}' must be an integer >= {spec['min']}"
                )
        elif kind == "float":
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or value < spec["min"]
            ):
                raise ConfigurationError(
                    f"Feature '{name}.parameters.{key}' must be a number >= {spec['min']}"
                )
        elif kind == "str":
            if not isinstance(value, str) or not value.strip():
                raise ConfigurationError(
                    f"Feature '{name}.parameters.{key}' must be a non-empty string"
                )
        else:  # pragma: no cover - schema kinds are static
            raise ConfigurationError(
                f"Feature '{name}.parameters.{key}' has an unknown schema kind"
            )
