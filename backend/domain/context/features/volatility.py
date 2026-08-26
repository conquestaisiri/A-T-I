# backend/domain/context/features/volatility.py
"""Deterministic volatility feature based on return standard deviation."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features._utils import extract_prices, lookback_slice


class VolatilityFeature:
    """Population standard deviation of simple returns over lookback."""

    name: ClassVar[str] = "volatility"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        params = parameters or {}
        lookback = int(params.get("lookback", 20))
        min_samples = int(params.get("min_samples", 3))

        start = time.perf_counter()
        prices = lookback_slice(extract_prices(snapshot), lookback)
        if len(prices) < min_samples:
            raise ValueError(
                f"VolatilityFeature requires at least {min_samples} price observations"
            )

        returns: list[float] = []
        for prev, curr in zip(prices[:-1], prices[1:], strict=False):
            if prev == 0:
                continue
            returns.append((curr - prev) / prev)

        if len(returns) < 2:
            raise ValueError("VolatilityFeature requires at least 2 valid returns")

        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_dev = math.sqrt(variance)

        return ContextFeature(
            name=VolatilityFeature.name,
            value={
                "std_dev": round(std_dev, 8),
                "mean_return": round(mean_return, 8),
                "return_count": len(returns),
                "sample_count": len(prices),
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
