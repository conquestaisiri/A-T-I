# CCXT unified venue order gateway.
#
# Wraps the CCXT async exchange behind the ``OrderGateway`` port so ATI can
# execute against 100+ crypto venues through one code path. CCXT domain
# objects never reach the core – every order and response is translated at
# this boundary (ADR 0012, Integration Constitution §102-108).
#
# The ``OrderGateway`` port is synchronous, while CCXT is async. This module
# bridges the two with a dedicated event loop on a daemon thread: ``submit``
# dispatches the async coroutine onto that loop and blocks for the result.

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.application.interfaces.order_gateway import OrderGateway
from backend.application.interfaces.venue_state import VenueStateSource
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderRequest, OrderSide, OrderStatus
from backend.domain.execution.reconciliation import VenuePosition

from ..ccxt_config import CcxtVenueConfig
from .errors import LiveTradingCredentialError, LiveTradingNotAuthorizedError

logger = logging.getLogger(__name__)

# CCXT unified order status → ATI OrderStatus ---------------------------
_FILLED_STATUSES = frozenset({"closed"})
_PARTIAL_STATUSES = frozenset({"open"})
_CANCELLED_STATUSES = frozenset({"canceled", "cancelled"})
_REJECTED_STATUSES = frozenset({"rejected"})
_EXPIRED_STATUSES = frozenset({"expired"})


def _mid_price(book: dict[str, Any]) -> float | None:
    """Mid of the best bid/ask from a CCXT order-book dict.

    Tolerates both flat price lists (``[price, amount]``) and fully quoted
    entries taken from exchange-specific formats where possible. Returns None
    when either side is missing or unparseable.
    """
    bids = book.get("bids")
    asks = book.get("asks")
    if not isinstance(bids, list) or not isinstance(asks, list):
        return None
    if not bids or not asks:
        return None
    best_bid = _first_price(bids[0])
    best_ask = _first_price(asks[0])
    if best_bid is None or best_ask is None:
        return None
    return (best_bid + best_ask) / 2.0


def _first_price(level: Any) -> float | None:
    try:
        if isinstance(level, (list, tuple)) and level:
            return float(level[0])
        if isinstance(level, dict):
            for key in ("price", "0"):
                if key in level:
                    return float(level[key])
    except (TypeError, ValueError, KeyError):
        return None
    return None


def _parse_position_side(value: str) -> OrderSide | None:
    """Map a venue-reported side string to :class:`OrderSide`."""
    normalized = value.strip().lower()
    if normalized in ("long", "long_side", "buy"):
        return OrderSide.BUY
    if normalized in ("short", "short_side", "sell"):
        return OrderSide.SELL
    return None


def _side_from_signed_quantity(quantity: float) -> OrderSide | None:
    """Derive side from a signed CCXT size (positive long, negative short)."""
    if quantity > 0.0:
        return OrderSide.BUY
    if quantity < 0.0:
        return OrderSide.SELL
    return None


class _AsyncExchange(Protocol):
    """Structural sub-set of the CCXT async exchange used for execution."""

    symbol: str

    async def load_markets(self) -> None: ...
    async def close(self) -> None: ...
    def set_sandbox_mode(self, enabled: bool) -> None: ...
    async def fetch_order_book(self, symbol: str, limit: int | None = None) -> dict[str, Any]: ...
    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...
    async def cancel_order(self, id: str, symbol: str | None = None) -> dict[str, Any]: ...
    async def fetch_positions(self, symbols: list[str] | None = None) -> list[dict[str, Any]]: ...
    async def fetch_order(self, id: str, symbol: str | None = None) -> dict[str, Any]: ...


class CcxtOrderGateway(OrderGateway, VenueStateSource):
    """CCXT-backed implementation of the :class:`OrderGateway` port.

    One instance per venue. Orders placed through :meth:`submit` are translated
    into CCXT ``create_order`` calls on a dedicated async event loop; the
    CCXT response is mapped back into an :class:`ExecutionReport`.

    Parameters
    ----------
    config:
        Venue-specific configuration.
    exchange_factory:
        Callable returning a CCXT async exchange instance. Defaults to
        instantiating the real CCXT exchange from ``config.venue_id`` –
        injected so tests run without network or the full CCXT runtime.
    """

    def __init__(
        self,
        config: CcxtVenueConfig,
        exchange_factory: Callable[[CcxtVenueConfig], _AsyncExchange] | None = None,
        *,
        live_trading_authorized: bool = False,
    ) -> None:
        self._config = config
        self._exchange_factory = exchange_factory or _default_exchange_factory
        self._exchange: _AsyncExchange | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._lock = threading.Lock()
        self._live_trading_authorized = live_trading_authorized

    # -- Lifecycle -------------------------------------------------------------
    def connect(self) -> None:
        """Start the async loop and initialise the exchange.

        Idempotent: subsequent calls are a no-op once the gateway is ready.

        Raises
        ------
        LiveTradingNotAuthorizedError
            If the venue is configured live (``sandbox=False``) without
            explicit operator authorization.
        LiveTradingCredentialError
            If the venue is configured live without API credentials.
        """
        with self._lock:
            if self._ready.is_set():
                return
            self._assert_live_safe()
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._loop.run_forever,
                name=f"ccxt-gateway-{self._config.venue_id}",
                daemon=True,
            )
            self._thread.start()
            self._run_sync(self._async_connect())
            self._ready.set()
            logger.info("CCXT gateway %s: ready", self._config.venue_id)

    def disconnect(self) -> None:
        """Close the exchange and stop the async loop."""
        with self._lock:
            if self._loop is None:
                return
            if self._exchange is not None:
                try:
                    self._run_sync(self._async_close(), timeout=10.0)
                except Exception as exc:  # pragma: no cover – cleanup path
                    logger.warning("CCXT gateway %s close error: %s", self._config.venue_id, exc)
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=10.0)
            self._loop = None
            self._thread = None
            self._exchange = None
            self._ready.clear()
            logger.info("CCXT gateway %s: stopped", self._config.venue_id)

    async def _async_connect(self) -> None:
        self._exchange = self._exchange_factory(self._config)
        await self._exchange.load_markets()
        if self._config.sandbox:
            self._exchange.set_sandbox_mode(True)

    def _assert_live_safe(self) -> None:
        """Refuse a live connection unless the operator explicitly authorized it.

        Live execution is never a default (P0-014). Sandbox mode is the only
        mode that works out of the box and never needs credentials; a non-
        sandbox gateway additionally requires both explicit operator
        authorization and real venue credentials. Production capital is only
        reachable by deliberate choice.
        """
        if self._config.sandbox:
            return
        if not self._live_trading_authorized:
            raise LiveTradingNotAuthorizedError(
                f"live trading on {self._config.venue_id} requires explicit operator "
                "authorization (live_trading_authorized=True)"
            )
        if not self._config.api_key or not self._config.secret:
            raise LiveTradingCredentialError(
                f"live trading on {self._config.venue_id} requires api_key and secret"
            )

    async def _async_close(self) -> None:
        if self._exchange is not None:
            await self._exchange.close()

    # -- OrderGateway port -----------------------------------------------------
    def submit(self, order: OrderRequest) -> ExecutionReport:
        """Place an order on the venue and return its execution report.

        Translates the venue-agnostic ``OrderRequest`` into a CCXT unified
        ``create_order`` call, then maps the response back to an
        :class:`ExecutionReport`. Never raises for a CCXT rejection – a
        rejected order is reported with ``status=REJECTED`` so the simulator
        and ledger can record the outcome (the risk gate holds *whether* to
        trade; the gateway only decides *how* it fills).
        """
        self.connect()
        result: ExecutionReport = self._run_sync(self._async_submit(order))
        return result

    def cancel(self, order_id: str, symbol: str | None = None) -> ExecutionReport:
        """Cancel a resting order (``CancelableGateway``).

        Delegates to the venue's ``cancel_order`` and maps the response back
        to an :class:`ExecutionReport`. Unlike order submission, a failed or
        unknown cancel **raises**: a cancellation the venue did not honour
        must never be reported as a success.
        """
        self.connect()
        report: ExecutionReport = self._run_sync(self._async_cancel(order_id, symbol))
        if report.status is OrderStatus.UNKNOWN:
            raise ValueError(
                f"order {order_id} is unknown to venue {self._config.venue_id}; cannot cancel"
            )
        return report

    async def _async_submit(self, order: OrderRequest) -> ExecutionReport:
        assert self._loop is not None
        assert self._exchange is not None
        arrival_price = await self._capture_arrival_mid(order.symbol)
        try:
            params = self._build_ccxt_params(order)
            started_at = time.perf_counter()
            response = await self._exchange.create_order(
                symbol=order.symbol,
                type=self._ccxt_order_type(order),
                side=order.side.value,
                amount=order.quantity,
                price=order.limit_price,
                params=params,
            )
            latency_ms = (time.perf_counter() - started_at) * 1000.0
        except Exception as exc:
            logger.warning(
                "CCXT gateway %s: create_order failed for %s – %s",
                self._config.venue_id,
                order.order_id,
                exc,
            )
            return self._rejected_report(order, reason=str(exc), arrival_price=arrival_price)
        return self._report_from_response(
            order, response, arrival_price=arrival_price, latency_ms=latency_ms
        )

    async def _async_cancel(self, order_id: str, symbol: str | None) -> ExecutionReport:
        assert self._exchange is not None
        try:
            response = await self._exchange.cancel_order(order_id, symbol=symbol)
        except Exception as exc:
            logger.warning(
                "CCXT gateway %s: cancel_order failed for %s – %s",
                self._config.venue_id,
                order_id,
                exc,
            )
            raise
        return self._report_from_cancel_response(order_id, response)

    def _report_from_cancel_response(
        self, order_id: str, response: dict[str, Any]
    ) -> ExecutionReport:
        status = self._map_status(response)
        filled = float(response.get("filled") or 0.0)
        average = response.get("average")
        price = response.get("price")
        fill_price = float(average if average is not None else (price or 0.0))
        raw_side = response.get("side")
        side = OrderSide.BUY if str(raw_side).lower() == "buy" else OrderSide.SELL
        remaining = response.get("remaining")
        return ExecutionReport(
            order_id=str(response.get("id") or order_id),
            symbol=str(response.get("symbol") or ""),
            side=side,
            quantity=filled if filled > 0 else 0.0,
            average_fill_price=fill_price if filled > 0 else 0.0,
            status=status,
            executed_at=self._parse_executed_at(response),
            fee=self._extract_fee(response),
            venue=self._config.venue_id,
            is_maker=self._extract_is_maker(response),
            remaining_quantity=float(remaining) if remaining is not None else None,
        )

    async def _capture_arrival_mid(self, symbol: str) -> float | None:
        """Snapshot the venue order-book mid **before** order submission.

        This is the arrival reference for slippage measurement. If the book
        cannot be fetched (network error, unsupported endpoint), the order is
        still submitted and the arrival mid is recorded as None rather than
        aborting execution.
        """
        exchange = self._exchange
        if exchange is None:
            return None
        try:
            book = await exchange.fetch_order_book(symbol)
        except Exception as exc:
            logger.warning(
                "CCXT gateway %s: arrival mid unavailable for %s – %s",
                self._config.venue_id,
                symbol,
                exc,
            )
            return None
        mid = _mid_price(book)
        if mid is None:
            logger.warning(
                "CCXT gateway %s: order book for %s has no usable bid/ask",
                self._config.venue_id,
                symbol,
            )
        return mid

    def _build_ccxt_params(self, order: OrderRequest) -> dict[str, Any]:
        """Build CCXT-specific params from OrderRequest."""
        params: dict[str, Any] = {}
        if order.post_only:
            params["postOnly"] = True
        # Map TimeInForce to CCXT timeInForce
        tif_map = {
            "gtc": "GTC",
            "ioc": "IOC",
            "fok": "FOK",
            "gtx": "GTX",  # post-only
        }
        tif = tif_map.get(order.time_in_force.value.lower())
        if tif:
            params["timeInForce"] = tif
        return params

    # -- VenueStateSource port (read-only venue truth) -------------------------
    def fetch_open_positions(self) -> list[VenuePosition]:
        """Return every position the venue reports as currently open."""
        self.connect()
        positions: list[VenuePosition] = self._run_sync(self._async_fetch_positions())
        return positions

    def fetch_order_status(self, order_id: str) -> OrderStatus:
        """Return the venue's current status for one order (explicit)."""
        self.connect()
        status: OrderStatus = self._run_sync(self._async_fetch_status(order_id))
        return status

    async def _async_fetch_positions(self) -> list[VenuePosition]:
        exchange = self._exchange
        if exchange is None:
            return []
        try:
            raw_positions = await exchange.fetch_positions()
        except Exception as exc:
            logger.warning(
                "CCXT gateway %s: fetch_positions failed – %s",
                self._config.venue_id,
                exc,
            )
            return []
        return self._positions_from_response(raw_positions)

    async def _async_fetch_status(self, order_id: str) -> OrderStatus:
        exchange = self._exchange
        if exchange is None:
            return OrderStatus.UNKNOWN
        try:
            response = await exchange.fetch_order(order_id)
        except Exception as exc:
            logger.warning(
                "CCXT gateway %s: fetch_order failed for %s – %s",
                self._config.venue_id,
                order_id,
                exc,
            )
            return OrderStatus.UNKNOWN
        return self._map_status(response)

    def _positions_from_response(self, raw_positions: list[dict[str, Any]]) -> list[VenuePosition]:
        """Normalise CCXT unified position dicts into domain :class:`VenuePosition`.

        CCXT reports net position sizes; the direction is given by a nominal
        "side"/"side" field or the sign of ``contracts``. Entries that carry
        neither a readable side nor a non-zero signed size are skipped (a
        venue-reported zero position is a flat account, not a position).
        """
        positions: list[VenuePosition] = []
        for raw in raw_positions:
            if not isinstance(raw, dict):
                continue
            symbol = str(raw.get("symbol") or "")
            if not symbol:
                continue
            side_value = raw.get("side") or raw.get("sideAmount")
            raw_contracts = raw.get("contracts")
            try:
                signed_quantity: float = float(raw_contracts) if raw_contracts is not None else 0.0
            except (TypeError, ValueError):
                signed_quantity = 0.0

            side: OrderSide | None = None
            if isinstance(side_value, str):
                side = _parse_position_side(side_value)
            if side is None:
                side = _side_from_signed_quantity(signed_quantity)
            if side is None or signed_quantity == 0.0:
                # A report without an open position is not a position.
                continue
            quantity = abs(signed_quantity)
            entry_price: float | None = None
            raw_entry = raw.get("entryPrice") or raw.get("avgPrice")
            if isinstance(raw_entry, (int, float)):
                entry_price = float(raw_entry)
            positions.append(
                VenuePosition(
                    symbol=symbol,
                    side=side,
                    quantity=quantity,
                    average_entry_price=entry_price,
                    reported_at=datetime.now(UTC),
                )
            )
        return positions

    # -- Mapping helpers -------------------------------------------------------
    @staticmethod
    def _ccxt_order_type(order: OrderRequest) -> str:
        from backend.domain.execution.order import OrderType

        if order.order_type is OrderType.LIMIT:
            return "limit"
        return "market"

    def _report_from_response(
        self,
        order: OrderRequest,
        response: dict[str, Any],
        arrival_price: float | None = None,
        latency_ms: float | None = None,
    ) -> ExecutionReport:
        status = self._map_status(response)
        filled = float(response.get("filled") or 0.0)
        average = response.get("average")
        price = response.get("price")
        fill_price = float(average if average is not None else (price or 0.0))
        executed_at = self._parse_executed_at(response)
        fee = self._extract_fee(response)
        is_maker = self._extract_is_maker(response)
        return ExecutionReport(
            order_id=str(response.get("id", order.order_id)),
            symbol=str(response.get("symbol", order.symbol)),
            side=order.side,
            quantity=filled
            if status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED)
            else 0.0,
            average_fill_price=fill_price if filled > 0 else 0.0,
            status=status,
            executed_at=executed_at,
            fee=fee,
            venue=self._config.venue_id,
            is_maker=is_maker,
            arrival_price=arrival_price,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _extract_fee(response: dict[str, Any]) -> float | None:
        fee = response.get("fee")
        if isinstance(fee, dict):
            cost = fee.get("cost")
            if isinstance(cost, (int, float)):
                return float(cost)
        if isinstance(fee, (int, float)):
            return float(fee)
        return None

    @staticmethod
    def _extract_is_maker(response: dict[str, Any]) -> bool | None:
        # CCXT unified format may include maker/taker in 'takerOrMaker' or 'maker'
        maker = response.get("maker")
        if isinstance(maker, bool):
            return maker
        taker_or_maker = response.get("takerOrMaker")
        if isinstance(taker_or_maker, str):
            return taker_or_maker.lower() == "maker"
        return None

    def _rejected_report(
        self, order: OrderRequest, reason: str, arrival_price: float | None = None
    ) -> ExecutionReport:
        logger.info(
            "CCXT gateway %s: order %s rejected – %s",
            self._config.venue_id,
            order.order_id,
            reason,
        )
        return ExecutionReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=0.0,
            average_fill_price=0.0,
            status=OrderStatus.REJECTED,
            executed_at=datetime.now(UTC),
            fee=None,
            venue=self._config.venue_id,
            is_maker=None,
            arrival_price=arrival_price,
        )

    @staticmethod
    def _map_status(response: dict[str, Any]) -> OrderStatus:
        raw = str(response.get("status", "")).lower()
        filled = float(response.get("filled") or 0.0)
        if raw in _FILLED_STATUSES:
            return OrderStatus.FILLED
        if raw in _CANCELLED_STATUSES:
            return OrderStatus.CANCELLED
        if raw in _REJECTED_STATUSES:
            return OrderStatus.REJECTED
        if raw in _EXPIRED_STATUSES:
            return OrderStatus.EXPIRED
        if raw in _PARTIAL_STATUSES and filled > 0:
            return OrderStatus.PARTIALLY_FILLED
        if raw in _PARTIAL_STATUSES and filled == 0:
            # A resting order with no fills yet is genuinely "new".
            return OrderStatus.NEW
        # An unrecognised venue status is a reconciliation problem, never a
        # silent NEW (order.py contract): surface it explicitly as UNKNOWN.
        return OrderStatus.UNKNOWN

    @staticmethod
    def _parse_executed_at(response: dict[str, Any]) -> datetime:
        timestamp_ms = response.get("timestamp")
        if isinstance(timestamp_ms, (int, float)):
            return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)
        return datetime.now(UTC)

    # -- Sync bridge -----------------------------------------------------------
    def _run_sync(self, coro: Any, timeout: float = 30.0) -> Any:
        """Dispatch a coroutine onto the gateway's dedicated event loop."""
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)


def _default_exchange_factory(config: CcxtVenueConfig) -> _AsyncExchange:
    """Instantiate a real CCXT async exchange from the configuration."""
    import ccxt.async_support as ccxt_async

    exchange_cls = getattr(ccxt_async, config.venue_id, None)
    if exchange_cls is None:
        raise ValueError(
            f"CCXT has no exchange id '{config.venue_id}'. "
            f"See https://docs.ccxt.com/#/exchanges for valid ids."
        )
    instance: _AsyncExchange = exchange_cls(
        {
            "apiKey": config.api_key,
            "secret": config.secret,
            "enableRateLimit": True,
            "options": {"defaultType": config.market_type},
        }
    )
    return instance
