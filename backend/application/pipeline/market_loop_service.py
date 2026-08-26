# backend/application/pipeline/market_loop_service.py
"""Self-feeding market-data loop (review action 3).

Drives live venue observations through the same durable pipeline as the
operator drive route: bus -> ingest (persist observation + context + enrich
microstructure state + feed supervisor freshness) -> decision (risk gate ->
paper simulation -> ledger). The loop is what makes the system *self-feeding*:
market data arriving from an adapter is traded automatically instead of
requiring an operator to POST every observation.

Safety posture (Constitution: keep the operator in charge, gated before live):
- The loop only ever runs against the paper path (same DecisionPipelineService
  the drive route uses, including the supervisor kill-switch / stale-data gate
  and the risk gate's veto authority).
- It is wired by the composition root ONLY when ``settings.ccxt_enabled`` and
  ``settings.ccxt_sandbox`` are both true; the default is off.
- One configurable symbol per loop; any adapter event for a different symbol is
  ignored, so a misconfigured feed can never trade unintended markets.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from backend.domain.context.market_context import MarketContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.observation.observation_bus import ObservationBus

if TYPE_CHECKING:
    from backend.application.pipeline.context_pipeline_service import ContextPipelineService
    from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
    from backend.application.simulation.paper_fill_engine import PaperFillEngine

logger = logging.getLogger(__name__)

# Event types that carry a tradeable price. Order-book snapshots enrich
# microstructure state (via the ingest pipeline) but do not, by themselves,
# drive a decision at a mark price.
_TRADEABLE_EVENT_TYPES = frozenset({ObservationEventType.TRADE, ObservationEventType.TICKER})


class MarketLoopService:
    """Consume the observation bus and run the full decision loop per event.

    Parameters
    ----------
    bus:
        Observation bus the venue adapter publishes into.
    ingest_pipeline:
        Durable observation -> context pipeline (persists observations and
        contexts, enriches microstructure state, feeds supervisor freshness).
    decision_pipeline:
        Decision path: reason -> risk gate -> paper simulation -> ledger.
    fill_engine:
        Paper fill engine whose mark price must follow the latest observation.
    symbol:
        The single symbol this loop trades. Events for other symbols are
        ingested (observability) but never acted upon.
    thread_lock:
        Optional shared lock serialising simulator mutations with the operator
        drive route. The loop runs on the event loop while the drive endpoint
        executes in a threadpool; without a shared lock the two could interleave
        on the paper simulator's state. ``None`` disables locking (single
        consumer, e.g. backtests).
    """

    def __init__(
        self,
        bus: ObservationBus,
        ingest_pipeline: ContextPipelineService,
        decision_pipeline: DecisionPipelineService,
        fill_engine: PaperFillEngine,
        *,
        symbol: str | None = None,
        symbols: list[str] | tuple[str, ...] | set[str] | None = None,
        thread_lock: Any | None = None,
        min_decision_interval_seconds: float = 30.0,
        pre_warm_fetcher: Callable[[str], Awaitable[list[dict[str, Any]] | None]] | None = None,
    ) -> None:
        if ingest_pipeline is None or decision_pipeline is None or fill_engine is None:
            raise ValueError("MarketLoopService requires ingest, decision and fill engine")
        self._bus = bus
        self._ingest = ingest_pipeline
        self._decision = decision_pipeline
        self._fill_engine = fill_engine
        # Multi-symbol support: ``symbols`` widens the tradeable universe while
        # ``symbol`` remains the single-symbol form (backward compatible).
        if symbols:
            self._symbols: frozenset[str] = frozenset(s.lower() for s in symbols)
        elif symbol:
            self._symbols = frozenset({symbol.lower()})
        else:
            self._symbols = frozenset()
        self._thread_lock = thread_lock
        # Optional injected pre-warm source (DI seam). When ``None`` the loop
        # falls back to its built-in MEXC candle fetch; tests and offline runs
        # inject a synthetic fetcher so ``start()`` stays hermetic.
        self._pre_warm_fetcher = pre_warm_fetcher
        # Decision cooldown (per symbol): without it every trade/tick drives a
        # full LLM round-trip, exhausting free-tier quotas within minutes and
        # flooding the ledger with near-duplicate proposals. Ingest still runs
        # per event; per-symbol keys let a multi-symbol loop trade each market
        # at its own cadence.
        self._min_decision_interval = max(0.0, min_decision_interval_seconds)
        self._last_decision_by_symbol: dict[str, float] = {}
        self._running = True
        self._events_seen = 0
        self._decisions_driven = 0
        self._decisions_skipped_cooldown = 0

    # -- Lifecycle --------------------------------------------------------------
    async def start(self) -> None:
        """Consume events until :meth:`stop` is signalled.

        Blocks; run inside an ``asyncio`` task. Like the ingest pipeline, the
        loop exits only at the next event boundary after ``stop``.
        """
        logger.info(
            "MarketLoopService started (symbols=%s)",
            ",".join(sorted(self._symbols)) or "*",
        )
        await self._pre_warm_features()
        stream = self._bus.subscribe()
        try:
            async for event in stream:
                # God-mode: never block the event loop on DB or LLM — offload
                # the entire handle (ingest + Omega + risk + ledger) to a thread
                # so the bus can keep draining at line-rate (sub-50ms).
                await asyncio.to_thread(self.handle, event)
                if not self._running:
                    break
        finally:
            await stream.aclose()
        logger.info("MarketLoopService stopped")

    async def _pre_warm_features(self) -> None:
        """Pre-feed historical candles so features are warm before going live.

        Without this, trend/momentum/volatility are cold (need 2-3+ observations)
        and the solver always returns stand_aside. Fetching the last 200 1h
        candles from MEXC gives the feature pipeline enough data to produce
        real signals immediately.
        """
        if not self._symbols:
            return
        try:
            for trade_symbol in sorted(self._symbols):
                symbol = trade_symbol.upper().replace("/", "").replace("_", "")
                try:
                    if self._pre_warm_fetcher is not None:
                        candles = await self._pre_warm_fetcher(trade_symbol)
                    else:
                        from backend.infrastructure.data_fabric.connectors.crypto.mexc import (
                            fetch_klines,
                        )

                        candles = await fetch_klines(symbol, "1h", 200)
                except Exception:  # noqa: BLE001 -- one cold symbol must not block the rest
                    logger.warning("Pre-warm failed for %s — warming naturally", symbol)
                    continue
                if not candles:
                    logger.warning("Pre-warm empty for %s — warming naturally", symbol)
                    continue
                logger.info(
                    "Pre-warming features for %s with %d historical candles",
                    symbol,
                    len(candles),
                )
                for i, candle in enumerate(candles):
                    ts = datetime.fromtimestamp(candle["time"] / 1000, tz=UTC)
                    event = ObservationEvent(
                        source_id="mexc",
                        source_name="MEXC",
                        event_type=ObservationEventType.TRADE,
                        timestamp=ts,
                        payload={
                            "symbol": trade_symbol.upper(),
                            "trade_id": f"warmup-{i}",
                            "price": candle["close"],
                            "quantity": candle.get("volume", 1.0),
                        },
                    )
                    # Run through ingest pipeline synchronously (no decision)
                    self._ingest.handle(event)
                logger.info("Feature pre-warm complete for %s", trade_symbol)
        except Exception:
            logger.exception("Feature pre-warm failed — features will warm up naturally")

    def stop(self) -> None:
        """Signal the loop to stop after the current event boundary."""
        self._running = False

    # -- Per-event processing ---------------------------------------------------
    def handle(self, event: ObservationEvent) -> None:
        """Run one observation through ingest, then decide at a mark price.

        Never raises: a single malformed event must not kill the loop. The
        ingest pipeline is the durable owner of persistence; the decision
        pipeline holds the risk gate and supervisor authority.
        """
        self._events_seen += 1
        try:
            context = self._ingest.handle(event)

            mark_price = self._mark_price(event)
            if mark_price is None:
                return
            event_symbol = context.snapshot.symbol.lower()
            if self._symbols and event_symbol not in self._symbols:
                return

            now = time.monotonic()
            # ``-inf`` sentinel: never-decided must never trip the cooldown.
            # Seeding with 0.0 made the first decision depend on machine
            # uptime (monotonic() is time-since-boot): a freshly booted host
            # -- including any CI runner or restarted trading box -- sat out
            # its first min_decision_interval_seconds entirely.
            last = self._last_decision_by_symbol.get(event_symbol, float("-inf"))
            if now - last < self._min_decision_interval:
                self._decisions_skipped_cooldown += 1
                return
            self._last_decision_by_symbol[event_symbol] = now

            if self._thread_lock is not None:
                with self._thread_lock:
                    self._drive_decision(context, mark_price)
            else:
                self._drive_decision(context, mark_price)
        except Exception:  # noqa: BLE001
            logger.exception("MarketLoopService failed to process event %s", event.event_key)

    def _drive_decision(self, context: MarketContext, mark_price: float) -> None:
        """Set the mark price and run one decision (already under the lock)."""
        self._fill_engine.set_mark_price(mark_price)
        step = self._decision.process(context, mark_price)
        self._decisions_driven += 1
        logger.info(
            "MarketLoop decision: %s result=%s verdict=%s mark=%.6f",
            step.proposal_id,
            step.result.value,
            step.risk_verdict,
            mark_price,
        )

    @staticmethod
    def _mark_price(event: ObservationEvent) -> float | None:
        """Derive a tradeable mark price from an event, or ``None``.

        Only trade and ticker events carry an unambiguous price today. This is
        deliberately expandable: a candle/order-book mid would slot in here.
        """
        if event.event_type not in _TRADEABLE_EVENT_TYPES:
            return None
        if event.event_type is ObservationEventType.TICKER:
            price = event.payload.get("last")
            if price is None:
                price = event.payload.get("close")
        else:
            price = event.payload.get("price")
        if isinstance(price, (int, float)) and price > 0:
            return float(price)
        return None

    # -- Observability ----------------------------------------------------------
    def stats(self) -> dict[str, int]:
        """Simple operational counters for the operator surface."""
        return {
            "events_seen": self._events_seen,
            "decisions_driven": self._decisions_driven,
            "decisions_skipped_cooldown": self._decisions_skipped_cooldown,
        }
