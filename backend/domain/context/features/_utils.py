# backend/domain/context/features/_utils.py
"""Shared deterministic extraction helpers for context features."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def extract_prices(snapshot: ContextSnapshot) -> list[float]:
    """Extract price series from trade and ticker events in chronological order."""
    prices: list[float] = []
    for event in snapshot.events:
        price = _price_from_event(event)
        if price is not None:
            prices.append(price)
    return prices


def extract_volumes(snapshot: ContextSnapshot) -> list[float]:
    """Extract volume/quantity series from trade events."""
    volumes: list[float] = []
    for event in snapshot.events:
        if event.event_type != ObservationEventType.TRADE:
            continue
        quantity = event.payload.get("quantity")
        if isinstance(quantity, (int, float)):
            volumes.append(float(quantity))
    return volumes


def extract_order_book_levels(
    snapshot: ContextSnapshot,
) -> list[tuple[list[tuple[float, float]], list[tuple[float, float]]]]:
    """Extract bid/ask level tuples from order book events."""
    books: list[tuple[list[tuple[float, float]], list[tuple[float, float]]]] = []
    for event in snapshot.events:
        if event.event_type != ObservationEventType.ORDER_BOOK:
            continue
        bids = _parse_levels(event.payload.get("bids"))
        asks = _parse_levels(event.payload.get("asks"))
        if bids or asks:
            books.append((bids, asks))
    return books


def lookback_slice(values: Sequence[float], lookback: int) -> list[float]:
    """Return the last ``lookback`` values, or all values if fewer exist."""
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    if len(values) <= lookback:
        return list(values)
    return list(values[-lookback:])


def _price_from_event(event: ObservationEvent) -> float | None:
    payload = event.payload
    if event.event_type == ObservationEventType.TRADE:
        price = payload.get("price")
    elif event.event_type == ObservationEventType.TICKER:
        price = payload.get("last_price", payload.get("price"))
    elif event.event_type == ObservationEventType.CANDLE:
        price = payload.get("close")
    else:
        price = payload.get("price")

    if isinstance(price, (int, float)):
        return float(price)
    return None


def _parse_levels(raw: Any) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        return []
    levels: list[tuple[float, float]] = []
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            price, size = item[0], item[1]
            if isinstance(price, (int, float)) and isinstance(size, (int, float)):
                levels.append((float(price), float(size)))
    return levels
