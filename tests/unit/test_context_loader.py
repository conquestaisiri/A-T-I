"""Unit tests for context configuration loading (task P0-003).

Every registered feature must be explicitly configured, unknown feature names
and unrecognised parameters must fail at startup, and newer experimental
features must be disabled by default in the shipped configuration.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from backend.domain.context.errors import ConfigurationError
from backend.infrastructure.config.context_loader import load_context_settings


class TestContextLoader:
    def test_load_default_config(self):
        settings = load_context_settings(Path("config/context.yaml"))
        assert settings.window_duration.total_seconds() == 900000
        assert settings.is_feature_enabled("trend")
        assert settings.feature_parameters("trend")["lookback"] == 10

    def test_default_config_declares_all_registered_features(self):
        settings = load_context_settings(Path("config/context.yaml"))
        for name in _feature_names():
            assert name in settings.features, f"{name} must be explicitly configured"

    def test_experimental_features_disabled_by_default(self):
        settings = load_context_settings(Path("config/context.yaml"))
        for name in (
            "sentiment",
            "insider",
            "order_flow",
            "micro_price",
            "regime",
            "book_imbalance",
            "kyle_lambda",
        ):
            assert not settings.is_feature_enabled(name), (
                f"experimental feature {name} must default to disabled"
            )

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(ConfigurationError):
            load_context_settings(tmp_path / "missing.yaml")

    def test_invalid_window_duration(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 0},
            "features": _valid_features(),
        }
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_context_settings(path)

    def test_missing_registered_feature_raises(self, tmp_path: Path):
        features = _valid_features()
        features.pop("regime")
        config = {"window": {"duration_seconds": 60}, "features": features}
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="regime"):
            load_context_settings(path)

    def test_unknown_feature_name_raises(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 60},
            "features": {**_valid_features(), "bogus": {"enabled": True, "parameters": {}}},
        }
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="bogus"):
            load_context_settings(path)

    def test_unknown_feature_parameter(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 60},
            "features": _valid_features(),
        }
        config["features"]["trend"]["parameters"]["unknown"] = 1
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_context_settings(path)

    def test_unknown_parameter_on_new_feature_raises(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 60},
            "features": _valid_features(),
        }
        config["features"]["order_flow"]["parameters"]["window_seconds"] = 60
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="window_seconds"):
            load_context_settings(path)

    def test_invalid_parameter_type_raises(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 60},
            "features": _valid_features(),
        }
        config["features"]["momentum"]["parameters"]["lookback"] = "10"
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_context_settings(path)

    def test_zero_lookback_raises(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 60},
            "features": _valid_features(),
        }
        config["features"]["volume"]["parameters"]["lookback"] = 0
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_context_settings(path)

    def test_string_parameter_must_be_non_empty(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 60},
            "features": _valid_features(),
        }
        config["features"]["micro_price"]["parameters"]["symbol"] = "  "
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_context_settings(path)

    def test_non_bool_enabled_raises(self, tmp_path: Path):
        config = {
            "window": {"duration_seconds": 60},
            "features": _valid_features(),
        }
        config["features"]["trend"]["enabled"] = "yes"
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        with pytest.raises(ConfigurationError):
            load_context_settings(path)

    def test_valid_full_config_loads(self, tmp_path: Path):
        config = {"window": {"duration_seconds": 60}, "features": _valid_features()}
        path = tmp_path / "context.yaml"
        path.write_text(yaml.dump(config), encoding="utf-8")
        settings = load_context_settings(path)
        assert set(settings.features) == set(_feature_names())
        assert settings.feature_parameters("regime")["symbol"] == "BTC"


def _feature_names() -> list[str]:
    return [
        "trend",
        "momentum",
        "volatility",
        "volume",
        "liquidity",
        "sentiment",
        "insider",
        "order_flow",
        "micro_price",
        "regime",
        "book_imbalance",
        "kyle_lambda",
    ]


def _valid_features() -> dict:
    return {
        "trend": {"enabled": True, "parameters": {"lookback": 10, "flat_threshold_pct": 0.05}},
        "momentum": {"enabled": True, "parameters": {"lookback": 5}},
        "volatility": {"enabled": True, "parameters": {"lookback": 20, "min_samples": 3}},
        "volume": {"enabled": True, "parameters": {"lookback": 10}},
        "liquidity": {"enabled": True, "parameters": {"depth_levels": 5, "lookback": 10}},
        "sentiment": {"enabled": False, "parameters": {"symbol": "BTC"}},
        "insider": {"enabled": False, "parameters": {"symbol": "BTC"}},
        "order_flow": {"enabled": False, "parameters": {"symbol": "BTC"}},
        "micro_price": {"enabled": False, "parameters": {"symbol": "BTC"}},
        "regime": {"enabled": False, "parameters": {"symbol": "BTC"}},
        "book_imbalance": {"enabled": False, "parameters": {"depth_levels": 10}},
        "kyle_lambda": {"enabled": False, "parameters": {}},
    }
