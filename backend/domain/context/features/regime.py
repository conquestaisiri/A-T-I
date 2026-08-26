# backend/domain/context/features\regime.py
"""Market regime feature from HMM + changepoint detection.

Detects bull/bear regimes using the domain-owned Gaussian HMM + CUSUM
detector (``backend.domain.context.regime_detector``). The feature is a pure
reader of per-symbol detector state — the domain feature never imports the
application layer (review gap G6).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features._utils import extract_prices
from backend.domain.context.regime_detector import get_detector


class RegimeFeature:
    """Market regime feature from HMM detection."""

    name: ClassVar[str] = "regime"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        """Read regime from the detector, feeding it a real price."""
        params = parameters or {}
        symbol = params.get("symbol", "BTC").upper()

        start = time.perf_counter()
        detector = get_detector(symbol)

        # Feed the most recent real price from the snapshot. If the snapshot
        # carries no price-carrying events (trade/ticker/candle), report the
        # detector's last known state instead of fabricating a price.
        prices = extract_prices(snapshot)
        result = detector.update(prices[-1]) if prices else detector.snapshot()

        return ContextFeature(
            name=RegimeFeature.name,
            value={
                "regime": result.regime,
                "regime_label": result.regime_label,
                "probability": result.probability,
                "volatility": result.volatility,
                "trend": result.trend,
                "changepoints": len(result.changepoints),
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
