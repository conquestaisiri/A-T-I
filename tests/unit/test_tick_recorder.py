"""Unit tests for the JSONL tick recorder (task P0-006).

Recorded data must be readable without pickle or numpy, the record schema must
include old/new size and sequence, legacy ``.npz`` captures must be quarantined
from the active path, and migration must be explicit and safe.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import numpy as np
import pytest
from backend.application.validation.tick_recorder import (
    TickRecorder,
    load_tick_data,
    migrate_legacy_tick_data,
)
from backend.domain.observation.event import ObservationEvent, ObservationEventType

EXPECTED_FIELDS = {
    "timestamp",
    "symbol",
    "side",
    "price",
    "old_size",
    "new_size",
    "size",
    "action",
    "sequence",
}


def _delta_event(
    ts: datetime,
    *,
    symbol: str = "btcusdt",
    delta_seq: int | None = 1,
    bids: list[dict] | None = None,
    asks: list[dict] | None = None,
) -> ObservationEvent:
    return ObservationEvent(
        source_id="ccxt",
        source_name="CCXT",
        event_type=ObservationEventType.ORDER_BOOK,
        timestamp=ts,
        payload={
            "symbol": symbol,
            "delta": True,
            "delta_seq": delta_seq,
            "bids": bids if bids is not None else [{"price": 100.0, "size": 5.0, "action": "add"}],
            "asks": asks if asks is not None else [],
        },
    )


def test_round_trip_and_schema(tmp_path: Path) -> None:
    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    recorder.record_event(
        _delta_event(
            now,
            bids=[
                {
                    "price": 100.0,
                    "old_size": 5.0,
                    "new_size": 7.0,
                    "action": "update",
                }
            ],
            asks=[
                {"price": 100.5, "old_size": 4.0, "new_size": 0.0, "action": "remove"},
            ],
        )
    )
    recorder.close()

    records = load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15))
    assert len(records) == 2

    bid, ask = records
    assert set(bid) == EXPECTED_FIELDS
    assert bid["timestamp"] == now.timestamp()
    assert bid["symbol"] == "btcusdt"
    assert bid["side"] == "bid"
    assert bid["price"] == 100.0
    assert bid["old_size"] == 5.0
    assert bid["new_size"] == 7.0
    assert bid["size"] == 7.0
    assert bid["action"] == "update"
    assert bid["sequence"] == 1

    assert ask["side"] == "ask"
    assert ask["old_size"] == 4.0
    assert ask["new_size"] == 0.0
    assert ask["action"] == "remove"


def test_no_pickle_or_numpy_required_to_read(tmp_path: Path) -> None:
    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    recorder.record_event(_delta_event(now))
    recorder.close()

    files = list((tmp_path / "btcusdt").glob("*.jsonl"))
    assert len(files) == 1
    # Every line must be plain JSON — read with the stdlib only.
    text = files[0].read_text(encoding="utf-8").strip()
    assert text, "expected at least one record line"
    for line in text.splitlines():
        json.loads(line)

    # The active data path must contain no numpy captures.
    assert not list((tmp_path / "btcusdt").glob("*.npz"))


def test_unknown_sizes_recorded_as_null(tmp_path: Path) -> None:
    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    recorder.record_event(
        _delta_event(
            now,
            bids=[{"price": 100.0, "size": 5.0, "action": "add"}],
            delta_seq=None,
        )
    )
    recorder.close()

    (record,) = load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15))
    assert record["old_size"] is None
    assert record["new_size"] == 5.0
    assert record["sequence"] is None


def test_multiple_flushes_append_to_same_file(tmp_path: Path) -> None:
    recorder = TickRecorder(data_dir=tmp_path, buffer_size=1)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    for price in (100.0, 101.0, 102.0):
        recorder.record_event(
            _delta_event(
                now,
                bids=[{"price": price, "size": 1.0, "action": "add"}],
            )
        )
    recorder.close()

    records = load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15))
    assert [r["price"] for r in records] == [100.0, 101.0, 102.0]


def test_legacy_npz_quarantined_on_write(tmp_path: Path) -> None:
    symbol_dir = tmp_path / "btcusdt"
    symbol_dir.mkdir(parents=True)
    legacy = symbol_dir / "2026-01-15.npz"
    np.savez_compressed(
        legacy,
        data=np.array(
            [{"timestamp": 1.0, "symbol": "btcusdt", "side": "bid", "price": 1.0}],
            dtype=object,
        ),
    )

    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    recorder.record_event(_delta_event(now))
    recorder.close()

    # The active path holds only JSONL; the legacy capture was quarantined.
    assert (symbol_dir / "2026-01-15.jsonl").is_file()
    assert not (symbol_dir / "2026-01-15.npz").exists()
    assert (symbol_dir / "2026-01-15.npz.legacy").is_file()

    records = load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15))
    assert len(records) >= 1


def test_explicit_migration_from_legacy_npz(tmp_path: Path) -> None:
    symbol_dir = tmp_path / "btcusdt"
    symbol_dir.mkdir(parents=True)
    legacy_records = [
        {"timestamp": 100.0, "symbol": "btcusdt", "side": "bid", "price": 10.0},
        {"timestamp": 101.0, "symbol": "btcusdt", "side": "ask", "price": 10.5},
    ]
    # numpy appends ".npz" unless the path already ends with it, so save to a
    # plain ".npz" first, then rename into the quarantined legacy name.
    tmp_src = symbol_dir / "2026-01-15.npz"
    np.savez_compressed(tmp_src, data=np.array(legacy_records, dtype=object))
    tmp_src.rename(symbol_dir / "2026-01-15.npz.legacy")

    migrated = migrate_legacy_tick_data(tmp_path, "btcusdt")
    assert migrated == 1

    records = load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15))
    assert records == legacy_records
    assert not (symbol_dir / "2026-01-15.npz.legacy").exists()
    assert (symbol_dir / "2026-01-15.npz.migrated").is_file()


def test_load_empty_and_missing_symbol(tmp_path: Path) -> None:
    assert load_tick_data(tmp_path, "nope", date(2026, 1, 1), date(2026, 1, 2)) == []

    symbol_dir = tmp_path / "btcusdt"
    symbol_dir.mkdir(parents=True)
    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    recorder.record_event(_delta_event(now))
    recorder.close()

    # Dates outside the recorded day return nothing.
    assert load_tick_data(tmp_path, "btcusdt", date(2026, 1, 1), date(2026, 1, 2)) == []
    assert len(load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15))) >= 1


def test_get_stats_counts_jsonl_files(tmp_path: Path) -> None:
    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    recorder.record_event(_delta_event(now))
    recorder.close()

    stats = recorder.get_stats()
    assert "btcusdt" in stats
    assert stats["btcusdt"]["files"] >= 1
    assert stats["btcusdt"]["total_bytes"] > 0


def test_non_delta_and_non_book_events_ignored(tmp_path: Path) -> None:
    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

    snapshot = ObservationEvent(
        source_id="ccxt",
        source_name="CCXT",
        event_type=ObservationEventType.ORDER_BOOK,
        timestamp=now,
        payload={"symbol": "btcusdt", "bids": [[100.0, 5.0]], "asks": [[100.5, 4.0]]},
    )
    trade = ObservationEvent(
        source_id="ccxt",
        source_name="CCXT",
        event_type=ObservationEventType.TRADE,
        timestamp=now,
        payload={"symbol": "btcusdt", "price": 100.0},
    )
    recorder.record_event(snapshot)
    recorder.record_event(trade)
    recorder.close()

    assert load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15)) == []
    assert recorder.get_stats() == {}


@pytest.mark.parametrize("action", ["add", "update", "remove"])
def test_all_actions_round_trip(tmp_path: Path, action: str) -> None:
    recorder = TickRecorder(data_dir=tmp_path)
    now = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
    recorder.record_event(
        _delta_event(
            now, bids=[{"price": 100.0, "old_size": 1.0, "new_size": 2.0, "action": action}]
        )
    )
    recorder.close()

    (record,) = load_tick_data(tmp_path, "btcusdt", date(2026, 1, 15), date(2026, 1, 15))
    assert record["action"] == action
