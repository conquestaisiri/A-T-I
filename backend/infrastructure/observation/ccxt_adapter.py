# CCXT unified venue observation adapter.
#
# Wraps the CCXT async exchange (100+ crypto venues) behind the
# ``ObservationAdapter`` port. CCXT domain objects never leak into ATI core –
# every raw CCXT message is translated into the canonical ``ObservationEvent``
# at this boundary (ADR 0012, Integration Constitution §102-108).

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from ...domain.observation.adapter_interface import ObservationAdapter
from ...domain.observation.event import ObservationEvent, ObservationEventType
from ...infrastructure.observation.observation_bus import ObservationBus
from ..ccxt_config import CcxtVenueConfig

logger = logging.getLogger(__name__)

# CCXT unified message field constants -------------------------------------
_TRADE_FIELDS = ("side", "price", "amount")
_TICKER_FIELDS = ("bid", "ask")
_ORDERBOOK_FIELDS = ("bids", "asks")
_ORDER_BOOK_DEPTH = 10  # cap depth to keep payloads bounded


class _Exchange(Protocol):
    """Structural sub-set of the CCXT async exchange we depend on.

    Defined so tests can inject a lightweight fake without pulling the full
    CCXT runtime. Only the members used by ``CcxtObservationAdapter`` are
    declared here.
    """

    rate_limit: int
    symbol: str

    async def load_markets(self) -> None: ...
    async def close(self) -> None: ...
    def set_sandbox_mode(self, enabled: bool) -> None: ...

    async def fetch_trades(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]: ...
    async def fetch_ticker(self, symbol: str, **kwargs: Any) -> dict[str, Any]: ...
    async def fetch_order_book(self, symbol: str, **kwargs: Any) -> dict[str, Any]: ...

    async def watch_trades(self, symbol: str, **kwargs: Any) -> list[dict[str, Any]]: ...
    async def watch_ticker(self, symbol: str, **kwargs: Any) -> dict[str, Any]: ...
    async def watch_order_book(self, symbol: str, **kwargs: Any) -> dict[str, Any]: ...


def _ms_to_utc(timestamp_ms: int) -> datetime:
    """Convert a CCXT millisecond epoch to an aware UTC ``datetime``."""
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=UTC)


def _detect_event_type(raw: dict[str, Any]) -> ObservationEventType:
    """Infer the ``ObservationEventType`` from a CCXT message's shape.

    CCXT uses a unified schema per message type, so the presence of a few
    fields is enough to distinguish trades, tickers, and order books reliably.
    """
    if all(field in raw for field in _ORDERBOOK_FIELDS):
        return ObservationEventType.ORDER_BOOK
    if all(field in raw for field in _TRADE_FIELDS):
        return ObservationEventType.TRADE
    if all(field in raw for field in _TICKER_FIELDS):
        return ObservationEventType.TICKER
    raise ValueError(f"Unrecognisable CCXT payload shape: {sorted(raw.keys())}")


def _normalize_trade(source_id: str, source_name: str, raw: dict[str, Any]) -> ObservationEvent:
    """Translate a CCXT unified trade into an ``ObservationEvent``."""
    timestamp_ms = raw.get("timestamp")
    if not isinstance(timestamp_ms, (int, float)):
        raise ValueError("CCXT trade missing numeric 'timestamp'")
    return ObservationEvent(
        source_id=source_id,
        source_name=source_name,
        event_type=ObservationEventType.TRADE,
        timestamp=_ms_to_utc(int(timestamp_ms)),
        payload={
            "symbol": raw.get("symbol"),
            "trade_id": raw.get("id"),
            "price": float(raw["price"]),
            "quantity": float(raw["amount"]),
            "side": raw["side"],
            "cost": float(raw["cost"]) if raw.get("cost") is not None else None,
            "taker_or_maker": raw.get("takerOrMaker"),
            "fee_cost": float(raw["fee"]["cost"])
            if isinstance(raw.get("fee"), dict) and raw["fee"].get("cost") is not None
            else None,
        },
    )


def _normalize_ticker(source_id: str, source_name: str, raw: dict[str, Any]) -> ObservationEvent:
    """Translate a CCXT unified ticker into an ``ObservationEvent``."""
    timestamp_ms = raw.get("timestamp")
    if not isinstance(timestamp_ms, (int, float)):
        raise ValueError("CCXT ticker missing numeric 'timestamp'")
    return ObservationEvent(
        source_id=source_id,
        source_name=source_name,
        event_type=ObservationEventType.TICKER,
        timestamp=_ms_to_utc(int(timestamp_ms)),
        payload={
            "symbol": raw.get("symbol"),
            "bid": raw.get("bid"),
            "ask": raw.get("ask"),
            "last": raw.get("last"),
            "high": raw.get("high"),
            "low": raw.get("low"),
            "open": raw.get("open"),
            "close": raw.get("close"),
            "vwap": raw.get("vwap"),
            "change": raw.get("change"),
            "percentage": raw.get("percentage"),
            "base_volume": raw.get("baseVolume"),
            "quote_volume": raw.get("quoteVolume"),
        },
    )


def _normalize_order_book(
    source_id: str, source_name: str, raw: dict[str, Any]
) -> ObservationEvent:
    """Translate a CCXT unified order book into an ``ObservationEvent``."""
    timestamp_ms = raw.get("timestamp")
    if not isinstance(timestamp_ms, (int, float)):
        raise ValueError("CCXT order book missing numeric 'timestamp'")
    bids = raw.get("bids", [])[:_ORDER_BOOK_DEPTH]
    asks = raw.get("asks", [])[:_ORDER_BOOK_DEPTH]
    return ObservationEvent(
        source_id=source_id,
        source_name=source_name,
        event_type=ObservationEventType.ORDER_BOOK,
        timestamp=_ms_to_utc(int(timestamp_ms)),
        payload={
            "symbol": raw.get("symbol"),
            "bids": bids,
            "asks": asks,
            "nonce": raw.get("nonce"),
        },
    )


def normalize_ccxt(source_id: str, source_name: str, raw: dict[str, Any]) -> ObservationEvent:
    """Pure entry point: translate any CCXT unified message.

    Dispatches on payload shape via :func:`_detect_event_type`. Raises
    ``ValueError`` if the payload cannot be interpreted.
    """
    event_type = _detect_event_type(raw)
    if event_type is ObservationEventType.TRADE:
        return _normalize_trade(source_id, source_name, raw)
    if event_type is ObservationEventType.TICKER:
        return _normalize_ticker(source_id, source_name, raw)
    return _normalize_order_book(source_id, source_name, raw)


def compute_order_book_delta(
    prev_bids: list[list[float]],
    prev_asks: list[list[float]],
    new_bids: list[list[float]],
    new_asks: list[list[float]],
) -> dict[str, list[dict[str, Any]]]:
    """Compute the delta between two order book snapshots.

    Returns a dict with 'bids' and 'asks' lists, each containing
    changes as dicts:
    {'price': float, 'old_size': float, 'new_size': float, 'size': float,
     'action': 'add'|'update'|'remove'}.

    Both ``old_size`` and ``new_size`` are always present so consumers can
    compute the exact size change ``new_size - old_size`` (an OFI delta needs
    the removed/previous size, not just the new one). ``size`` mirrors
    ``new_size`` for backward compatibility with legacy consumers.
    """
    prev_bid_dict = {float(bid[0]): float(bid[1]) for bid in prev_bids if len(bid) >= 2}
    prev_ask_dict = {float(ask[0]): float(ask[1]) for ask in prev_asks if len(ask) >= 2}
    new_bid_dict = {float(bid[0]): float(bid[1]) for bid in new_bids if len(bid) >= 2}
    new_ask_dict = {float(ask[0]): float(ask[1]) for ask in new_asks if len(ask) >= 2}

    delta_bids: list[dict[str, Any]] = []
    delta_asks: list[dict[str, Any]] = []

    # Bids
    for price, size in new_bid_dict.items():
        if price in prev_bid_dict:
            if size != prev_bid_dict[price]:
                delta_bids.append(
                    {
                        "price": price,
                        "old_size": prev_bid_dict[price],
                        "new_size": size,
                        "size": size,
                        "action": "update",
                    }
                )
        else:
            delta_bids.append(
                {"price": price, "old_size": 0.0, "new_size": size, "size": size, "action": "add"}
            )
    for price in prev_bid_dict:
        if price not in new_bid_dict:
            delta_bids.append(
                {
                    "price": price,
                    "old_size": prev_bid_dict[price],
                    "new_size": 0.0,
                    "size": 0.0,
                    "action": "remove",
                }
            )

    # Asks
    for price, size in new_ask_dict.items():
        if price in prev_ask_dict:
            if size != prev_ask_dict[price]:
                delta_asks.append(
                    {
                        "price": price,
                        "old_size": prev_ask_dict[price],
                        "new_size": size,
                        "size": size,
                        "action": "update",
                    }
                )
        else:
            delta_asks.append(
                {"price": price, "old_size": 0.0, "new_size": size, "size": size, "action": "add"}
            )
    for price in prev_ask_dict:
        if price not in new_ask_dict:
            delta_asks.append(
                {
                    "price": price,
                    "old_size": prev_ask_dict[price],
                    "new_size": 0.0,
                    "size": 0.0,
                    "action": "remove",
                }
            )

    return {"bids": delta_bids, "asks": delta_asks}


class CcxtObservationAdapter(ObservationAdapter):
    """CCXT-backed implementation of the :class:`ObservationAdapter` port.

    One instance per (venue, symbol) pair. Messages from the venue are
    translated into :class:`ObservationEvent` at the boundary – no CCXT domain
    object ever reaches the bus.

    Parameters
    ----------
    config:
        Venue-specific configuration.
    bus:
        Observation bus to publish normalised events to.
    symbol:
        Venue symbol to observe. Defaults to ``config.default_symbol``.
    exchange_factory:
        Callable returning a CCXT async exchange instance. Defaults to
        instantiating the real CCXT exchange from ``config.venue_id`` –
        injected so tests run without network or the full CCXT runtime.
    source_id:
        Source Registry identifier. Defaults to ``"{venue_id}_{symbol}"``.
    source_name:
        Human-readable name. Defaults to the venue id.
    """

    def __init__(
        self,
        config: CcxtVenueConfig,
        bus: ObservationBus,
        symbol: str | None = None,
        exchange_factory: Callable[[CcxtVenueConfig], _Exchange] | None = None,
        source_id: str | None = None,
        source_name: str | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self.symbol = symbol or config.default_symbol
        self.source_id = source_id or f"{config.venue_id}_{self.symbol}"
        self.source_name = source_name or config.venue_id
        self._exchange_factory = exchange_factory or _default_exchange_factory
        self._exchange: _Exchange | None = None
        self._subscribed: list[str] = []
        self._connected = asyncio.Event()
        self._stop = asyncio.Event()
        self._reconnect_attempts = 0
        self._max_backoff = 30
        self._initial_backoff = 1
        # Order book delta tracking
        self._prev_bids: list[list[float]] = []
        self._prev_asks: list[list[float]] = []
        self._delta_count = 0

    # -- ObservationAdapter port ------------------------------------------------
    async def connect(self) -> None:
        """Instantiate the CCXT exchange, load markets, enable sandbox."""
        backoff: float = float(self._initial_backoff)
        while not self._stop.is_set():
            try:
                logger.info("CCXT %s: creating exchange for %s", self._config.venue_id, self.symbol)
                self._exchange = self._exchange_factory(self._config)
                await self._exchange.load_markets()
                if self._config.sandbox:
                    self._exchange.set_sandbox_mode(True)
                self._connected.set()
                self._reconnect_attempts = 0
                logger.info("CCXT %s: connected for %s", self._config.venue_id, self.symbol)
                return
            except Exception as exc:
                self._connected.clear()
                self._reconnect_attempts += 1
                logger.warning(
                    "CCXT %s connection failed (attempt %d): %s – retry in %ss",
                    self._config.venue_id,
                    self._reconnect_attempts,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2 * (0.5 + random.random()), self._max_backoff)

    async def disconnect(self) -> None:
        """Close the exchange and signal the receive loop to stop."""
        self._stop.set()
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception as exc:  # pragma: no cover – cleanup path
                logger.warning("CCXT %s close error: %s", self._config.venue_id, exc)
        self._connected.clear()
        logger.info("CCXT %s: disconnected", self._config.venue_id)

    async def subscribe(self, event_types: list[str]) -> None:
        """Record the requested event types; the receive loop honours them."""
        self._subscribed = list(event_types)
        logger.debug("CCXT %s: subscribed to %s", self._config.venue_id, event_types)

    def normalize(self, raw: dict[str, Any]) -> ObservationEvent:
        """Translate a raw CCXT message – delegates to :func:`normalize_ccxt`."""
        return normalize_ccxt(self.source_id, self.source_name, raw)

    async def health(self) -> dict[str, Any]:
        """Return a health snapshot for operational monitoring."""
        rate_limit = getattr(self._exchange, "rate_limit", None)
        return {
            "connected": self._connected.is_set(),
            "stop_requested": self._stop.is_set(),
            "reconnect_attempts": self._reconnect_attempts,
            "venue_id": self._config.venue_id,
            "symbol": self.symbol,
            "mode": "websocket" if self._config.enable_websocket else "polling",
            "rate_limit_ms": rate_limit,
            "subscribed": list(self._subscribed),
        }

    # -- Receive loop -----------------------------------------------------------
    async def _receive_loop(self) -> None:
        """Continuously observe the venue and publish normalised events."""
        while not self._stop.is_set():
            if not self._connected.is_set():
                await self.connect()
                continue
            try:
                if self._config.enable_websocket:
                    await self._watch_once()
                else:
                    await self._poll_once()
                    await asyncio.sleep(self._poll_interval())
            except Exception as exc:
                logger.warning(
                    "CCXT %s receive error: %s – reconnecting", self._config.venue_id, exc
                )
                self._connected.clear()
                await self.connect()

    def _poll_interval(self) -> float:
        """Seconds between REST polls, respecting the rate-limit buffer."""
        rate_limit_ms = getattr(self._exchange, "rate_limit", 1000) or 1000
        interval = rate_limit_ms / 1000.0 / self._config.rate_limit_buffer
        return max(interval, 0.5)

    async def _watch_once(self) -> None:
        """Pull one batch per subscribed stream over CCXT ``watch_*`` with FIRST_COMPLETED."""
        assert self._exchange is not None
        tasks: dict[asyncio.Task[Any], str] = {}
        for event_type in self._subscribed or ["trade"]:
            if event_type == "trade":
                tasks[asyncio.create_task(self._exchange.watch_trades(self.symbol))] = "trade"
            elif event_type == "ticker":
                tasks[asyncio.create_task(self._exchange.watch_ticker(self.symbol))] = "ticker"
            elif event_type == "order_book":
                tasks[asyncio.create_task(self._exchange.watch_order_book(self.symbol))] = (
                    "order_book"
                )
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks.keys(), return_when=asyncio.FIRST_COMPLETED)
        for p in pending:
            p.cancel()
        for task in done:
            et = tasks[task]
            try:
                result = task.result()
            except Exception:
                continue
            if et == "trade":
                for raw in result:
                    await self._publish_raw(raw)
            elif et == "ticker":
                await self._publish_raw(result)
            elif et == "order_book":
                await self._publish_raw_with_delta(result)

    async def _poll_once(self) -> None:
        """Fetch one snapshot per subscribed stream over REST."""
        assert self._exchange is not None
        for event_type in self._subscribed or ["trade"]:
            if event_type == "trade":
                trades = await self._exchange.fetch_trades(self.symbol)
                for raw in trades:
                    await self._publish_raw(raw)
            elif event_type == "ticker":
                raw = await self._exchange.fetch_ticker(self.symbol)
                await self._publish_raw(raw)
            elif event_type == "order_book":
                raw = await self._exchange.fetch_order_book(self.symbol)
                await self._publish_raw_with_delta(raw)

    async def _publish_raw_with_delta(self, raw: dict[str, Any]) -> None:
        """Normalise a raw order book message, compute delta, and publish both."""
        try:
            # Publish the full snapshot as before
            event = self.normalize(raw)
        except ValueError as exc:
            logger.warning("CCXT %s normalisation skipped: %s", self._config.venue_id, exc)
            return
        await self._bus.publish(event)

        # Compute and publish delta if we have previous state
        new_bids = raw.get("bids", [])[:_ORDER_BOOK_DEPTH]
        new_asks = raw.get("asks", [])[:_ORDER_BOOK_DEPTH]
        if self._prev_bids or self._prev_asks:
            delta = compute_order_book_delta(self._prev_bids, self._prev_asks, new_bids, new_asks)
            if delta["bids"] or delta["asks"]:
                self._delta_count += 1
                delta_event = ObservationEvent(
                    source_id=self.source_id,
                    source_name=self.source_name,
                    event_type=ObservationEventType.ORDER_BOOK,
                    timestamp=self.normalize(raw).timestamp,
                    payload={
                        "symbol": raw.get("symbol"),
                        "delta": True,
                        "delta_seq": self._delta_count,
                        # Deltas computed by diffing consecutive snapshots are
                        # synthetic: this adapter has no native sequence stream.
                        # "delta_seq" is the local synthetic counter; when a
                        # venue supplies a native sequence (e.g. Binance "u"),
                        # it is carried as "nonce" and a future native-seq
                        # capability can replace the synthetic diffing path.
                        "synthetic": True,
                        "bids": delta["bids"],
                        "asks": delta["asks"],
                        "nonce": raw.get("nonce"),
                    },
                )
                await self._bus.publish(delta_event)

        # Update previous state
        self._prev_bids = new_bids
        self._prev_asks = new_asks

    async def _publish_raw(self, raw: dict[str, Any]) -> None:
        """Normalise a single raw message and push it onto the bus."""
        try:
            event = self.normalize(raw)
        except ValueError as exc:
            logger.warning("CCXT %s normalisation skipped: %s", self._config.venue_id, exc)
            return
        await self._bus.publish(event)

    # -- Lifecycle helpers ------------------------------------------------------
    async def start(self) -> None:
        """Connect and run the receive loop until cancelled."""
        await self.connect()
        await self._receive_loop()

    async def stop(self) -> None:
        """Stop the adapter gracefully."""
        await self.disconnect()


def _default_exchange_factory(config: CcxtVenueConfig) -> _Exchange:
    """Instantiate a real CCXT async exchange from the configuration."""
    import ccxt.async_support as ccxt_async

    exchange_cls = getattr(ccxt_async, config.venue_id, None)
    if exchange_cls is None:
        raise ValueError(
            f"CCXT has no exchange id '{config.venue_id}'. "
            f"See https://docs.ccxt.com/#/exchanges for valid ids."
        )
    instance: _Exchange = exchange_cls(
        {
            "apiKey": config.api_key,
            "secret": config.secret,
            "enableRateLimit": True,
            "options": {"defaultType": config.market_type},
        }
    )
    return instance
