"""Tests for the HistoricalBar contract (P5-005)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.domain.research.historical_bar import HistoricalBar

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)


def bar(**overrides: Any) -> HistoricalBar:
    fields: dict[str, Any] = dict(
        timestamp=T0,
        open=100.0,
        high=105.0,
        low=99.0,
        close=104.0,
        volume=1000.0,
    )
    fields.update(overrides)
    return HistoricalBar(**fields)


def test_valid_bar_constructs():
    b = bar()
    assert b.high == 105.0
    assert b.close == 104.0


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        bar(timestamp=datetime(2026, 1, 1))


def test_non_positive_prices_rejected():
    with pytest.raises(ValueError, match="positive"):
        bar(open=0.0)
    with pytest.raises(ValueError, match="positive"):
        bar(close=-1.0)


def test_high_low_inconsistency_rejected():
    with pytest.raises(ValueError, match="high"):
        bar(high=90.0, low=99.0)
    with pytest.raises(ValueError, match="high"):
        bar(high=101.0, low=98.0)
    with pytest.raises(ValueError, match="low"):
        bar(high=105.0, low=102.0, close=99.0)


def test_negative_volume_rejected():
    with pytest.raises(ValueError, match="volume"):
        bar(volume=-1.0)


def test_as_dict_round_trips():
    original = bar()
    restored = HistoricalBar.from_dict(original.as_dict())
    assert restored == original
