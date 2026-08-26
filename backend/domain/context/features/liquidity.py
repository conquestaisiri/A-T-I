# backend/domain/context/features/liquidity.py
"""Deterministic liquidity feature from order book depth or trade-size proxy."""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features._utils import (
    extract_order_book_levels,
    extract_volumes,
    lookback_slice,
)


class LiquidityFeature:
    """Liquidity estimate from order book depth or average trade size."""

    name: ClassVar[str] = "liquidity"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        params = parameters or {}
        depth_levels = int(params.get("depth_levels", 5))
        lookback = int(params.get("lookback", 10))

        start = time.perf_counter()
        books = extract_order_book_levels(snapshot)

        if books:
            bids, asks = books[-1]
            bid_depth = sum(size for _, size in bids[:depth_levels])
            ask_depth = sum(size for _, size in asks[:depth_levels])
            total_depth = bid_depth + ask_depth
            source = "order_book"
            value = {
                "source": source,
                "bid_depth": round(bid_depth, 8),
                "ask_depth": round(ask_depth, 8),
                "total_depth": round(total_depth, 8),
                "depth_levels": depth_levels,
            }
        else:
            volumes = lookback_slice(extract_volumes(snapshot), lookback)
            if not volumes:
                raise ValueError("LiquidityFeature requires order book events or trade volumes")
            average_trade_size = sum(volumes) / len(volumes)
            source = "trade_proxy"
            value = {
                "source": source,
                "average_trade_size": round(average_trade_size, 8),
                "trade_count": len(volumes),
            }

        return ContextFeature(
            name=LiquidityFeature.name,
            value=value,
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
