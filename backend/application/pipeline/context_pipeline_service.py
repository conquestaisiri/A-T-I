# backend/application/pipeline/context_pipeline_service.py
"""Observation ingestion pipeline: bus -> persist -> context -> persist.

The pipeline consumes normalized :class:`ObservationEvent` instances from the
:class:`ObservationBus`, persists each one at-least-once, runs it through the
:class:`ContextBuilder`, and persists the resulting :class:`MarketContext`.
It is the single application-side owner of the ingest order; it contains no
business logic beyond that ordering.

Durability contract
-------------------
Every event read from the bus is written to the observation repository before
the context builder runs, so a crash mid-pipeline never loses a market event
that was already delivered to the process. If the repository rejects a replay
(``save`` returns ``False``) the event is still forwarded to the context
builder: replays must be visible to in-process state, only their *storage* is
deduplicated.
"""

from __future__ import annotations

import logging

from backend.application.interfaces.context_builder import ContextBuilder
from backend.application.interfaces.context_repository import ContextRepository
from backend.application.interfaces.observation_repository import ObservationRepository
from backend.application.interfaces.risk_feed import RiskFeed
from backend.application.interfaces.supervisor import Supervisor
from backend.application.pipeline.observation_enrichment import ObservationEnrichment
from backend.domain.context.market_context import MarketContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.observation.observation_bus import ObservationBus

logger = logging.getLogger(__name__)

# Event types that reflect live market-data freshness. News/macro/sentiment
# events do NOT count: a feed delivering only headlines must not keep the
# price-data gate green.
_MARKET_DATA_EVENT_TYPES = frozenset(
    {
        ObservationEventType.TRADE,
        ObservationEventType.TICKER,
        ObservationEventType.ORDER_BOOK,
        ObservationEventType.CANDLE,
    }
)


class ContextPipelineService:
    """Run the durable observation -> context pipeline for one process."""

    def __init__(
        self,
        bus: ObservationBus,
        context_builder: ContextBuilder,
        observation_repository: ObservationRepository,
        context_repository: ContextRepository,
        supervisor: Supervisor | None = None,
        enrichment: ObservationEnrichment | None = None,
        risk_feed: RiskFeed | None = None,
    ) -> None:
        self._bus = bus
        self._context_builder = context_builder
        self._observation_repository = observation_repository
        self._context_repository = context_repository
        self._supervisor = supervisor
        self._enrichment = enrichment
        self._risk_feed = risk_feed
        self._running = True

    async def start(self) -> None:
        """Consume events until :meth:`stop` is signalled.

        Blocks; run inside an ``asyncio`` task. Like :class:`ConsoleConsumer`,
        the loop exits only at the next event boundary after ``stop``.
        """
        logger.info("ContextPipelineService started")
        stream = self._bus.subscribe()
        try:
            async for event in stream:
                self.handle(event)
                if not self._running:
                    break
        finally:
            await stream.aclose()
        logger.info("ContextPipelineService stopped")

    def _record_freshness(self, event: ObservationEvent) -> None:
        """Tell the supervisor the latest known-good market timestamp for a symbol.

        Only genuine market-data events count; a supervisor is optional so
        replay/backtest determinism never depends on it (ADR 0007). This never
        raises: freshness tracking must not be able to kill the ingest path.
        """
        if self._supervisor is None or event.event_type not in _MARKET_DATA_EVENT_TYPES:
            return
        symbol = event.payload.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            return
        try:
            self._supervisor.record_observation(symbol, event.timestamp)
        except Exception:  # noqa: BLE001
            logger.exception("Supervisor failed to record freshness for %s", symbol)

    def _record_toxicity(self, event: ObservationEvent) -> None:
        """Feed signed order flow into the risk gate's VPIN veto.

        A TRADE event whose payload carries an aggressor ``side`` and
        ``quantity`` is converted to signed flow (buy positive, sell negative)
        and fed to the gate's toxicity estimator. This is a *risk signal* feed,
        mirroring :meth:`_record_freshness`: it never raises, so the ingest
        path can never be killed by the risk layer. The drive route publishes
        events without ``side``, so those contribute nothing — toxicity is fed
        only by venue trades where the aggressor side is actually known.
        """
        if self._risk_feed is None or event.event_type != ObservationEventType.TRADE:
            return
        payload = event.payload
        symbol = payload.get("symbol")
        side = payload.get("side")
        quantity = payload.get("quantity")
        if not isinstance(symbol, str) or not symbol:
            return
        if side not in ("buy", "sell") or not isinstance(quantity, (int, float)):
            return
        signed_flow = quantity if side == "buy" else -quantity
        try:
            self._risk_feed.record_toxicity_flow(symbol, signed_flow)
        except Exception:  # noqa: BLE001
            logger.exception("Risk feed failed to record toxicity for %s", symbol)

    def _enrich(self, event: ObservationEvent) -> None:
        """Feed order-book events into feature state before context is built.

        Order-book snapshots update micro-price state; L2 deltas update OFI and
        reach the tick recorder. The enrichment is the single documented writer
        for these caches (audit §19); without it the order-flow and micro-price
        features always read cold/default values. It never raises.
        """
        if self._enrichment is None:
            return
        self._enrichment.enrich(event)

    def handle(self, event: ObservationEvent) -> MarketContext:
        """Process one observation event through the durable ingest path.

        Synchronous single-event entry point used by the operator drive loop
        (and by :meth:`start` per event). Returns the built context so callers
        can run a decision on exactly what was persisted.
        """
        self._record_freshness(event)
        self._record_toxicity(event)
        try:
            self._observation_repository.save(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Observation repository save failed for %s: %s", event.event_key, exc)
            raise

        self._enrich(event)

        context = self._context_builder.handle(event)

        try:
            self._context_repository.save(context)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Context repository save failed for %s: %s", event.event_key, exc)
            raise

        logger.debug(
            "Persisted context for %s (events=%d, features=%d)",
            context.snapshot.symbol,
            len(context.snapshot.events),
            len(context.features),
        )
        return context

    def stop(self) -> None:
        """Signal the pipeline to stop after the current event boundary."""
        self._running = False
