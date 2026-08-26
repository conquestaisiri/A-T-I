"""Unit tests for InMemoryWindowManager."""

from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest
from backend.application.interfaces.context_settings import ContextSettings
from backend.application.window_manager_impl import InMemoryWindowManager


class TestWindowManager:
    def test_add_and_snapshot_returns_immutable_events(
        self, make_trade_event, test_settings: ContextSettings
    ):
        manager = InMemoryWindowManager(test_settings)
        event = make_trade_event(price=100.0)
        manager.add(event)

        snapshot = manager.snapshot("BTCUSDT")
        assert len(snapshot.events) == 1
        assert snapshot.start_timestamp == event.timestamp
        assert snapshot.end_timestamp == event.timestamp

        with pytest.raises(FrozenInstanceError):
            snapshot.events = ()  # type: ignore[misc]

    def test_events_ordered_by_timestamp(self, make_trade_event, test_settings: ContextSettings):
        manager = InMemoryWindowManager(test_settings)
        late = make_trade_event(price=102.0, offset_seconds=10, trade_id=2)
        early = make_trade_event(price=101.0, offset_seconds=1, trade_id=1)

        manager.add(late)
        manager.add(early)

        snapshot = manager.snapshot("BTCUSDT")
        assert snapshot.events[0].payload["trade_id"] == 1
        assert snapshot.events[1].payload["trade_id"] == 2

    def test_rolling_expiration(self, make_trade_event):
        settings = ContextSettings(window_duration=timedelta(seconds=30))
        manager = InMemoryWindowManager(settings)

        manager.add(make_trade_event(price=100.0, offset_seconds=0, trade_id=1))
        manager.add(make_trade_event(price=101.0, offset_seconds=20, trade_id=2))
        manager.add(make_trade_event(price=102.0, offset_seconds=60, trade_id=3))

        snapshot = manager.snapshot("BTCUSDT")
        trade_ids = [event.payload["trade_id"] for event in snapshot.events]
        assert trade_ids == [3]

    def test_clear_removes_symbol_window(self, make_trade_event, test_settings: ContextSettings):
        manager = InMemoryWindowManager(test_settings)
        manager.add(make_trade_event())
        manager.clear("BTCUSDT")

        with pytest.raises(KeyError):
            manager.snapshot("BTCUSDT")

    def test_missing_symbol_raises_key_error(self, test_settings: ContextSettings):
        manager = InMemoryWindowManager(test_settings)
        with pytest.raises(KeyError):
            manager.snapshot("ETHUSDT")

    def test_missing_symbol_in_payload_raises(
        self, make_trade_event, test_settings: ContextSettings
    ):
        manager = InMemoryWindowManager(test_settings)
        event = make_trade_event()
        broken = event.model_copy(update={"payload": {"price": 1.0}})
        with pytest.raises(KeyError):
            manager.add(broken)

    def test_concurrent_access(self, make_trade_event, test_settings: ContextSettings):
        manager = InMemoryWindowManager(test_settings)
        errors: list[Exception] = []

        def worker(trade_id: int) -> None:
            try:
                for i in range(20):
                    manager.add(
                        make_trade_event(
                            price=100.0 + trade_id,
                            offset_seconds=trade_id * 20 + i,
                            trade_id=trade_id * 100 + i,
                        )
                    )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert not errors
        snapshot = manager.snapshot("BTCUSDT")
        assert len(snapshot.events) > 0
        timestamps = [event.timestamp for event in snapshot.events]
        assert timestamps == sorted(timestamps)
