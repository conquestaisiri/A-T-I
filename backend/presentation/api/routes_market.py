# backend/presentation/api/routes_market.py
"""Market data API — live OHLCV, order book, ticker, and pair listing from MEXC."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Security

from backend.infrastructure.data_fabric.connectors.crypto.mexc import (
    fetch_all_usdt_pairs,
    fetch_klines,
    fetch_order_book,
    fetch_recent_trades,
    fetch_ticker,
)
from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/market",
    tags=["market"],
    dependencies=[Security(verify_api_key)],
)


@router.get("/klines")
async def get_klines(
    symbol: str = Query(..., min_length=1, max_length=20),
    interval: str = Query(default="1h"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """Fetch OHLCV candlesticks from MEXC."""
    valid_intervals = {"1m", "5m", "15m", "30m", "60m", "4h", "1d", "1W", "1M"}
    if interval not in valid_intervals:
        raise HTTPException(status_code=422, detail=f"Invalid interval. Use: {valid_intervals}")
    try:
        candles = await fetch_klines(symbol.strip().upper(), interval, limit)
        return {"symbol": symbol.strip().upper(), "interval": interval, "candles": candles}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MEXC fetch failed: {exc}") from exc


@router.get("/pairs")
async def get_pairs() -> dict[str, Any]:
    """Fetch all USDT trading pairs from MEXC."""
    try:
        pairs = await fetch_all_usdt_pairs()
        return {"count": len(pairs), "pairs": sorted(pairs)}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MEXC fetch failed: {exc}") from exc


@router.get("/depth")
async def get_depth(
    symbol: str = Query(..., min_length=1, max_length=20),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """Fetch order book depth from MEXC."""
    try:
        book = await fetch_order_book(symbol.strip().upper(), limit)
        return book
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MEXC fetch failed: {exc}") from exc


@router.get("/ticker")
async def get_ticker(
    symbol: str = Query(..., min_length=1, max_length=20),
) -> dict[str, Any]:
    """Fetch current price from MEXC."""
    try:
        ticker = await fetch_ticker(symbol.strip().upper())
        return ticker
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MEXC fetch failed: {exc}") from exc


@router.get("/trades")
async def get_trades(
    symbol: str = Query(..., min_length=1, max_length=20),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    """Fetch recent trades from MEXC."""
    try:
        trades = await fetch_recent_trades(symbol.strip().upper(), limit)
        return {"symbol": symbol.strip().upper(), "trades": trades}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"MEXC fetch failed: {exc}") from exc
