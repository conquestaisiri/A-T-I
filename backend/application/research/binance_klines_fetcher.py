# backend/application/research/binance_klines_fetcher.py
"""Public Binance klines fetch (real-data capture for P5-005/T1-8-1).

The operator console needs real market history to run an honest evidence
run. This fetcher pulls historical OHLCV klines from Binance's public
market-data REST endpoint — no authentication, no keys — and returns a
validated :class:`HistoricalBar` series ready for the
:class:`HistoricalDataIngestor`.

Research-only: nothing in the live path imports this module. It is the
data-acquisition arm of the research institution.

Honesty rules enforced here:
- the still-forming candle is dropped (a partially-known bar must never
  enter a dataset);
- every bar passes the :class:`HistoricalBar` contract (positive prices,
  high/low consistency, aware UTC timestamps);
- timestamps must be strictly ascending (mirrors the ingestor);
- pagination advances by ``startTime`` in capped steps, so a runaway
  request loop is structurally impossible.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from backend.domain.research.historical_bar import HistoricalBar

BINANCE_KLINES_URL = "https://data-api.binance.vision"
BINANCE_KLINES_PATH = "/api/v3/klines"
BINANCE_MAX_LIMIT = 1000
BINANCE_MAX_PAGES = 100

INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "2h": 7200,
    "4h": 14400,
    "6h": 21600,
    "8h": 28800,
    "12h": 43200,
    "1d": 86400,
    "3d": 259200,
    "1w": 604800,
    "1M": 2592000,
}


class BinanceKlinesFetcher:
    """Fetch validated historical klines from the public Binance API."""

    def __init__(
        self,
        *,
        base_url: str = BINANCE_KLINES_URL,
        client: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(timeout=httpx.Timeout(timeout))
        self._timeout = timeout

    def fetch(
        self,
        symbol: str,
        *,
        interval: str = "1h",
        limit: int = BINANCE_MAX_LIMIT,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        drop_incomplete: bool = True,
        now: datetime | None = None,
    ) -> list[HistoricalBar]:
        """Return validated ascending OHLCV bars for ``symbol``.

        Parameters
        ----------
        symbol:
            Binance spot symbol, e.g. ``BTCUSDT``.
        interval:
            Binance kline interval (``1m``, ``1h``, ``1d``, ...).
        limit:
            Bars per request (max 1000). When ``start_time``/``end_time``
            span more than ``limit`` bars the fetcher pages automatically.
        start_time:
            Inclusive open-time bound (UTC).
        end_time:
            Exclusive open-time bound (UTC).
        drop_incomplete:
            Drop the final bar if its interval has not yet closed.
        now:
            Clock override used to judge whether the final bar is complete
            (defaults to the real UTC now; tests inject a fixed value).
        """
        if not symbol or not isinstance(symbol, str):
            raise ValueError("symbol must be a non-empty string")
        if interval not in INTERVAL_SECONDS:
            raise ValueError(f"unsupported kline interval {interval!r}")
        if limit < 1 or limit > BINANCE_MAX_LIMIT:
            raise ValueError(f"limit must be in [1, {BINANCE_MAX_LIMIT}]")
        if start_time is not None and start_time.tzinfo is None:
            raise ValueError("start_time must be timezone-aware (UTC)")
        if end_time is not None and end_time.tzinfo is None:
            raise ValueError("end_time must be timezone-aware (UTC)")

        interval_seconds = INTERVAL_SECONDS[interval]
        params: dict[str, Any] = {"symbol": symbol, "interval": interval}
        if start_time is not None:
            params["startTime"] = int(start_time.timestamp() * 1000)
        if end_time is not None:
            params["endTime"] = int(end_time.timestamp() * 1000)

        rows: list[list[Any]] = []
        for _ in range(BINANCE_MAX_PAGES):
            page_params = dict(params)
            if rows:
                next_open = rows[-1][0] + interval_seconds * 1000
                page_params["startTime"] = next_open
            page_rows = self._request_kline_page(symbol, page_params, limit)
            if not page_rows:
                break
            rows.extend(page_rows)
            if len(page_rows) < limit or end_time is None:
                break
        else:
            raise RuntimeError(
                f"kline pagination exceeded {BINANCE_MAX_PAGES} requests "
                f"({symbol} {interval}); tighten start_time/end_time"
            )

        if not rows:
            raise ValueError(f"no klines returned for {symbol} {interval}")

        bars = [_kline_to_bar(row) for row in rows]
        for index in range(1, len(bars)):
            if bars[index].timestamp <= bars[index - 1].timestamp:
                raise ValueError("kline series is not strictly ascending")
        if drop_incomplete and bars:
            current = now if now is not None else datetime.now(UTC)
            if bars[-1].timestamp + timedelta(seconds=interval_seconds) > current:
                bars.pop()
        if not bars:
            raise ValueError(f"kline series for {symbol} {interval} contains no closed bars")
        return bars

    def _request_kline_page(
        self, symbol: str, params: dict[str, Any], limit: int
    ) -> list[list[Any]]:
        page_params = dict(params)
        page_params["limit"] = limit
        try:
            response = self._client.get(
                f"{self._base_url}{BINANCE_KLINES_PATH}",
                params=page_params,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise RuntimeError(f"Binance klines request failed for {symbol}: {exc}") from exc
        data = response.json()
        if not isinstance(data, list):
            raise RuntimeError(f"unexpected Binance klines payload for {symbol}")
        return [row for row in data if isinstance(row, list)]

    def close(self) -> None:
        """Release the underlying HTTP client (only if self-owned)."""
        self._client.close()


def _kline_to_bar(row: list[Any]) -> HistoricalBar:
    """Convert one Binance kline row into a validated :class:`HistoricalBar`.

    Binance row shape: ``[open_time(ms), open, high, low, close, volume,
    close_time, quote_volume, trades, taker_buy_base, taker_buy_quote,
    ignore]``.
    """
    try:
        open_ms = int(row[0])
        return HistoricalBar(
            timestamp=datetime.fromtimestamp(open_ms / 1000.0, tz=UTC),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
    except (ValueError, TypeError, IndexError) as exc:
        raise ValueError(f"invalid Binance kline row: {row!r}") from exc
