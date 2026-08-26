from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar, Protocol

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features.book_imbalance import BookImbalanceFeature
from backend.domain.context.features.insider import InsiderFeature
from backend.domain.context.features.kyle_lambda import KyleLambdaFeature
from backend.domain.context.features.liquidity import LiquidityFeature
from backend.domain.context.features.micro_price import MicroPriceFeature
from backend.domain.context.features.momentum import MomentumFeature
from backend.domain.context.features.order_flow import OrderFlowFeature
from backend.domain.context.features.regime import RegimeFeature
from backend.domain.context.features.sentiment import SentimentFeature
from backend.domain.context.features.trend import TrendFeature
from backend.domain.context.features.volatility import VolatilityFeature
from backend.domain.context.features.volume import VolumeFeature


class FeatureCls(Protocol):
    """Structural type for context feature classes.

    A feature class exposes a class-level ``name`` and a static ``compute``
    method that derives a :class:`ContextFeature` from a snapshot.
    """

    name: ClassVar[str]

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature: ...


ALL_FEATURES: tuple[type[FeatureCls], ...] = (
    TrendFeature,
    MomentumFeature,
    VolatilityFeature,
    VolumeFeature,
    LiquidityFeature,
    SentimentFeature,
    InsiderFeature,
    OrderFlowFeature,
    MicroPriceFeature,
    RegimeFeature,
    BookImbalanceFeature,
    KyleLambdaFeature,
)

# Declarative per-feature parameter schemas (task P0-003). The config loader
# validates every configured parameter against these; anything not declared
# here is rejected at startup. Supported kinds:
#   "int"   -> an integer >= "min"
#   "float" -> an int/float >= "min"
#   "str"   -> a non-empty string
FEATURE_PARAMETER_SCHEMAS: dict[str, dict[str, dict[str, Any]]] = {
    "trend": {
        "lookback": {"kind": "int", "min": 1},
        "flat_threshold_pct": {"kind": "float", "min": 0.0},
    },
    "momentum": {"lookback": {"kind": "int", "min": 1}},
    "volatility": {
        "lookback": {"kind": "int", "min": 1},
        "min_samples": {"kind": "int", "min": 1},
    },
    "volume": {"lookback": {"kind": "int", "min": 1}},
    "liquidity": {
        "depth_levels": {"kind": "int", "min": 1},
        "lookback": {"kind": "int", "min": 1},
    },
    "sentiment": {"symbol": {"kind": "str"}},
    "insider": {"symbol": {"kind": "str"}},
    "order_flow": {"symbol": {"kind": "str"}},
    "micro_price": {"symbol": {"kind": "str"}},
    "regime": {"symbol": {"kind": "str"}},
    "book_imbalance": {"depth_levels": {"kind": "int", "min": 1}},
    "kyle_lambda": {},
}

__all__ = [
    "FeatureCls",
    "TrendFeature",
    "MomentumFeature",
    "VolatilityFeature",
    "VolumeFeature",
    "LiquidityFeature",
    "SentimentFeature",
    "InsiderFeature",
    "OrderFlowFeature",
    "MicroPriceFeature",
    "RegimeFeature",
    "BookImbalanceFeature",
    "KyleLambdaFeature",
    "ALL_FEATURES",
    "FEATURE_PARAMETER_SCHEMAS",
]
