"""Unit tests for the CCXT order gateway.

The CCXT runtime and network are never touched: the gateway is constructed
with an injected fake exchange factory, so all tests are deterministic and
offline. The sync ``submit`` path is exercised end-to-end, including the
sync→async bridge over the dedicated event-loop thread.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderRequest, OrderSide, OrderStatus, OrderType
from backend.infrastructure.ccxt_config import CcxtVenueConfig
from backend.infrastructure.execution.ccxt_gateway import CcxtOrderGateway
from backend.infrastructure.execution.errors import (
    LiveTradingCredentialError,
    LiveTradingNotAuthorizedError,
)


def _make_order(**overrides: Any) -> OrderRequest:
    defaults: dict[str, Any] = {
        "order_id": "ord-1",
        "proposal_id": "prop-1",
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 0.5,
        "limit_price": None,
        "created_at": datetime(2024, 1, 1, 12, 0, tzinfo=UTC),
    }
    defaults.update(overrides)
    return OrderRequest(**defaults)


def _filled_response(**overrides: Any) -> dict[str, Any]:
    response: dict[str, Any] = {
        "id": "ccxt-1",
        "symbol": "BTC/USDT",
        "status": "closed",
        "filled": 0.5,
        "average": 37000.0,
        "price": 37000.0,
        "side": "buy",
        "timestamp": 1700000000000,
    }
    response.update(overrides)
    return response


def _order_book(best_bid: float = 36990.0, best_ask: float = 37010.0) -> dict[str, Any]:
    """A CCXT-style order book with flat ``[price, amount]`` levels."""
    return {
        "bids": [[best_bid, 1.0], [best_bid - 5.0, 2.0]],
        "asks": [[best_ask, 1.0], [best_ask + 5.0, 2.0]],
    }


class _RecordingExchange:
    """A fake CCXT async exchange that records create_order calls."""

    def __init__(
        self,
        response: dict[str, Any] | None = None,
        *,
        raises: Exception | None = None,
        book: dict[str, Any] | None = None,
        book_raises: Exception | None = None,
        positions: list[dict[str, Any]] | None = None,
        positions_raises: Exception | None = None,
        order_status: dict[str, Any] | None = None,
        order_raises: Exception | None = None,
        cancel_response: dict[str, Any] | None = None,
        cancel_raises: Exception | None = None,
    ) -> None:
        self.symbol = "BTC/USDT"
        self.calls: list[dict[str, Any]] = []
        self.book_fetches: list[str] = []
        self.position_fetches: list[list[str] | None] = []
        self.order_fetches: list[tuple[str, str | None]] = []
        self.cancelled: list[tuple[str, str | None]] = []
        self._response = response
        self._raises = raises
        self._book = book if book is not None else _order_book()
        self._book_raises = book_raises
        self._positions = positions if positions is not None else []
        self._positions_raises = positions_raises
        self._order_status = order_status
        self._order_raises = order_raises
        self._cancel_response = cancel_response
        self._cancel_raises = cancel_raises
        self.closed = False
        self.markets_loaded = False
        self.sandbox_set: bool | None = None

    async def load_markets(self) -> None:
        self.markets_loaded = True

    async def close(self) -> None:
        self.closed = True

    def set_sandbox_mode(self, enabled: bool) -> None:
        self.sandbox_set = enabled

    async def fetch_order_book(self, symbol: str, limit: int | None = None) -> dict[str, Any]:
        self.book_fetches.append(symbol)
        if self._book_raises is not None:
            raise self._book_raises
        return self._book

    async def fetch_positions(self, symbols: list[str] | None = None) -> list[dict[str, Any]]:
        self.position_fetches.append(symbols)
        if self._positions_raises is not None:
            raise self._positions_raises
        return self._positions

    async def fetch_order(self, id: str, symbol: str | None = None) -> dict[str, Any]:
        self.order_fetches.append((id, symbol))
        if self._order_raises is not None:
            raise self._order_raises
        if self._order_status is not None:
            return self._order_status
        return _filled_response()

    async def cancel_order(self, id: str, symbol: str | None = None) -> dict[str, Any]:
        self.cancelled.append((id, symbol))
        if self._cancel_raises is not None:
            raise self._cancel_raises
        if self._cancel_response is not None:
            return self._cancel_response
        return _filled_response(status="canceled", filled=0.0)

    async def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "symbol": symbol,
                "type": type,
                "side": side,
                "amount": amount,
                "price": price,
                "params": params,
            }
        )
        if self._raises is not None:
            raise self._raises
        if self._response is not None:
            return self._response
        return _filled_response()


def _make_gateway(
    response: dict[str, Any] | None = None,
    *,
    raises: Exception | None = None,
    config: CcxtVenueConfig | None = None,
    book: dict[str, Any] | None = None,
    book_raises: Exception | None = None,
    positions: list[dict[str, Any]] | None = None,
    positions_raises: Exception | None = None,
    order_status: dict[str, Any] | None = None,
    order_raises: Exception | None = None,
    cancel_response: dict[str, Any] | None = None,
    cancel_raises: Exception | None = None,
) -> CcxtOrderGateway:
    exchange = _RecordingExchange(
        response,
        raises=raises,
        book=book,
        book_raises=book_raises,
        positions=positions,
        positions_raises=positions_raises,
        order_status=order_status,
        order_raises=order_raises,
        cancel_response=cancel_response,
        cancel_raises=cancel_raises,
    )

    def factory(cfg: CcxtVenueConfig) -> Any:
        return exchange

    gateway = CcxtOrderGateway(config=config or CcxtVenueConfig(), exchange_factory=factory)
    return gateway


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------
class TestCcxtOrderGatewayConstruction:
    def test_connect_loads_markets_and_sets_sandbox(self) -> None:
        exchange = _RecordingExchange()

        def factory(cfg: CcxtVenueConfig) -> Any:
            return exchange

        config = CcxtVenueConfig(sandbox=True)
        gateway = CcxtOrderGateway(config=config, exchange_factory=factory)
        gateway.connect()
        assert exchange.markets_loaded is True
        assert exchange.sandbox_set is True
        gateway.disconnect()

    def test_connect_is_idempotent(self) -> None:
        loaded: list[bool] = []

        class CountingExchange(_RecordingExchange):
            async def load_markets(self) -> None:
                loaded.append(True)
                await super().load_markets()

        def factory(cfg: CcxtVenueConfig) -> Any:
            return CountingExchange()

        gateway = CcxtOrderGateway(config=CcxtVenueConfig(), exchange_factory=factory)
        gateway.connect()
        gateway.connect()
        assert len(loaded) == 1
        gateway.disconnect()

    def test_disconnect_closes_exchange(self) -> None:
        exchange = _RecordingExchange()

        def factory(cfg: CcxtVenueConfig) -> Any:
            return exchange

        gateway = CcxtOrderGateway(config=CcxtVenueConfig(), exchange_factory=factory)
        gateway.connect()
        gateway.disconnect()
        assert exchange.closed is True


# ---------------------------------------------------------------------------
# Order submission – happy path
# ---------------------------------------------------------------------------
class TestCcxtOrderGatewaySubmit:
    def test_market_buy_translates_to_ccxt_call(self) -> None:
        exchange = _RecordingExchange()

        def factory(cfg: CcxtVenueConfig) -> Any:
            return exchange

        gateway = CcxtOrderGateway(config=CcxtVenueConfig(), exchange_factory=factory)
        order = _make_order()
        gateway.submit(order)

        assert len(exchange.calls) == 1
        call = exchange.calls[0]
        assert call["symbol"] == "BTC/USDT"
        assert call["type"] == "market"
        assert call["side"] == "buy"
        assert call["amount"] == 0.5
        assert call["price"] is None
        gateway.disconnect()

    def test_limit_sell_translates_to_ccxt_call(self) -> None:
        exchange = _RecordingExchange()

        def factory(cfg: CcxtVenueConfig) -> Any:
            return exchange

        gateway = CcxtOrderGateway(config=CcxtVenueConfig(), exchange_factory=factory)
        order = _make_order(
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            limit_price=38000.0,
        )
        gateway.submit(order)

        call = exchange.calls[0]
        assert call["type"] == "limit"
        assert call["side"] == "sell"
        assert call["price"] == 38000.0
        gateway.disconnect()

    def test_filled_market_order_report(self) -> None:
        gateway = _make_gateway(response=_filled_response())
        order = _make_order()
        report = gateway.submit(order)

        assert report.order_id == "ccxt-1"
        assert report.symbol == "BTC/USDT"
        assert report.side is OrderSide.BUY
        assert report.quantity == 0.5
        assert report.average_fill_price == 37000.0
        assert report.status is OrderStatus.FILLED
        assert report.is_filled is True
        assert report.executed_at.tzinfo is not None
        gateway.disconnect()


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------
class TestCcxtOrderGatewayStatusMapping:
    @pytest.mark.parametrize(
        "ccxt_status,expected",
        [
            ("closed", OrderStatus.FILLED),
            ("canceled", OrderStatus.CANCELLED),
            ("cancelled", OrderStatus.CANCELLED),
            ("rejected", OrderStatus.REJECTED),
            ("expired", OrderStatus.EXPIRED),
        ],
    )
    def test_terminal_statuses(self, ccxt_status: str, expected: OrderStatus) -> None:
        gateway = _make_gateway(response=_filled_response(status=ccxt_status, filled=0.0))
        report = gateway.submit(_make_order())
        assert report.status is expected
        gateway.disconnect()

    def test_open_with_partial_fill(self) -> None:
        gateway = _make_gateway(
            response=_filled_response(status="open", filled=0.25, average=37000.0)
        )
        report = gateway.submit(_make_order())
        assert report.status is OrderStatus.PARTIALLY_FILLED
        assert report.quantity == 0.25
        assert report.average_fill_price == 37000.0
        gateway.disconnect()

    def test_open_with_no_fill_is_new(self) -> None:
        gateway = _make_gateway(response=_filled_response(status="open", filled=0.0))
        report = gateway.submit(_make_order())
        assert report.status is OrderStatus.NEW
        assert report.quantity == 0.0
        gateway.disconnect()

    def test_unrecognized_status_is_explicit_unknown(self) -> None:
        # An unrecognised venue status is a reconciliation problem, never a
        # silent NEW (order.py contract / ADR 0019).
        gateway = _make_gateway(response=_filled_response(status="oddball", filled=0.0))
        report = gateway.submit(_make_order())
        assert report.status is OrderStatus.UNKNOWN
        assert report.quantity == 0.0
        gateway.disconnect()


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------
class TestCcxtOrderGatewayFailures:
    def test_create_order_exception_yields_rejected_report(self) -> None:
        gateway = _make_gateway(raises=RuntimeError("insufficient funds"))
        order = _make_order()
        report = gateway.submit(order)

        assert report.status is OrderStatus.REJECTED
        assert report.order_id == "ord-1"
        assert report.quantity == 0.0
        assert report.average_fill_price == 0.0
        gateway.disconnect()

    def test_rejected_report_is_an_execution_report(self) -> None:
        gateway = _make_gateway(raises=RuntimeError("boom"))
        report = gateway.submit(_make_order())
        assert isinstance(report, ExecutionReport)
        assert report.is_filled is False
        gateway.disconnect()


# ---------------------------------------------------------------------------
# Arrival state, slippage, latency, fee normalization (P0-011)
# ---------------------------------------------------------------------------
class TestArrivalState:
    def test_arrival_mid_captured_before_submission(self) -> None:
        gateway = _make_gateway(response=_filled_response())
        order = _make_order()
        report = gateway.submit(order)

        # Book mid is (36990 + 37010) / 2 = 37000
        assert report.arrival_price == pytest.approx(37000.0)
        gateway.disconnect()

    def test_book_fetch_precedes_create_order(self) -> None:
        exchange = _RecordingExchange()

        def factory(cfg: CcxtVenueConfig) -> Any:
            return exchange

        gateway = CcxtOrderGateway(config=CcxtVenueConfig(), exchange_factory=factory)
        gateway.submit(_make_order())

        assert exchange.book_fetches == ["BTC/USDT"]
        assert len(exchange.calls) == 1
        gateway.disconnect()

    def test_missing_order_book_yields_none_arrival(self) -> None:
        gateway = _make_gateway(response=_filled_response(), book={})
        report = gateway.submit(_make_order())
        assert report.arrival_price is None
        assert report.status is OrderStatus.FILLED
        gateway.disconnect()

    def test_book_fetch_failure_degrades_gracefully(self) -> None:
        gateway = _make_gateway(response=_filled_response(), book_raises=RuntimeError("rate limit"))
        report = gateway.submit(_make_order())
        assert report.arrival_price is None
        assert report.status is OrderStatus.FILLED
        gateway.disconnect()

    def test_buy_slippage_bps_measurable(self) -> None:
        # Buy fills at 37000 but arrival mid was 36995: (37000-36995)/36995*1e4
        gateway = _make_gateway(
            response=_filled_response(average=37000.0),
            book=_order_book(best_bid=36990.0, best_ask=37000.0),
        )
        report = gateway.submit(_make_order(side=OrderSide.BUY))
        assert report.arrival_price == pytest.approx(36995.0)
        expected_slippage = ((37000.0 - 36995.0) / 36995.0) * 10_000
        slippage_bps = report.slippage_bps
        assert slippage_bps is not None
        assert slippage_bps == pytest.approx(expected_slippage, rel=1e-9)
        assert slippage_bps > 0
        gateway.disconnect()

    def test_sell_slippage_bps_measurable(self) -> None:
        # Sell fills at 36990, arrival mid 37005: (37005-36990)/37005*1e4
        gateway = _make_gateway(
            response=_filled_response(average=36990.0, side="sell"),
            book=_order_book(best_bid=36990.0, best_ask=37020.0),
        )
        report = gateway.submit(_make_order(side=OrderSide.SELL))
        expected_slippage = ((37005.0 - 36990.0) / 37005.0) * 10_000
        slippage_bps = report.slippage_bps
        assert slippage_bps is not None
        assert slippage_bps == pytest.approx(expected_slippage, rel=1e-9)
        assert slippage_bps > 0
        gateway.disconnect()

    def test_latency_measured_in_milliseconds(self) -> None:
        gateway = _make_gateway(response=_filled_response())
        report = gateway.submit(_make_order())
        assert report.latency_ms is not None
        assert report.latency_ms >= 0.0
        gateway.disconnect()

    def test_rejected_order_still_carries_arrival_mid(self) -> None:
        gateway = _make_gateway(
            raises=RuntimeError("insufficient funds"),
            book=_order_book(best_bid=36990.0, best_ask=37010.0),
        )
        report = gateway.submit(_make_order())
        assert report.status is OrderStatus.REJECTED
        assert report.arrival_price == pytest.approx(37000.0)
        gateway.disconnect()


class TestFeeMakerNormalization:
    def test_fee_dict_cost_normalized(self) -> None:
        gateway = _make_gateway(response=_filled_response(fee={"cost": 1.25, "currency": "USDT"}))
        report = gateway.submit(_make_order())
        assert report.fee == pytest.approx(1.25)
        gateway.disconnect()

    def test_fee_flat_number_normalized(self) -> None:
        gateway = _make_gateway(response=_filled_response(fee=0.75))
        report = gateway.submit(_make_order())
        assert report.fee == pytest.approx(0.75)
        gateway.disconnect()

    def test_missing_fee_is_none(self) -> None:
        gateway = _make_gateway(response=_filled_response())
        report = gateway.submit(_make_order())
        assert report.fee is None
        gateway.disconnect()

    def test_maker_flag_from_boolean(self) -> None:
        gateway = _make_gateway(response=_filled_response(maker=True))
        report = gateway.submit(_make_order())
        assert report.is_maker is True
        gateway.disconnect()

    def test_maker_flag_from_taker_or_maker_string(self) -> None:
        gateway = _make_gateway(response=_filled_response(takerOrMaker="taker"))
        report = gateway.submit(_make_order())
        assert report.is_maker is False
        gateway.disconnect()

    def test_unknown_maker_flag_is_none(self) -> None:
        gateway = _make_gateway(response=_filled_response())
        report = gateway.submit(_make_order())
        assert report.is_maker is None
        gateway.disconnect()


class TestVenueStateContract:
    """The gateway implements VenueStateSource for reconciliation (P0-012)."""

    def test_fetch_positions_empty_when_exchange_unreachable(self) -> None:
        gateway = _make_gateway(positions_raises=RuntimeError("venue offline"))
        assert gateway.fetch_open_positions() == []
        gateway.disconnect()

    def test_fetch_positions_normalizes_ccxt_unified_dicts(self) -> None:
        gateway = _make_gateway(
            positions=[
                {
                    "symbol": "BTC/USDT",
                    "side": "long",
                    "contracts": 1.5,
                    "entryPrice": 50000.0,
                },
                {
                    "symbol": "ETH/USDT",
                    "side": "short",
                    "contracts": 2.0,
                    "avgPrice": 3000.0,
                },
            ]
        )
        positions = gateway.fetch_open_positions()
        assert len(positions) == 2
        by_symbol = {p.symbol: p for p in positions}
        assert by_symbol["BTC/USDT"].side is OrderSide.BUY
        assert by_symbol["BTC/USDT"].quantity == pytest.approx(1.5)
        assert by_symbol["BTC/USDT"].average_entry_price == pytest.approx(50000.0)
        assert by_symbol["ETH/USDT"].side is OrderSide.SELL
        assert by_symbol["ETH/USDT"].quantity == pytest.approx(2.0)
        assert by_symbol["ETH/USDT"].average_entry_price == pytest.approx(3000.0)
        gateway.disconnect()

    def test_fetch_positions_deduces_side_from_signed_contracts(self) -> None:
        gateway = _make_gateway(
            positions=[
                {"symbol": "BTC/USDT", "contracts": 1.0},
                {"symbol": "ETH/USDT", "contracts": -0.5},
            ]
        )
        positions = gateway.fetch_open_positions()
        by_symbol = {p.symbol: p for p in positions}
        assert by_symbol["BTC/USDT"].side is OrderSide.BUY
        assert by_symbol["ETH/USDT"].side is OrderSide.SELL
        gateway.disconnect()

    def test_fetch_positions_skips_flat_and_unparseable(self) -> None:
        gateway = _make_gateway(
            positions=[
                {"symbol": "BTC/USDT", "contracts": 0.0},
                {"symbol": "ETH/USDT", "contracts": "bogus"},
                {"symbol": "", "side": "long", "contracts": 1.0},
                {"symbol": "SOL/USDT", "side": "long", "contracts": 0.5},
            ]
        )
        positions = gateway.fetch_open_positions()
        assert [p.symbol for p in positions] == ["SOL/USDT"]
        gateway.disconnect()

    def test_fetch_order_status_maps_to_domain_status(self) -> None:
        gateway = _make_gateway(order_status=_filled_response(status="closed"))
        assert gateway.fetch_order_status("ccxt-1") is OrderStatus.FILLED
        gateway.disconnect()

    def test_fetch_order_status_unknown_when_unreachable(self) -> None:
        gateway = _make_gateway(order_raises=RuntimeError("venue offline"))
        assert gateway.fetch_order_status("ccxt-1") is OrderStatus.UNKNOWN
        gateway.disconnect()

    def test_fetch_order_status_unknown_for_unrecognized_status(self) -> None:
        # An unrecognised venue status is a reconciliation problem (order.py
        # contract / ADR 0019): it must surface as UNKNOWN, never NEW.
        gateway = _make_gateway(order_status=_filled_response(status="oddball"))
        assert gateway.fetch_order_status("ccxt-1") is OrderStatus.UNKNOWN
        gateway.disconnect()


# ---------------------------------------------------------------------------
# Live trading guard (P0-014)
# ---------------------------------------------------------------------------
class TestLiveTradingGuard:
    """A live (non-sandbox) gateway must never connect by default."""

    def test_live_without_authorization_refuses_before_touching_venue(self) -> None:
        factory_called: list[bool] = []

        def factory(cfg: CcxtVenueConfig) -> Any:
            factory_called.append(True)
            return _RecordingExchange()

        config = CcxtVenueConfig(sandbox=False)
        gateway = CcxtOrderGateway(config=config, exchange_factory=factory)
        with pytest.raises(LiveTradingNotAuthorizedError):
            gateway.connect()
        assert factory_called == []

    def test_live_authorized_but_credentialess_refuses(self) -> None:
        # api_key/secret default to None on the config: authorization alone is
        # not enough for a production venue.
        config = CcxtVenueConfig(sandbox=False)
        gateway = CcxtOrderGateway(
            config=config,
            exchange_factory=lambda cfg: _RecordingExchange(),
            live_trading_authorized=True,
        )
        with pytest.raises(LiveTradingCredentialError):
            gateway.connect()

    def test_live_authorized_with_credentials_connects_without_sandbox_mode(self) -> None:
        exchange = _RecordingExchange()

        def factory(cfg: CcxtVenueConfig) -> Any:
            return exchange

        config = CcxtVenueConfig(sandbox=False, api_key="key", secret="secret")
        gateway = CcxtOrderGateway(
            config=config,
            exchange_factory=factory,
            live_trading_authorized=True,
        )
        gateway.connect()
        assert exchange.markets_loaded is True
        # Live mode must NOT enable the venue sandbox.
        assert exchange.sandbox_set is None
        gateway.disconnect()

    def test_submit_in_live_without_authorization_raises(self) -> None:
        config = CcxtVenueConfig(sandbox=False)
        gateway = CcxtOrderGateway(config=config, exchange_factory=lambda cfg: _RecordingExchange())
        with pytest.raises(LiveTradingNotAuthorizedError):
            gateway.submit(_make_order())

    def test_fetch_positions_refuses_live_when_unauthorized(self) -> None:
        config = CcxtVenueConfig(sandbox=False)
        gateway = CcxtOrderGateway(config=config, exchange_factory=lambda cfg: _RecordingExchange())
        with pytest.raises(LiveTradingNotAuthorizedError):
            gateway.fetch_open_positions()

    def test_sandbox_submit_needs_no_credentials(self) -> None:
        # The sandbox lifecycle works with no production credential at all:
        # sandbox is the only mode that functions out of the box.
        gateway = _make_gateway(response=_filled_response())
        report = gateway.submit(_make_order())
        assert report.status is OrderStatus.FILLED
        gateway.disconnect()


# ---------------------------------------------------------------------------
# Cancellation (CancelableGateway, P0-014)
# ---------------------------------------------------------------------------
class TestCcxtOrderGatewayCancel:
    def test_cancel_delegates_to_exchange(self) -> None:
        exchange = _RecordingExchange()
        gateway = CcxtOrderGateway(config=CcxtVenueConfig(), exchange_factory=lambda cfg: exchange)
        report = gateway.cancel("ccxt-1")
        assert exchange.cancelled == [("ccxt-1", None)]
        assert report.status is OrderStatus.CANCELLED
        assert report.quantity == 0.0
        gateway.disconnect()

    def test_cancel_partial_fill_reported(self) -> None:
        gateway = _make_gateway(
            cancel_response=_filled_response(status="canceled", filled=0.25, average=37000.0)
        )
        report = gateway.cancel("ccxt-1")
        assert report.status is OrderStatus.CANCELLED
        assert report.quantity == 0.25
        assert report.average_fill_price == 37000.0
        gateway.disconnect()

    def test_cancel_raises_when_venue_rejects(self) -> None:
        gateway = _make_gateway(cancel_raises=RuntimeError("insufficient permissions"))
        with pytest.raises(RuntimeError, match="insufficient permissions"):
            gateway.cancel("ccxt-1")
        gateway.disconnect()

    def test_cancel_raises_when_order_unknown(self) -> None:
        gateway = _make_gateway(cancel_response=_filled_response(status="oddball"))
        with pytest.raises(ValueError, match="unknown to venue"):
            gateway.cancel("ccxt-1")
        gateway.disconnect()
