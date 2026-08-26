# backend/domain/context/features\micro_price.py
"""Micro-price (Stoikov) fair-value estimator.

The micro-price is a martingale by construction (unlike mid-price which
is heavily autocorrelated). It provides a better fair-value anchor for
feature computation, PnL marking, and quoting logic.

Reference: Stoikov, S. (2018). "The micro-price: a high-frequency estimator
of future prices." Quantitative Finance.
"""

from __future__ import annotations

import time
from collections import abc
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.observation.event import ObservationEvent, ObservationEventType

# Global micro-price state per symbol
_state: dict[str, dict[str, Any]] = {}


def update_from_event(event: ObservationEvent) -> None:
    """Update micro-price state from an order book event."""
    if event.event_type is not ObservationEventType.ORDER_BOOK:
        return

    symbol = event.payload.get("symbol")
    if not symbol:
        return
    symbol = str(symbol).upper()

    bids = event.payload.get("bids", [])
    asks = event.payload.get("asks", [])

    if not bids or not asks:
        return

    import math as _math

    try:
        best_bid = float(bids[0][0]) if bids else 0.0
        best_ask = float(asks[0][0]) if asks else 0.0
        bid_size = float(bids[0][1]) if bids and len(bids[0]) > 1 else 0.0
        ask_size = float(asks[0][1]) if asks and len(asks[0]) > 1 else 0.0
    except (ValueError, TypeError, IndexError):
        return

    if (
        best_bid <= 0
        or best_ask <= 0
        or not _math.isfinite(best_bid)
        or not _math.isfinite(best_ask)
    ):
        return
    if not _math.isfinite(bid_size) or not _math.isfinite(ask_size):
        return

    mid = (best_bid + best_ask) / 2.0
    spread = best_ask - best_bid

    # Weighted mid (micro-price approximation)
    total_size = bid_size + ask_size
    micro = (best_bid * ask_size + best_ask * bid_size) / total_size if total_size > 0 else mid

    # Imbalance
    imbalance = (bid_size - ask_size) / total_size if total_size > 0 else 0.0

    _state[symbol] = {
        "micro_price": micro,
        "mid_price": mid,
        "spread": spread,
        "imbalance": imbalance,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "timestamp": event.timestamp,
    }


def get_state(symbol: str) -> dict[str, Any] | None:
    """Get current micro-price state for a symbol."""
    return _state.get(symbol)


def reset_state() -> None:
    """Clear all per-symbol micro-price state.

    Called when a fresh context pipeline is built so a replay of the same
    events produces identical contexts (ADR 0007). Without this, two pipelines
    built in the same process leak order-book state into each other.
    """
    _state.clear()


class MicroPriceFeature:
    """Micro-price (Stoikov) fair-value feature.

    Provides micro-price, mid-price, spread, and imbalance from the
    latest order book state.
    """

    name: ClassVar[str] = "micro_price"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: abc.Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        """Read micro-price from global state."""
        params = parameters or {}
        symbol = params.get("symbol", "BTC").upper()

        start = time.perf_counter()
        state = _state.get(symbol)

        if state is None:
            return ContextFeature(
                name=MicroPriceFeature.name,
                value={
                    "micro_price": None,
                    "mid_price": None,
                    "spread": None,
                    "imbalance": 0.0,
                    "cache_status": "cold",
                },
                computation_timestamp=snapshot.end_timestamp,
                execution_time=time.perf_counter() - start,
            )

        return ContextFeature(
            name=MicroPriceFeature.name,
            value={
                "micro_price": state["micro_price"],
                "mid_price": state["mid_price"],
                "spread": state["spread"],
                "imbalance": state["imbalance"],
                "best_bid": state["best_bid"],
                "best_ask": state["best_ask"],
                "cache_status": "warm",
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
