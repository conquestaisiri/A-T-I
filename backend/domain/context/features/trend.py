# backend/domain/context/features/trend.py
"""Deterministic trend feature based on price change over a lookback window."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features._utils import extract_prices, lookback_slice


class TrendFeature:
    """Simple trend direction derived from first vs last price in lookback."""

    name: ClassVar[str] = "trend"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        params = parameters or {}
        lookback = int(params.get("lookback", 10))
        flat_threshold_pct = float(params.get("flat_threshold_pct", 0.05))

        start = time.perf_counter()
        prices = lookback_slice(extract_prices(snapshot), lookback)
        if len(prices) < 2:
            raise ValueError("TrendFeature requires at least 2 price observations")

        first_price = prices[0]
        last_price = prices[-1]
        if first_price == 0:
            raise ValueError("TrendFeature cannot compute with zero first price")

        change_pct = ((last_price - first_price) / first_price) * 100.0
        if abs(change_pct) <= flat_threshold_pct:
            direction = "flat"
        elif change_pct > 0:
            direction = "up"
        else:
            direction = "down"

        return ContextFeature(
            name=TrendFeature.name,
            value={
                "direction": direction,
                "change_pct": round(change_pct, 6),
                "first_price": first_price,
                "last_price": last_price,
                "sample_count": len(prices),
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
