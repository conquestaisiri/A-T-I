"""MT5 Execution Bridge - connects ATI to MetaTrader 5 terminal.

This bridge provides a unified interface for executing orders through MT5,
supporting multiple brokers/prop firms via their MT5 credentials.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from ....application.interfaces.order_gateway import OrderGateway
from ....domain.execution.execution_report import ExecutionReport
from ....domain.execution.order import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
    TimeInForce,
)
from ....domain.execution.reconciliation import VenuePosition

logger = logging.getLogger(__name__)

T = TypeVar("T")


def _import_mt5() -> Any:
    """Lazily import the MetaTrader5 package (untyped third-party)."""
    import MetaTrader5

    return MetaTrader5


@dataclass(frozen=True, slots=True)
class MT5Credentials:
    """MT5 account credentials."""

    login: int
    password: str
    server: str
    path: str | None = None  # MT5 terminal path if non-standard
    magic: int = 0  # magic number stamped on ATI orders for identification


@dataclass(frozen=True, slots=True)
class MT5AccountInfo:
    """Parsed MT5 account information."""

    login: int
    balance: float
    equity: float
    margin: float
    free_margin: float
    margin_level: float
    currency: str
    leverage: int
    trade_allowed: bool
    trade_expert: bool


class MT5Bridge:
    """Bridge between ATI and MT5 terminal.

    Runs MT5 operations on a dedicated thread with its own event loop
    to avoid blocking the main ATI event loop. The MetaTrader5 Python API
    requires all calls from the thread that called ``initialize``, so every
    MT5 call is dispatched onto this single thread.
    """

    def __init__(self, credentials: MT5Credentials) -> None:
        self._credentials = credentials
        self._mt5: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._initialized = False
        self._init_done = threading.Event()
        self._init_error: BaseException | None = None
        self._lock = threading.Lock()

    def start(self) -> None:
        """Start the MT5 bridge thread and wait for initialization."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._init_done.clear()
            self._init_error = None
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="MT5Bridge")
            self._thread.start()

        if not self._init_done.wait(timeout=30):
            self._running = False
            raise RuntimeError("MT5 bridge failed to initialize: timeout")
        if self._init_error is not None:
            self._running = False
            raise RuntimeError("MT5 bridge failed to initialize") from self._init_error
        logger.info("MT5 bridge started for account %d", self._credentials.login)

    def stop(self) -> None:
        """Stop the MT5 bridge."""
        with self._lock:
            if not self._running:
                return
            self._running = False
            loop = self._loop
            thread = self._thread

        if loop is not None:
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(self._shutdown_async(), loop).result(timeout=10)
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=10)
        logger.info("MT5 bridge stopped")

    def _run_loop(self) -> None:
        """Run the asyncio event loop for MT5 operations."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            try:
                loop.run_until_complete(self._initialize_async())
            except Exception as e:
                self._init_error = e
                self._running = False
                logger.exception("MT5 bridge initialization failed: %s", e)
            finally:
                self._init_done.set()
            if self._running:
                loop.run_forever()
        except Exception as e:
            logger.exception("MT5 bridge loop error: %s", e)
        finally:
            with contextlib.suppress(Exception):
                loop.close()
            self._loop = None

    async def _initialize_async(self) -> None:
        """Initialize MT5 connection."""
        try:
            mt5 = _import_mt5()

            self._mt5 = mt5

            if not mt5.initialize(
                path=self._credentials.path or "",
                login=self._credentials.login,
                password=self._credentials.password,
                server=self._credentials.server,
            ):
                error = mt5.last_error()
                raise RuntimeError(f"MT5 initialize failed: {error}")

            account = mt5.account_info()
            if account is None:
                raise RuntimeError("Failed to get account info after login")

            self._initialized = True
            logger.info(
                "MT5 initialized for account %d on %s",
                self._credentials.login,
                self._credentials.server,
            )
        except ImportError as e:
            raise RuntimeError(
                "MetaTrader5 package not installed. Install with: pip install MetaTrader5"
            ) from e

    async def _shutdown_async(self) -> None:
        """Shutdown MT5 connection."""
        if self._mt5 is not None:
            with contextlib.suppress(Exception):
                self._mt5.shutdown()
        self._initialized = False

    def _submit(self, coro: Coroutine[Any, Any, T]) -> T:
        """Submit a coroutine to the MT5 event loop and wait for result."""
        loop = self._loop
        if loop is None or not self._running:
            # Avoid "coroutine was never awaited" warning when the bridge
            # isn't running (common in tests and when the terminal is closed).
            coro.close()
            raise RuntimeError("MT5 bridge not running")
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        return future.result(timeout=30)

    # --- Order execution ---

    def submit_order(self, request: OrderRequest) -> ExecutionReport:
        """Submit an order through MT5."""
        return self._submit(self._submit_order_async(request))

    async def _submit_order_async(self, request: OrderRequest) -> ExecutionReport:
        if not self._mt5:
            raise RuntimeError("MT5 not initialized")

        mt5 = _import_mt5()

        mt5_request = self._build_mt5_request(request)

        check_result = mt5.order_check(mt5_request)
        if check_result is None or check_result.retcode != mt5.TRADE_RETCODE_DONE:
            logger.warning(
                "MT5 order check failed: %s",
                check_result.comment if check_result is not None else "no result",
            )
            return self._rejected_report(request)

        result = mt5.order_send(mt5_request)
        if result is None:
            logger.warning("MT5 order_send returned no result: %s", mt5.last_error())
            return self._rejected_report(request)

        return self._parse_execution_report(request, result)

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order."""
        return self._submit(self._cancel_order_async(order_id))

    async def _cancel_order_async(self, order_id: str) -> bool:
        if not self._mt5:
            return False
        mt5 = _import_mt5()

        try:
            ticket = int(order_id)
        except ValueError:
            logger.warning("Invalid MT5 order id for cancel: %s", order_id)
            return False

        request = {
            "action": mt5.TRADE_ACTION_REMOVE,
            "order": ticket,
        }
        result = mt5.order_send(request)
        return result is not None and result.retcode == mt5.TRADE_RETCODE_DONE

    def get_positions(self) -> list[VenuePosition]:
        """Get current positions."""
        return self._submit(self._get_positions_async())

    async def _get_positions_async(self) -> list[VenuePosition]:
        if not self._mt5:
            return []
        mt5 = _import_mt5()

        positions = mt5.positions_get()
        if positions is None:
            return []
        return [self._parse_position(p) for p in positions]

    def get_account_info(self) -> MT5AccountInfo:
        """Get account information."""
        return self._submit(self._get_account_info_async())

    async def _get_account_info_async(self) -> MT5AccountInfo:
        if not self._mt5:
            raise RuntimeError("MT5 not initialized")
        mt5 = _import_mt5()

        account = mt5.account_info()
        if account is None:
            raise RuntimeError("Failed to get account info")
        return MT5AccountInfo(
            login=account.login,
            balance=account.balance,
            equity=account.equity,
            margin=account.margin,
            free_margin=account.margin_free,
            margin_level=float(account.margin_level) if account.margin_level else 0.0,
            currency=account.currency,
            leverage=account.leverage,
            trade_allowed=account.trade_allowed,
            trade_expert=account.trade_expert,
        )

    def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        """Get symbol information."""
        return self._submit(self._get_symbol_info_async(symbol))

    async def _get_symbol_info_async(self, symbol: str) -> dict[str, Any] | None:
        if not self._mt5:
            return None
        mt5 = _import_mt5()

        info = mt5.symbol_info(symbol)
        if info is None:
            return None
        return {
            "symbol": info.name,
            "bid": info.bid,
            "ask": info.ask,
            "spread": info.spread,
            "point": info.point,
            "digits": info.digits,
            "trade_mode": info.trade_mode,
            "min_lot": info.volume_min,
            "max_lot": info.volume_max,
            "lot_step": info.volume_step,
            "swap_long": info.swap_long,
            "swap_short": info.swap_short,
        }

    def get_tick(self, symbol: str) -> dict[str, Any] | None:
        """Get latest tick."""
        return self._submit(self._get_tick_async(symbol))

    def get_rates(
        self, symbol: str, timeframe: str = "H1", count: int = 200
    ) -> list[dict[str, Any]]:
        """Get OHLCV bars.

        timeframe: "M1", "M5", "M15", "M30", "H1", "H4", "D1".
        Selects the symbol in Market Watch before fetching.
        """
        return self._submit(self._get_rates_async(symbol, timeframe, count))

    async def _get_rates_async(
        self, symbol: str, timeframe: str, count: int
    ) -> list[dict[str, Any]]:
        if not self._mt5:
            return []
        mt5 = _import_mt5()
        mt5.symbol_select(symbol, True)
        tf_map = {
            "M1": mt5.TIMEFRAME_M1,
            "M5": mt5.TIMEFRAME_M5,
            "M15": mt5.TIMEFRAME_M15,
            "M30": mt5.TIMEFRAME_M30,
            "H1": mt5.TIMEFRAME_H1,
            "H4": mt5.TIMEFRAME_H4,
            "D1": mt5.TIMEFRAME_D1,
        }
        tf = tf_map.get(timeframe, mt5.TIMEFRAME_H1)
        rates = mt5.copy_rates_from_pos(symbol, tf, 0, count)
        if rates is None:
            return []
        return [
            {
                "time": int(r["time"]),
                "open": r["open"],
                "high": r["high"],
                "low": r["low"],
                "close": r["close"],
                "volume": r["tick_volume"],
            }
            for r in rates
        ]

    async def _get_tick_async(self, symbol: str) -> dict[str, Any] | None:
        if not self._mt5:
            return None
        mt5 = _import_mt5()

        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None
        return {
            "symbol": symbol,
            "bid": tick.bid,
            "ask": tick.ask,
            "last": tick.last,
            "volume": tick.volume,
            "time": datetime.fromtimestamp(tick.time, tz=UTC),
        }

    # --- Helpers ---

    def _build_mt5_request(self, request: OrderRequest) -> dict[str, Any]:
        """Convert ATI OrderRequest to MT5 order request dict."""
        mt5 = _import_mt5()

        is_limit = request.order_type is OrderType.LIMIT

        if request.side is OrderSide.BUY:
            order_type = mt5.ORDER_TYPE_BUY_LIMIT if is_limit else mt5.ORDER_TYPE_BUY
        else:
            order_type = mt5.ORDER_TYPE_SELL_LIMIT if is_limit else mt5.ORDER_TYPE_SELL

        filling_map = {
            TimeInForce.FOK: mt5.ORDER_FILLING_FOK,
            TimeInForce.IOC: mt5.ORDER_FILLING_IOC,
            TimeInForce.GTC: mt5.ORDER_FILLING_RETURN,
            # MT5 has no post-only fill mode; treat GTX as RETURN (best effort).
            TimeInForce.GTX: mt5.ORDER_FILLING_RETURN,
        }
        filling = filling_map.get(request.time_in_force, mt5.ORDER_FILLING_RETURN)

        return {
            "action": mt5.TRADE_ACTION_PENDING if is_limit else mt5.TRADE_ACTION_DEAL,
            "symbol": request.symbol,
            "volume": float(request.quantity),
            "type": order_type,
            "price": float(request.limit_price) if request.limit_price else 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "deviation": 10,
            "magic": self._credentials.magic,
            "comment": "ATI",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": filling,
        }

    def _parse_execution_report(self, request: OrderRequest, result: Any) -> ExecutionReport:
        """Parse MT5 order result into ExecutionReport."""
        mt5 = _import_mt5()

        if result.retcode == mt5.TRADE_RETCODE_DONE:
            status = OrderStatus.FILLED
            filled_qty = float(result.volume)
            avg_price = float(result.price)
        elif result.retcode == mt5.TRADE_RETCODE_DONE_PARTIAL:
            status = OrderStatus.PARTIALLY_FILLED
            filled_qty = float(result.volume)
            avg_price = float(result.price)
        elif result.retcode == mt5.TRADE_RETCODE_PLACED:
            status = OrderStatus.NEW
            filled_qty = 0.0
            avg_price = 0.0
        else:
            status = OrderStatus.REJECTED
            filled_qty = 0.0
            avg_price = 0.0

        ticket = int(getattr(result, "order", 0) or 0)
        commission = getattr(result, "commission", 0)
        return ExecutionReport(
            order_id=str(ticket) if ticket else request.order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=filled_qty,
            average_fill_price=avg_price,
            status=status,
            executed_at=datetime.now(UTC),
            fee=float(commission) if commission else None,
            funding_cost=None,
            venue="mt5",
            is_maker=None,
            arrival_price=None,
            latency_ms=None,
        )

    def _rejected_report(self, request: OrderRequest) -> ExecutionReport:
        """Build a REJECTED report so the gateway contract is never violated."""
        return ExecutionReport(
            order_id=request.order_id,
            symbol=request.symbol,
            side=request.side,
            quantity=0.0,
            average_fill_price=0.0,
            status=OrderStatus.REJECTED,
            executed_at=datetime.now(UTC),
            fee=None,
            funding_cost=None,
            venue="mt5",
            is_maker=None,
            arrival_price=None,
            latency_ms=None,
        )

    def _parse_position(self, pos: Any) -> VenuePosition:
        """Parse MT5 position to VenuePosition."""
        mt5 = _import_mt5()

        side = OrderSide.BUY if pos.type == mt5.POSITION_TYPE_BUY else OrderSide.SELL
        return VenuePosition(
            symbol=pos.symbol,
            side=side,
            quantity=float(pos.volume),
            average_entry_price=float(pos.price_open),
            reported_at=datetime.now(UTC),
        )


class MT5OrderGateway(OrderGateway):
    """OrderGateway implementation using MT5Bridge."""

    def __init__(self, bridge: MT5Bridge) -> None:
        self._bridge = bridge

    def submit(self, order: OrderRequest) -> ExecutionReport:
        return self._bridge.submit_order(order)

    def cancel(self, order_id: str) -> bool:
        return self._bridge.cancel_order(order_id)

    def get_position(self, symbol: str) -> VenuePosition | None:
        positions = self._bridge.get_positions()
        for p in positions:
            if p.symbol == symbol:
                return p
        return None

    def get_all_positions(self) -> list[VenuePosition]:
        return self._bridge.get_positions()

    def get_account_info(self) -> MT5AccountInfo:
        return self._bridge.get_account_info()

    def get_symbol_info(self, symbol: str) -> dict[str, Any] | None:
        return self._bridge.get_symbol_info(symbol)

    def get_tick(self, symbol: str) -> dict[str, Any] | None:
        return self._bridge.get_tick(symbol)
