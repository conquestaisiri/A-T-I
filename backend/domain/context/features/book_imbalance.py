# backend/domain/context/features/book_imbalance.py
"""Depth-weighted order book imbalance (OBI) and book slope feature.

Implements Tier 2 microstructure signals (integration #11):

- **Depth-weighted OBI** over L1..L10: a signed imbalance statistic that
  weights each level's size by how close it is to the mid. Positive values
  mean the bid side is deeper (buy pressure), negative the ask side.
- **Book slope**: the log-log slope of size vs. distance from the mid
  (Kyle-style depth). A steeper book (more size near the mid) means lower
  expected price impact per unit flow.

Both are computed from the *current snapshot feed* — no L2 deltas required
(this is why #11 was ranked ahead of integrated OFI, which is blocked on
delta capture).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features._utils import extract_order_book_levels


def order_book_imbalance(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    depth_levels: int = 10,
) -> dict[str, float]:
    """Compute depth-weighted OBI over ``depth_levels`` levels.

    Each level contributes its signed size weighted by ``1/(level+1)`` so the
    best level dominates but deeper liquidity still counts. Zero-size books
    (both sides empty) return a neutral 0.0 imbalance.
    """
    if not bids and not asks:
        return {
            "obi": 0.0,
            "bid_weighted_size": 0.0,
            "ask_weighted_size": 0.0,
            "levels_used": 0,
        }
    bid_weighted = 0.0
    ask_weighted = 0.0
    levels = 0
    for level in range(min(depth_levels, max(len(bids), len(asks)))):
        weight = 1.0 / (level + 1.0)
        if level < len(bids):
            bid_weighted += bids[level][1] * weight
        if level < len(asks):
            ask_weighted += asks[level][1] * weight
        levels += 1
    total = bid_weighted + ask_weighted
    obi = 0.0 if total <= 0.0 else (bid_weighted - ask_weighted) / total
    return {
        "obi": round(obi, 6),
        "bid_weighted_size": round(bid_weighted, 8),
        "ask_weighted_size": round(ask_weighted, 8),
        "levels_used": levels,
    }


def book_slope(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
    *,
    mid_price: float,
) -> float | None:
    """Log-log slope of size vs. distance from the mid (market depth slope).

    Levels at equal distance on both sides are aggregated (summed) first,
    then an OLS slope of ``log(size)`` against ``log(distance)`` is fit.
    Returns ``None`` when fewer than 2 distinct distances have positive size.
    """
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    if best_bid is None or best_ask is None or best_bid >= best_ask:
        return None
    if mid_price <= 0.0:
        return None

    from math import log

    by_distance: dict[float, float] = {}
    for price, size in [*bids, *asks]:
        distance = abs(price - mid_price)
        if distance <= 0.0 or size <= 0.0:
            continue
        by_distance[distance] = by_distance.get(distance, 0.0) + size
    if len(by_distance) < 2:
        return None

    points = sorted(by_distance.items())
    n = len(points)
    log_x = [log(distance) for distance, _ in points]
    log_y = [log(size) for _, size in points]
    mean_x = sum(log_x) / n
    mean_y = sum(log_y) / n
    num = sum((log_x[i] - mean_x) * (log_y[i] - mean_y) for i in range(n))
    den = sum((log_x[i] - mean_x) ** 2 for i in range(n))
    if den == 0.0:
        return None
    return round(num / den, 6)


def spread_and_mid(
    bids: list[tuple[float, float]],
    asks: list[tuple[float, float]],
) -> tuple[float | None, float | None]:
    """Return ``(spread, mid_price)`` from best levels, or ``(None, None)``."""
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None
    if best_bid is None or best_ask is None or best_bid >= best_ask:
        return None, None
    return best_ask - best_bid, (best_bid + best_ask) / 2.0


class BookImbalanceFeature:
    """Depth-weighted OBI + book slope from the latest order book snapshot.

    Uses the most recent order book event in the snapshot window (not a
    rolling average): these are instantaneous cross-sectional signals whose
    predictive value decays within tens of seconds (integration #11).
    """

    name: ClassVar[str] = "book_imbalance"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        params = parameters or {}
        depth_levels = int(params.get("depth_levels", 10))

        start = time.perf_counter()
        books = extract_order_book_levels(snapshot)
        if not books:
            raise ValueError("BookImbalanceFeature requires at least one order book event")
        bids, asks = books[-1]
        spread, mid = spread_and_mid(bids, asks)
        slope = book_slope(bids, asks, mid_price=mid) if mid else None
        obi = order_book_imbalance(bids, asks, depth_levels=depth_levels)

        value: dict[str, Any] = {
            "obi": obi["obi"],
            "bid_weighted_size": obi["bid_weighted_size"],
            "ask_weighted_size": obi["ask_weighted_size"],
            "levels_used": obi["levels_used"],
            "spread": round(spread, 8) if spread is not None else None,
            "mid_price": round(mid, 8) if mid is not None else None,
            "book_slope": slope,
        }
        return ContextFeature(
            name=BookImbalanceFeature.name,
            value=value,
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
