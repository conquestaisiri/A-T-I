"""Tests for the public Binance klines fetcher (real-data capture).

The fetcher is the research institution's data-acquisition arm: public
Binance klines -> validated HistoricalBars -> the operator console freezes
them. Tests cover row parsing, the still-forming-candle drop, pagination,
error surfacing, and validation failures — all with a mocked transport so
no network is touched.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from backend.application.research.binance_klines_fetcher import (
    BINANCE_MAX_PAGES,
    BinanceKlinesFetcher,
)

OPEN_MS = int(datetime(2026, 1, 1, 0, 0, tzinfo=UTC).timestamp() * 1000)
HOUR_MS = 3_600_000


def kline_row(index: int, *, close: float | None = None) -> list:
    open_ms = OPEN_MS + index * HOUR_MS
    close = close if close is not None else 100.0 + index
    return [
        open_ms,
        str(close - 0.5),
        str(close + 1.0),
        str(close - 1.0),
        str(close),
        "10.5",
        open_ms + HOUR_MS - 1,
        "0",
        10,
        "5.0",
        "0",
        "0",
    ]


def make_client(rows_by_page: list[list[list]]) -> httpx.Client:
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(dict(request.url.params))
        page = rows_by_page[min(len(calls) - 1, len(rows_by_page) - 1)]
        return httpx.Response(200, json=page)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_parses_rows_into_validated_ascending_bars():
    client = make_client([[kline_row(i) for i in range(3)]])
    fetcher = BinanceKlinesFetcher(client=client)
    bars = fetcher.fetch("BTCUSDT", interval="1h", limit=1000, drop_incomplete=False)
    assert [b.close for b in bars] == [100.0, 101.0, 102.0]
    assert bars[0].open == 99.5
    assert bars[0].high == 101.0
    assert bars[0].low == 99.0
    assert bars[0].volume == 10.5
    assert bars[0].timestamp == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert bars[1].timestamp == datetime(2026, 1, 1, 1, 0, tzinfo=UTC)


def test_drops_still_forming_candle_by_default():
    client = make_client([[kline_row(i) for i in range(3)]])
    fetcher = BinanceKlinesFetcher(client=client)
    # The third bar's interval is still open at the injected "now".
    now = datetime(2026, 1, 1, 2, 30, tzinfo=UTC)
    bars = fetcher.fetch("BTCUSDT", interval="1h", now=now)
    assert len(bars) == 2
    assert bars[-1].close == 101.0


def test_keep_incomplete_retains_last_bar():
    client = make_client([[kline_row(i) for i in range(3)]])
    fetcher = BinanceKlinesFetcher(client=client)
    now = datetime(2026, 1, 1, 2, 30, tzinfo=UTC)
    bars = fetcher.fetch("BTCUSDT", interval="1h", drop_incomplete=False, now=now)
    assert len(bars) == 3


def test_paginates_when_start_and_end_span_more_than_limit():
    rows = [kline_row(i) for i in range(2500)]
    client = make_client([rows[:1000], rows[1000:2000], rows[2000:]])
    fetcher = BinanceKlinesFetcher(client=client)
    bars = fetcher.fetch(
        "BTCUSDT",
        interval="1h",
        limit=1000,
        start_time=datetime(2026, 1, 1, tzinfo=UTC),
        end_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=2500),
        drop_incomplete=False,
    )
    assert len(bars) == 2500
    assert bars[0].timestamp == datetime(2026, 1, 1, tzinfo=UTC)
    assert bars[-1].timestamp == datetime(2026, 1, 1, 0, 0, tzinfo=UTC) + timedelta(hours=2499)


def test_no_pagination_without_end_time():
    client = make_client([[kline_row(i) for i in range(3)]])
    fetcher = BinanceKlinesFetcher(client=client)
    bars = fetcher.fetch("BTCUSDT", interval="1h", drop_incomplete=False)
    assert len(bars) == 3


def test_http_error_surfaces_with_symbol():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = BinanceKlinesFetcher(client=client)
    with pytest.raises(RuntimeError, match="BTCUSDT"):
        fetcher.fetch("BTCUSDT", interval="1h")


def test_empty_page_raises():
    client = make_client([[]])
    fetcher = BinanceKlinesFetcher(client=client)
    with pytest.raises(ValueError, match="no klines"):
        fetcher.fetch("BTCUSDT", interval="1h")


def test_malformed_row_raises_value_error():
    client = make_client([[[OPEN_MS, "oops", "h", "l", "c", "v"]]])
    fetcher = BinanceKlinesFetcher(client=client)
    with pytest.raises(ValueError, match="invalid Binance kline row"):
        fetcher.fetch("BTCUSDT", interval="1h")


def test_unsorted_rows_rejected():
    rows = [kline_row(1), kline_row(0)]
    client = make_client([rows])
    fetcher = BinanceKlinesFetcher(client=client)
    with pytest.raises(ValueError, match="not strictly ascending"):
        fetcher.fetch("BTCUSDT", interval="1h")


def test_invalid_symbol_rejected():
    fetcher = BinanceKlinesFetcher(client=make_client([[kline_row(0)]]))
    with pytest.raises(ValueError, match="symbol"):
        fetcher.fetch("", interval="1h")


def test_invalid_interval_rejected():
    fetcher = BinanceKlinesFetcher(client=make_client([[kline_row(0)]]))
    with pytest.raises(ValueError, match="interval"):
        fetcher.fetch("BTCUSDT", interval="fortnightly")


def test_naive_start_time_rejected():
    fetcher = BinanceKlinesFetcher(client=make_client([[kline_row(0)]]))
    with pytest.raises(ValueError, match="timezone-aware"):
        fetcher.fetch("BTCUSDT", interval="1h", start_time=datetime(2026, 1, 1))


def test_pagination_cap_raises_runtime_error():
    rows = [kline_row(i) for i in range(1000)]
    client = make_client([rows] * (BINANCE_MAX_PAGES + 1))
    fetcher = BinanceKlinesFetcher(client=client)
    with pytest.raises(RuntimeError, match="pagination"):
        fetcher.fetch(
            "BTCUSDT",
            interval="1h",
            start_time=datetime(2026, 1, 1, tzinfo=UTC),
            end_time=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=1000),
            drop_incomplete=False,
        )
