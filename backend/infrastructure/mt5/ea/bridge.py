"""MT5 EA File Bridge - communicates with MT5 EA via file polling.

The MT5 EA polls for order files, executes orders, writes responses.
This bridge writes order requests and reads responses.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ....application.interfaces.order_gateway import OrderGateway
from ....domain.execution.execution_report import ExecutionReport
from ....domain.execution.order import (
    OrderRequest,
    OrderSide,
    OrderStatus,
    OrderType,
)
from ....domain.execution.reconciliation import VenuePosition

logger = logging.getLogger(__name__)

# MT5 TRADE_RETCODE_* values echoed back by the EA in its response file.
_TRADE_RETCODE_PLACED = 10008
_TRADE_RETCODE_DONE_PARTIAL = 10010


def _rejected_report(order: OrderRequest) -> ExecutionReport:
    """Build a REJECTED report so the gateway contract is never violated."""
    return ExecutionReport(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
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


@dataclass
class MT5EABridgeConfig:
    """Configuration for MT5 EA file bridge."""

    # File paths
    data_folder: str = ""  # MT5 terminal data folder (auto-detect if empty)
    magic_number: int = 123456

    # Polling
    poll_interval_ms: int = 100
    request_timeout_sec: float = 30.0

    # File naming
    orders_prefix: str = "ATI_Orders_"
    response_prefix: str = "ATI_Response_"

    # Symbol mapping
    symbol_map: dict[str, str] | None = None  # ATI symbol -> MT5 symbol


class MT5EABridge:
    """MT5 EA File Bridge - communicates with MT5 EA via file polling."""

    def __init__(
        self,
        config: MT5EABridgeConfig,
    ) -> None:
        self._config = config
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Determine MT5 data folder
        self._data_folder = self._resolve_data_folder(config.data_folder)

        # File paths
        self._orders_file = (
            Path(self._data_folder)
            / "MQL5"
            / "Files"
            / f"{config.orders_prefix}{config.magic_number}.json"
        )
        self._response_file = (
            Path(self._data_folder)
            / "MQL5"
            / "Files"
            / f"{config.response_prefix}{config.magic_number}.json"
        )

        # Symbol mapping
        self._symbol_map: dict[str, str] = config.symbol_map or {}

        # Pending requests (only touched on the bridge event loop)
        self._pending_requests: dict[str, asyncio.Future[ExecutionReport]] = {}
        self._pending_orders: dict[str, OrderRequest] = {}

        logger.info(
            "MT5 EA Bridge initialized: data_folder=%s, magic=%d",
            self._data_folder,
            config.magic_number,
        )

    def _resolve_data_folder(self, folder: str) -> str:
        """Resolve MT5 terminal data folder."""
        if folder:
            return folder

        # Auto-detect common locations
        candidates = [
            os.path.expanduser("~/AppData/Roaming/MetaQuotes/Terminal"),
            os.path.expandvars("%APPDATA%/MetaQuotes/Terminal"),
            "C:/Users/Public/AppData/Roaming/MetaQuotes/Terminal",
        ]

        for candidate in candidates:
            if os.path.exists(candidate):
                # Find the terminal subfolder (has MQL5/Files)
                for root, dirs, _files in os.walk(candidate):
                    if "MQL5" in dirs and "Files" in os.listdir(os.path.join(root, "MQL5")):
                        logger.info("Found MT5 data folder: %s", root)
                        return root

        # Fallback
        fallback = os.path.expanduser("~/AppData/Roaming/MetaQuotes/Terminal")
        logger.warning("MT5 data folder not found, using fallback: %s", fallback)
        return fallback

    async def start(self) -> None:
        """Start the bridge polling loop."""
        if self._running:
            return

        # Clear stale files from a previous run so a leftover order file is
        # never re-executed by the EA.
        with contextlib.suppress(OSError):
            self._orders_file.unlink(missing_ok=True)
            self._response_file.unlink(missing_ok=True)

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info("MT5 EA Bridge started")

    async def stop(self) -> None:
        """Stop the bridge."""
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("MT5 EA Bridge stopped")

    async def _poll_loop(self) -> None:
        """Poll for response files."""
        while self._running:
            try:
                await self._check_responses()
            except Exception:
                logger.exception("Error in MT5 EA poll loop")

            await asyncio.sleep(self._config.poll_interval_ms / 1000.0)

    async def _check_responses(self) -> None:
        """Check for response files from MT5 EA."""
        if not self._response_file.exists():
            return

        try:
            content = self._response_file.read_text(encoding="utf-8")
            response = json.loads(content)
        except json.JSONDecodeError:
            # The EA may still be mid-write; retry once before dropping the file.
            await asyncio.sleep(0.1)
            try:
                content = self._response_file.read_text(encoding="utf-8")
                response = json.loads(content)
            except (json.JSONDecodeError, OSError):
                with contextlib.suppress(OSError):
                    self._response_file.unlink(missing_ok=True)
                logger.warning("Dropped unparseable MT5 EA response file")
                return
        except OSError:
            return

        order_id = response.get("order_id", "")
        if order_id in self._pending_requests:
            future = self._pending_requests.pop(order_id)
            order = self._pending_orders.pop(order_id, None)
            if not future.done():
                if order is None:
                    future.set_result(
                        ExecutionReport(
                            order_id=order_id,
                            symbol="",
                            side=OrderSide.BUY,
                            quantity=0.0,
                            average_fill_price=0.0,
                            status=OrderStatus.REJECTED,
                            executed_at=datetime.now(UTC),
                            venue="mt5",
                        )
                    )
                else:
                    future.set_result(self._parse_execution_report(order, response))

        with contextlib.suppress(OSError):
            self._response_file.unlink(missing_ok=True)

    async def submit_async(self, order: OrderRequest) -> ExecutionReport:
        """Submit an order via file-based communication (async)."""
        if not self._running:
            raise RuntimeError("Bridge not running")

        ea_request = {
            "order_id": order.order_id,
            "symbol": self._symbol_map.get(order.symbol, order.symbol),
            "order_type": self._convert_order_type(order.order_type, order.side),
            "volume": order.quantity,
            "price": order.limit_price if order.order_type is OrderType.LIMIT else 0.0,
            "sl": 0.0,
            "tp": 0.0,
            "deviation": 10,
            "magic": self._config.magic_number,
            "comment": f"ATI:{order.proposal_id}",
        }

        # Register the pending future BEFORE writing the order file so a fast
        # EA response can never arrive before we are ready to match it.
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_requests[order.order_id] = future
        self._pending_orders[order.order_id] = order

        try:
            self._write_orders_file(json.dumps(ea_request))
        except OSError as e:
            self._pending_requests.pop(order.order_id, None)
            self._pending_orders.pop(order.order_id, None)
            logger.error("Failed to write MT5 EA order file: %s", e)
            return _rejected_report(order)

        try:
            return await asyncio.wait_for(future, timeout=self._config.request_timeout_sec)
        except TimeoutError:
            self._pending_requests.pop(order.order_id, None)
            self._pending_orders.pop(order.order_id, None)
            logger.warning("MT5 EA response timeout for order %s", order.order_id)
            return _rejected_report(order)

    def _write_orders_file(self, payload: str) -> None:
        """Write the order file atomically so the EA never reads partial JSON."""
        tmp = self._orders_file.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self._orders_file)
                return
            except OSError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def cancel(self, order_id: str) -> bool:
        """Cancel an order (not implemented for file-based bridge)."""
        logger.warning("Cancel not implemented for file-based MT5 bridge")
        return False

    def get_position(self, symbol: str) -> VenuePosition | None:
        return None

    def get_all_positions(self) -> list[VenuePosition]:
        return []

    def _parse_execution_report(
        self, order: OrderRequest, response: dict[str, Any]
    ) -> ExecutionReport:
        """Parse MT5 EA response into ExecutionReport."""
        success = bool(response.get("success", False))
        retcode = int(response.get("retcode", 0) or 0)
        volume = float(response.get("volume", 0.0) or 0.0)
        price = float(response.get("price", 0.0) or 0.0)

        if success and retcode == _TRADE_RETCODE_PLACED:
            status = OrderStatus.NEW
        elif success and retcode == _TRADE_RETCODE_DONE_PARTIAL:
            status = OrderStatus.PARTIALLY_FILLED
        elif success:
            status = OrderStatus.FILLED
        else:
            status = OrderStatus.REJECTED

        filled = status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
        return ExecutionReport(
            order_id=str(response.get("order_id") or order.order_id),
            symbol=order.symbol,
            side=order.side,
            quantity=volume if filled else 0.0,
            average_fill_price=price if filled else 0.0,
            status=status,
            executed_at=datetime.now(UTC),
            fee=None,
            funding_cost=None,
            venue="mt5",
            is_maker=None,
            arrival_price=None,
            latency_ms=None,
        )

    def _convert_order_type(self, order_type: OrderType, side: OrderSide) -> int:
        """Convert ATI OrderType+side to the EA's combined MQL5 order type.

        Protocol (matches ATI_EA.mq5): 0=BUY, 1=SELL, 2=BUY_LIMIT, 3=SELL_LIMIT.
        """
        mapping = {
            (OrderType.MARKET, OrderSide.BUY): 0,
            (OrderType.MARKET, OrderSide.SELL): 1,
            (OrderType.LIMIT, OrderSide.BUY): 2,
            (OrderType.LIMIT, OrderSide.SELL): 3,
        }
        return mapping.get((order_type, side), 0)

    # OrderGateway interface - sync methods
    def submit(self, order: OrderRequest) -> ExecutionReport:
        """Sync submit for OrderGateway interface.

        Only safe outside any running event loop; inside a loop use
        ``submit_async`` (or the MT5EABridgeSync wrapper).
        """
        if not self._running:
            raise RuntimeError("Bridge not running")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(self.submit_async(order))
            finally:
                loop.close()
        raise RuntimeError("Use submit_async when running inside an event loop")


# Synchronous wrapper for compatibility
class MT5EABridgeSync(OrderGateway):
    """Synchronous wrapper for MT5EABridge."""

    def __init__(self, config: MT5EABridgeConfig) -> None:
        self._bridge = MT5EABridge(config)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._started: threading.Event | None = None
        self._init_error: BaseException | None = None
        self._request_timeout_sec = config.request_timeout_sec

    def start(self) -> None:
        """Start the bridge on its own event loop thread."""
        if self._thread is not None:
            return
        self._loop = asyncio.new_event_loop()
        self._started = threading.Event()
        self._init_error = None
        self._thread = threading.Thread(target=self._run_loop, name="MT5EABridge", daemon=True)
        self._thread.start()

        if not self._started.wait(timeout=30):
            self._thread = None
            raise RuntimeError("MT5 EA bridge failed to start: timeout")
        if self._init_error is not None:
            self._thread = None
            raise RuntimeError("MT5 EA bridge failed to start") from self._init_error

    def _run_loop(self) -> None:
        """Run the bridge's event loop on this thread."""
        loop = self._loop
        if loop is None:
            return
        asyncio.set_event_loop(loop)
        try:
            try:
                loop.run_until_complete(self._bridge.start())
            except Exception as e:
                self._init_error = e
                logger.exception("MT5 EA bridge start failed: %s", e)
            finally:
                if self._started is not None:
                    self._started.set()
            if self._init_error is None:
                # Keep the loop pumping so run_coroutine_threadsafe calls
                # (submit_async) and the poll task can execute.
                loop.run_forever()
        except Exception as e:
            logger.exception("MT5 EA bridge loop error: %s", e)
        finally:
            with contextlib.suppress(Exception):
                if loop.is_running():
                    loop.stop()
            loop.close()
            self._loop = None

    def stop(self) -> None:
        """Stop the bridge and its event loop thread."""
        thread = self._thread
        if thread is None:
            return
        loop = self._loop
        self._loop = None
        self._thread = None
        if loop is not None:
            with contextlib.suppress(Exception):
                asyncio.run_coroutine_threadsafe(self._bridge.stop(), loop).result(timeout=10)
            loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)

    def submit(self, order: OrderRequest) -> ExecutionReport:
        if self._loop is None:
            raise RuntimeError("Bridge not started")
        future = asyncio.run_coroutine_threadsafe(self._bridge.submit_async(order), self._loop)
        try:
            return future.result(timeout=self._request_timeout_sec + 5)
        except concurrent.futures.TimeoutError:
            logger.warning("MT5 EA bridge submit timed out for order %s", order.order_id)
            return _rejected_report(order)

    def cancel(self, order_id: str) -> bool:
        return self._bridge.cancel(order_id)

    def get_position(self, symbol: str) -> VenuePosition | None:
        return self._bridge.get_position(symbol)

    def get_all_positions(self) -> list[VenuePosition]:
        return self._bridge.get_all_positions()
