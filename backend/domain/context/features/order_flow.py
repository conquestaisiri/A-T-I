# backend/domain/context/features/order_flow.py
"""Order Flow Imbalance (OFI) feature from L2 deltas.

Computes multi-level OFI from order book delta events (Cont 2014).
Best-level OFI explains 65% of mid-price variance; integrated OFI (PCA
across levels) explains 87% (Cont-Cucuringu-Zhang 2023).

The single highest-R² microstructure variable in the literature.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.observation.event import ObservationEvent


def resolve_delta_sizes(delta: Mapping[str, Any]) -> tuple[float, float]:
    """Resolve ``(old_size, new_size)`` from a delta entry.

    Modern delta entries carry ``old_size``/``new_size`` explicitly. Legacy
    entries carrying only ``size`` + ``action`` are resolved by convention:
    add -> (0, size), remove -> (size, 0), update -> (size, size) because the
    previous size is unknown (a size-preserving update contributes zero OFI).
    """
    if "old_size" in delta and "new_size" in delta:
        return float(delta["old_size"]), float(delta["new_size"])
    size = float(delta.get("size", 0.0))
    action = delta.get("action", "")
    if action == "remove":
        return size, 0.0
    if action == "update":
        return size, size
    return 0.0, size


# OFI state tracker (module-level, updated by event consumer)
class OFITracker:
    """Tracks order flow imbalance from L2 delta events.

    Maintains a reconstructed per-symbol order book (seeded by full snapshots,
    updated by deltas) so that each price change is attributed to its true
    level relative to the current best bid/ask, and computes rolling
    statistics over a sliding time window.

    OFI contribution of a delta is ``±(new_size - old_size)``: positive for
    bid increases / ask decreases (buy pressure), negative for the converse.
    """

    def __init__(self, *, window_seconds: int = 60, max_levels: int = 10) -> None:
        self._window = window_seconds
        self._max_levels = max_levels
        # Per-symbol list of (timestamp, level, ofi_value)
        self._events: dict[str, list[tuple[float, int, float]]] = defaultdict(list)
        # Reconstructed book: {symbol: {"bids": {price: size}, "asks": {price: size}}}
        self._book: dict[str, dict[str, dict[float, float]]] = defaultdict(
            lambda: {"bids": {}, "asks": {}}
        )

    def set_book(
        self,
        symbol: str,
        bids: list[list[float]],
        asks: list[list[float]],
        timestamp: float | None = None,
    ) -> None:
        """Replace the reconstructed book with a full snapshot.

        Snapshots are the source of truth; deltas only ever apply on top of
        the most recent snapshot so levels stay anchored to the real book.
        """
        book = self._book[symbol]
        book["bids"] = {float(p): float(s) for p, s, *_ in bids}
        book["asks"] = {float(p): float(s) for p, s, *_ in asks}
        if timestamp is not None:
            self._prune(symbol, timestamp)

    def add_delta_event(
        self, symbol: str, timestamp: float, deltas: dict[str, list[dict[str, Any]]]
    ) -> None:
        """Apply an order book delta event and record its OFI contribution."""
        book = self._book[symbol]
        for side in ("bids", "asks"):
            side_book = book[side]
            for delta in deltas.get(side, []):
                price = float(delta.get("price", 0.0))
                old_size, new_size = resolve_delta_sizes(delta)

                if old_size > 0:
                    # update/remove: the price is still present, rank it now
                    level = self._rank_of(symbol, side, price)
                else:
                    # add: apply first so the new price participates in ranking
                    if new_size > 0:
                        side_book[price] = new_size
                    level = self._rank_of(symbol, side, price)

                if new_size > 0:
                    side_book[price] = new_size
                elif price in side_book:
                    del side_book[price]

                ofi = self._ofi_change(side, old_size, new_size)
                if ofi != 0 and level < self._max_levels:
                    self._events[symbol].append((timestamp, level, ofi))

        # Prune old events
        self._prune(symbol, timestamp)

    def get_ofi(self, symbol: str) -> dict[str, Any]:
        """Get current OFI statistics for a symbol."""
        events = self._events.get(symbol, [])
        if not events:
            return {
                "best_level_ofi": 0.0,
                "integrated_ofi": 0.0,
                "level_count": 0,
                "event_count": 0,
            }

        # Best-level OFI (level 0)
        best_ofi = sum(ofi for _, level, ofi in events if level == 0)

        # Integrated OFI (sum across all levels, weighted by 1/level)
        integrated = 0.0
        for _, level, ofi in events:
            weight = 1.0 / (level + 1)  # level 0 = weight 1, level 1 = 0.5, etc.
            integrated += ofi * weight

        return {
            "best_level_ofi": round(best_ofi, 6),
            "integrated_ofi": round(integrated, 6),
            "level_count": self._max_levels,
            "event_count": len(events),
        }

    @staticmethod
    def _ofi_change(side: str, old_size: float, new_size: float) -> float:
        """OFI contribution of a size change: ±(new_size - old_size)."""
        change = new_size - old_size
        if side == "bids":
            return change
        return -change

    def _rank_of(self, symbol: str, side: str, price: float) -> int:
        """Rank of ``price`` on its side: 0 is best bid (highest) / ask (lowest)."""
        side_book = self._book[symbol][side]
        if not side_book:
            return self._max_levels  # not found -> outside window, ignore
        prices = sorted(side_book, reverse=(side == "bids"))
        try:
            return prices.index(price)
        except ValueError:
            return self._max_levels

    def _prune(self, symbol: str, current_time: float) -> None:
        """Remove events older than window."""
        cutoff = current_time - self._window
        self._events[symbol] = [
            (t, level, o) for t, level, o in self._events[symbol] if t >= cutoff
        ]


# Global tracker instance
_tracker: OFITracker | None = None


def get_ofi_tracker() -> OFITracker | None:
    """Get the global OFI tracker."""
    return _tracker


def set_ofi_tracker(tracker: OFITracker | None) -> None:
    """Set (or clear) the global OFI tracker."""
    global _tracker
    _tracker = tracker


class OrderFlowFeature:
    """Order Flow Imbalance feature from L2 deltas.

    Computes best-level and integrated OFI from order book delta events.
    Best-level OFI explains 65% of mid-price variance; integrated OFI
    (PCA across levels) explains 87% (Cont 2014/2023).
    """

    name: ClassVar[str] = "order_flow"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        """Read OFI from the global tracker cache."""
        params = parameters or {}
        symbol = params.get("symbol", "BTC").upper()

        start = time.perf_counter()
        tracker = get_ofi_tracker()

        if tracker is None:
            return ContextFeature(
                name=OrderFlowFeature.name,
                value={
                    "best_level_ofi": 0.0,
                    "integrated_ofi": 0.0,
                    "event_count": 0,
                    "cache_status": "unavailable",
                },
                computation_timestamp=snapshot.end_timestamp,
                execution_time=time.perf_counter() - start,
            )

        ofi = tracker.get_ofi(symbol)

        return ContextFeature(
            name=OrderFlowFeature.name,
            value={
                "best_level_ofi": ofi["best_level_ofi"],
                "integrated_ofi": ofi["integrated_ofi"],
                "event_count": ofi["event_count"],
                "cache_status": "warm" if ofi["event_count"] > 0 else "cold",
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )


def process_observation_event(event: ObservationEvent) -> None:
    """Feed one ORDER_BOOK event into the OFI tracker.

    Snapshots seed the reconstructed book (source of truth); delta events
    apply on top and record level-aware OFI contributions.
    """
    tracker = get_ofi_tracker()
    if tracker is None:
        return

    symbol = str(event.payload.get("symbol", "UNKNOWN")).upper()
    timestamp = event.timestamp.timestamp()

    if event.payload.get("delta", False):
        deltas = {"bids": event.payload.get("bids", []), "asks": event.payload.get("asks", [])}
        tracker.add_delta_event(symbol, timestamp, deltas)
    else:
        tracker.set_book(
            symbol,
            event.payload.get("bids", []),
            event.payload.get("asks", []),
            timestamp,
        )
