# backend/presentation/api/routes_mt5.py
"""MT5 Forex API — account, positions, symbols, rates, and order execution."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Security
from pydantic import BaseModel, Field

from backend.infrastructure.execution.mt5.bridge import MT5Bridge, MT5Credentials
from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/mt5",
    tags=["mt5"],
    dependencies=[Security(verify_api_key)],
)

_bridge: MT5Bridge | None = None


def _get_bridge() -> MT5Bridge:
    global _bridge
    if _bridge is None:
        import os

        credentials = MT5Credentials(
            login=int(os.getenv("MT5_LOGIN", "111620066")),
            password=os.getenv("MT5_PASSWORD", ""),
            server=os.getenv("MT5_SERVER", "MetaQuotes-Demo"),
            path=os.getenv("MT5_TERMINAL_PATH"),
            magic=int(os.getenv("MT5_MAGIC_NUMBER", "123456")),
        )
        _bridge = MT5Bridge(credentials)
        _bridge.start()
    return _bridge


@router.get("/account")
async def get_account() -> dict[str, Any]:
    """Get MT5 account information."""
    try:
        bridge = _get_bridge()
        info = bridge.get_account_info()
        return {
            "login": info.login,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.free_margin,
            "margin_level": info.margin_level,
            "currency": info.currency,
            "leverage": info.leverage,
            "trade_allowed": info.trade_allowed,
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MT5 not available: {exc}") from exc


@router.get("/positions")
async def get_positions() -> dict[str, Any]:
    """Get all open MT5 positions."""
    try:
        bridge = _get_bridge()
        positions = bridge.get_positions()
        return {
            "count": len(positions),
            "positions": [
                {
                    "symbol": p.symbol,
                    "side": p.side.value,
                    "quantity": p.quantity,
                    "entry_price": p.average_entry_price,
                }
                for p in positions
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MT5 not available: {exc}") from exc


@router.get("/rates/{symbol}")
async def get_rates(
    symbol: str,
    timeframe: str = Query(default="H1"),
    count: int = Query(default=200, ge=1, le=5000),
) -> dict[str, Any]:
    """Get OHLCV bars from MT5."""
    try:
        bridge = _get_bridge()
        rates = bridge.get_rates(symbol.upper(), timeframe, count)
        return {"symbol": symbol.upper(), "count": len(rates), "rates": rates}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MT5 not available: {exc}") from exc


@router.get("/tick/{symbol}")
async def get_tick(symbol: str) -> dict[str, Any]:
    """Get latest tick for a symbol."""
    try:
        bridge = _get_bridge()
        tick = bridge.get_tick(symbol.upper())
        if tick is None:
            raise HTTPException(status_code=404, detail=f"No tick for {symbol}")
        return tick
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MT5 not available: {exc}") from exc


@router.get("/symbols")
async def get_symbols(group: str = Query(default="*USD*")) -> dict[str, Any]:
    """Get available MT5 symbols matching a group pattern."""
    try:
        bridge = _get_bridge()
        mt5_module = bridge._mt5
        if mt5_module is None:
            raise HTTPException(status_code=503, detail="MT5 not initialized")
        symbols = mt5_module.symbols_get(group=group)
        if symbols is None:
            return {"count": 0, "symbols": []}
        return {
            "count": len(symbols),
            "symbols": [
                {"name": s.name, "bid": s.bid, "ask": s.ask, "digits": s.digits}
                for s in list(symbols)[:100]
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MT5 not available: {exc}") from exc


class MT5OrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=30)
    side: str = Field(..., pattern="^(buy|sell)$")
    volume: float = Field(..., gt=0)
    stop_loss: float | None = None
    take_profit: float | None = None


def _assert_order_allowed(request: Request) -> None:
    """Audit fix: this route previously bypassed every safety layer.

    A live broker order is now refused unless (a) the supervisor kill switch
    is disengaged AND the platform is HEALTHY, and (b) the operator has not
    left paper mode without live authorization. This mirrors the gate the
    decision pipeline applies before any proposal becomes a fill.
    """
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is not None:
        decision = supervisor.check()
        status = (
            decision.status.value if hasattr(decision.status, "value") else str(decision.status)
        )
        reason = decision.reason or "no reason given"
        if status != "healthy":
            raise HTTPException(
                status_code=423,
                detail=f"MT5 order refused: platform is {status} ({reason})",
            )
    from backend.infrastructure.config import settings as _settings_module

    settings = _settings_module.settings
    if getattr(settings, "paper_mode", True) and not getattr(
        settings, "live_trading_authorized", False
    ):
        raise HTTPException(
            status_code=403,
            detail="MT5 order refused: paper mode interlock engaged (set PAPER_MODE=false "
            "and LIVE_TRADING_AUTHORIZED=true to enable live orders)",
        )


@router.post("/order")
async def place_order(order: MT5OrderRequest, request: Request) -> dict[str, Any]:
    """Place a market order through MT5 (supervisor + paper-mode gated)."""
    _assert_order_allowed(request)
    try:
        bridge = _get_bridge()
        mt5_module = bridge._mt5
        if mt5_module is None:
            raise HTTPException(status_code=503, detail="MT5 not initialized")

        tick = mt5_module.symbol_info_tick(order.symbol.upper())
        if tick is None:
            raise HTTPException(status_code=404, detail=f"Symbol {order.symbol} not found")

        is_buy = order.side == "buy"
        price = tick.ask if is_buy else tick.bid
        order_type = mt5_module.ORDER_TYPE_BUY if is_buy else mt5_module.ORDER_TYPE_SELL

        mt5_request = {
            "action": mt5_module.TRADE_ACTION_DEAL,
            "symbol": order.symbol.upper(),
            "volume": order.volume,
            "type": order_type,
            "price": price,
            "sl": order.stop_loss or 0.0,
            "tp": order.take_profit or 0.0,
            "deviation": 10,
            "magic": bridge._credentials.magic,
            "comment": "ATI Manual",
            "type_time": mt5_module.ORDER_TIME_GTC,
            "type_filling": mt5_module.ORDER_FILLING_IOC,
        }
        result = mt5_module.order_send(mt5_request)
        if result is None or result.retcode != mt5_module.TRADE_RETCODE_DONE:
            comment = result.comment if result else "no result"
            raise HTTPException(status_code=400, detail=f"Order failed: {comment}")

        return {
            "status": "filled",
            "ticket": result.order,
            "symbol": order.symbol.upper(),
            "side": order.side,
            "volume": result.volume,
            "price": result.price,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"MT5 error: {exc}") from exc
