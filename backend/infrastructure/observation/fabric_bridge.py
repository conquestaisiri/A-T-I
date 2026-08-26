"""Bridge data-fabric events onto the observation bus.

The decision loop consumes :class:`ObservationEvent` instances from the
:class:`ObservationBus`, but the data fabric emits richer
:class:`NormalizedEvent` instances on its own :class:`EnhancedEventBus`. This
adapter subscribes to the fabric bus, translates each normalized event into an
observation event (the schema the ingest/decision pipeline understands), and
publishes it to the observation bus.

Only market-data events that carry a concrete symbol are bridged
(trade/quote/book/candle). News, macro, and sentiment events are persisted by
the fabric itself and carry no venue symbol; routing them onto the
observation -> context -> decision path would poison the context builder
(which requires a ``symbol`` payload field). The bridge therefore skips them.

Backpressure: the fabric bus applies backpressure to connectors when its queue
is full, and the observation bus applies backpressure to this bridge. The
bridge is deliberately the only consumer of the fabric bus on the paper path so
no event is lost silently.
"""

from __future__ import annotations

import logging
from typing import Any

from backend.domain.data_fabric.envelope import NormalizedEvent
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.data_fabric.event_bus import EnhancedEventBus
from backend.infrastructure.observation.observation_bus import ObservationBus

logger = logging.getLogger(__name__)

# Fabric event_type strings that map onto the observation pipeline. The
# market-data types are the only ones worth bridging: they carry a symbol and
# drive context/decisions.
_FABRIC_TO_OBSERVATION: dict[str, ObservationEventType] = {
    "trade": ObservationEventType.TRADE,
    "quote": ObservationEventType.TICKER,
    "book": ObservationEventType.ORDER_BOOK,
    "candle": ObservationEventType.CANDLE,
}


class FabricObservationBridge:
    """Forward trade/quote/book/candle fabric events onto the observation bus.

    Parameters
    ----------
    fabric_bus:
        The data fabric's event bus (source of :class:`NormalizedEvent`).
    observation_bus:
        The observation bus (sink for :class:`ObservationEvent`).
    """

    def __init__(self, fabric_bus: EnhancedEventBus, observation_bus: ObservationBus) -> None:
        if fabric_bus is None or observation_bus is None:
            raise ValueError("FabricObservationBridge requires a fabric bus and observation bus")
        self._fabric_bus = fabric_bus
        self._observation_bus = observation_bus
        self._running = True
        self._bridged = 0
        self._skipped = 0

    async def start(self) -> None:
        """Consume fabric events and translate until :meth:`stop` is signalled.

        Blocks; run inside an ``asyncio`` task. Exits at the next event
        boundary after ``stop``, mirroring the ingest pipeline's lifecycle.
        """
        logger.info("FabricObservationBridge started")
        stream = self._fabric_bus.subscribe()
        try:
            async for event in stream:
                if not self._running:
                    break
                observation = _to_observation_event(event)
                if observation is None:
                    self._skipped += 1
                    continue
                await self._observation_bus.publish(observation)
                self._bridged += 1
        finally:
            await stream.aclose()
        logger.info(
            "FabricObservationBridge stopped (bridged=%d skipped=%d)", self._bridged, self._skipped
        )

    def stop(self) -> None:
        """Signal the bridge to stop after the current event boundary."""
        self._running = False

    def stats(self) -> dict[str, int]:
        return {"bridged": self._bridged, "skipped": self._skipped}


def _to_observation_event(event: NormalizedEvent) -> ObservationEvent | None:
    """Translate one normalized fabric event, or ``None`` if not bridgeable."""
    event_type = _FABRIC_TO_OBSERVATION.get(event.event_type)
    if event_type is None:
        return None
    symbol = event.symbol
    if not symbol:
        return None

    payload = _build_payload(event, event_type, symbol)
    if payload is None:
        return None
    return ObservationEvent(
        source_id=event.source_id,
        source_name=event.source_name,
        event_type=event_type,
        timestamp=event.event_time,
        payload=payload,
    )


def _build_payload(
    event: NormalizedEvent, event_type: ObservationEventType, symbol: str
) -> dict[str, Any] | None:
    """Build an observation payload matching the CCXT adapter's schema.

    The MarketLoopService reads ``price`` for TRADE events and ``last``/``close``
    for TICKER events; the context builder requires the ``symbol`` key. Mirror
    the CCXT adapter shapes so downstream consumers behave identically whether
    the event arrived via the fabric or via CCXT.
    """
    if event_type is ObservationEventType.TRADE:
        return {
            "symbol": symbol,
            "trade_id": event.payload.get("trade_id"),
            "price": event.price,
            "quantity": event.quantity,
            "side": event.side,
        }
    if event_type is ObservationEventType.TICKER:
        # Fabric quotes carry the mid as ``price`` and the top of book as
        # ``bid``/``ask``; expose ``last`` and ``close`` for the mark-price path.
        mid = event.price
        return {
            "symbol": symbol,
            "bid": event.bid,
            "ask": event.ask,
            "last": mid,
            "close": mid,
        }
    if event_type is ObservationEventType.ORDER_BOOK:
        return {
            "symbol": symbol,
            "bids": event.payload.get("bids", []),
            "asks": event.payload.get("asks", []),
            "nonce": event.payload.get("last_update_id"),
        }
    if event_type is ObservationEventType.CANDLE:
        return {
            "symbol": symbol,
            **{k: event.payload.get(k) for k in ("open", "high", "low", "close", "volume")},
            "interval": event.payload.get("interval", "1m"),
        }
    return None
