"""Unit tests for the level-aware order flow imbalance (OFI) feature.

P0-005: OFI must be computed from mathematically correct deltas — each price
change contributes ``±(new_size - old_size)`` at its true book level relative
to the current best bid/ask. Expected values below are hand-calculated.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.domain.context.features.order_flow import (
    OFITracker,
    process_observation_event,
    resolve_delta_sizes,
    set_ofi_tracker,
)
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def test_resolve_delta_sizes_legacy_schema() -> None:
    assert resolve_delta_sizes({"price": 1.0, "size": 5.0, "action": "add"}) == (0.0, 5.0)
    assert resolve_delta_sizes({"price": 1.0, "size": 5.0, "action": "remove"}) == (5.0, 0.0)
    # Legacy update carries no previous size: treated as size-preserving (zero OFI).
    assert resolve_delta_sizes({"price": 1.0, "size": 5.0, "action": "update"}) == (5.0, 5.0)
    # Modern schema wins.
    assert resolve_delta_sizes({"old_size": 2.0, "new_size": 7.0, "action": "update"}) == (
        2.0,
        7.0,
    )


def test_level_aware_ofi_hand_calculated() -> None:
    tracker = OFITracker(window_seconds=60, max_levels=10)
    tracker.set_book(
        "BTCUSDT",
        bids=[[100.0, 5.0], [99.0, 3.0], [98.0, 2.0]],
        asks=[[101.0, 4.0], [102.0, 2.0], [103.0, 1.0]],
    )

    # Bid add at 100.5 -> new best bid, +6 @ level 0.
    # Ask remove at 101.0 (best ask) -> +(4 - 0) @ level 0.
    tracker.add_delta_event(
        "BTCUSDT",
        1000.0,
        {
            "bids": [{"price": 100.5, "old_size": 0.0, "new_size": 6.0, "action": "add"}],
            "asks": [{"price": 101.0, "old_size": 4.0, "new_size": 0.0, "action": "remove"}],
        },
    )

    ofi = tracker.get_ofi("BTCUSDT")
    assert ofi["best_level_ofi"] == pytest.approx(6.0 + 4.0)
    assert ofi["integrated_ofi"] == pytest.approx(10.0)
    assert ofi["event_count"] == 2

    # Bid update at 99.0 -> rank 2 among bids {100.5, 100.0, 99.0, 98.0},
    #   contributes +2 weighted by 1/3.
    # Ask update at 102.0 -> rank 0 among asks {102.0, 103.0}, contributes -(3 - 2) = -1.
    tracker.add_delta_event(
        "BTCUSDT",
        1001.0,
        {
            "bids": [{"price": 99.0, "old_size": 3.0, "new_size": 5.0, "action": "update"}],
            "asks": [{"price": 102.0, "old_size": 2.0, "new_size": 3.0, "action": "update"}],
        },
    )

    ofi = tracker.get_ofi("BTCUSDT")
    assert ofi["best_level_ofi"] == pytest.approx(6.0 + 4.0 - 1.0)
    assert ofi["integrated_ofi"] == pytest.approx(6.0 + 4.0 - 1.0 + 2.0 / 3.0)
    assert ofi["event_count"] == 4


def test_ofi_sign_convention() -> None:
    tracker = OFITracker()
    tracker.set_book("BTCUSDT", [], [])

    # Bid add +2, ask add -3 -> -1.
    tracker.add_delta_event(
        "BTCUSDT",
        0.0,
        {
            "bids": [{"price": 1.0, "old_size": 0.0, "new_size": 2.0, "action": "add"}],
            "asks": [{"price": 2.0, "old_size": 0.0, "new_size": 3.0, "action": "add"}],
        },
    )
    assert tracker.get_ofi("BTCUSDT")["best_level_ofi"] == pytest.approx(-1.0)

    # Removing reverses the sign: bid remove -2, ask remove +3 -> +1; total 0.
    tracker.add_delta_event(
        "BTCUSDT",
        1.0,
        {
            "bids": [{"price": 1.0, "old_size": 2.0, "new_size": 0.0, "action": "remove"}],
            "asks": [{"price": 2.0, "old_size": 3.0, "new_size": 0.0, "action": "remove"}],
        },
    )
    assert tracker.get_ofi("BTCUSDT")["best_level_ofi"] == pytest.approx(0.0)


def test_legacy_delta_schema_fallback() -> None:
    tracker = OFITracker()
    tracker.add_delta_event(
        "BTCUSDT",
        0.0,
        {
            "bids": [{"price": 100.0, "size": 5.0, "action": "add"}],
            "asks": [{"price": 100.5, "size": 4.0, "action": "add"}],
        },
    )
    assert tracker.get_ofi("BTCUSDT")["best_level_ofi"] == pytest.approx(1.0)

    tracker.add_delta_event(
        "BTCUSDT",
        1.0,
        {"bids": [{"price": 100.0, "size": 5.0, "action": "remove"}]},
    )
    assert tracker.get_ofi("BTCUSDT")["best_level_ofi"] == pytest.approx(1.0 + (-5.0))


def test_out_of_depth_levels_ignored() -> None:
    tracker = OFITracker(max_levels=3)
    tracker.set_book("BTCUSDT", bids=[[100.0, 5.0], [99.0, 3.0], [98.0, 2.0], [97.0, 1.0]], asks=[])

    # Added at rank 4 -> beyond configured depth, not tracked.
    tracker.add_delta_event(
        "BTCUSDT",
        0.0,
        {"bids": [{"price": 96.5, "old_size": 0.0, "new_size": 4.0, "action": "add"}]},
    )
    assert tracker.get_ofi("BTCUSDT")["event_count"] == 0

    # New best bid still tracked at level 0.
    tracker.add_delta_event(
        "BTCUSDT",
        1.0,
        {"bids": [{"price": 100.5, "old_size": 0.0, "new_size": 6.0, "action": "add"}]},
    )
    ofi = tracker.get_ofi("BTCUSDT")
    assert ofi["event_count"] == 1
    assert ofi["best_level_ofi"] == pytest.approx(6.0)


def test_window_prunes_old_events() -> None:
    tracker = OFITracker(window_seconds=60)
    tracker.set_book("BTCUSDT", [], [])
    tracker.add_delta_event(
        "BTCUSDT",
        100.0,
        {"bids": [{"price": 1.0, "old_size": 0.0, "new_size": 5.0, "action": "add"}]},
    )
    tracker.add_delta_event(
        "BTCUSDT",
        150.0,
        {"bids": [{"price": 2.0, "old_size": 0.0, "new_size": 3.0, "action": "add"}]},
    )
    # At t=210 the cutoff is 150: only the t=150 event survives.
    tracker.add_delta_event("BTCUSDT", 210.0, {"bids": [], "asks": []})

    ofi = tracker.get_ofi("BTCUSDT")
    assert ofi["event_count"] == 1
    assert ofi["best_level_ofi"] == pytest.approx(3.0)


def test_snapshot_resync_replaces_book() -> None:
    tracker = OFITracker()
    tracker.set_book("BTCUSDT", bids=[[100.0, 5.0]], asks=[])
    tracker.add_delta_event(
        "BTCUSDT",
        0.0,
        {"bids": [{"price": 100.0, "old_size": 5.0, "new_size": 0.0, "action": "remove"}]},
    )
    assert tracker.get_ofi("BTCUSDT")["event_count"] == 1

    # A fresh snapshot replaces the reconstructed book and prunes stale events.
    tracker.set_book("BTCUSDT", bids=[[100.0, 8.0]], asks=[[101.0, 2.0]], timestamp=1000.0)
    assert tracker.get_ofi("BTCUSDT")["event_count"] == 0

    tracker.add_delta_event(
        "BTCUSDT",
        1001.0,
        {"asks": [{"price": 101.0, "old_size": 2.0, "new_size": 1.0, "action": "update"}]},
    )
    assert tracker.get_ofi("BTCUSDT")["best_level_ofi"] == pytest.approx(1.0)


def _order_book_event(ts: datetime, *, bids, asks, delta: bool = False) -> ObservationEvent:
    payload = {"symbol": "btcusdt", "bids": bids, "asks": asks}
    if delta:
        payload["delta"] = True
    return ObservationEvent(
        source_id="ccxt",
        source_name="CCXT",
        event_type=ObservationEventType.ORDER_BOOK,
        timestamp=ts,
        payload=payload,
    )


def test_process_observation_event_dispatches_snapshot_and_delta() -> None:
    tracker = OFITracker()
    set_ofi_tracker(tracker)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    try:
        # Snapshot seeds the reconstructed book.
        process_observation_event(_order_book_event(now, bids=[[100.0, 5.0]], asks=[[101.0, 4.0]]))
        # Delta applies on top; removing the best bid contributes -5 at level 0.
        process_observation_event(
            _order_book_event(
                now,
                bids=[{"price": 100.0, "old_size": 5.0, "new_size": 0.0, "action": "remove"}],
                asks=[],
                delta=True,
            )
        )
        assert tracker.get_ofi("BTCUSDT")["best_level_ofi"] == pytest.approx(-5.0)
    finally:
        set_ofi_tracker(None)
