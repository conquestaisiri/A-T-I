# backend/domain/context/features/momentum.py
"""Deterministic momentum feature based on rate of change."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features._utils import extract_prices, lookback_slice


class MomentumFeature:
    """Rate-of-change momentum over a configurable lookback window."""

    name: ClassVar[str] = "momentum"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        params = parameters or {}
        lookback = int(params.get("lookback", 5))

        start = time.perf_counter()
        prices = lookback_slice(extract_prices(snapshot), lookback)
        if len(prices) < 2:
            raise ValueError("MomentumFeature requires at least 2 price observations")

        base_price = prices[0]
        current_price = prices[-1]
        if base_price == 0:
            raise ValueError("MomentumFeature cannot compute with zero base price")

        roc = ((current_price - base_price) / base_price) * 100.0

        return ContextFeature(
            name=MomentumFeature.name,
            value={
                "rate_of_change_pct": round(roc, 6),
                "base_price": base_price,
                "current_price": current_price,
                "sample_count": len(prices),
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
