"""Simple asynchronous observation bus.

The bus provides a publish/subscribe mechanism for :class:`ObservationEvent`
instances. It is deliberately lightweight – a thin wrapper around
``asyncio.Queue`` that also tracks basic statistics.

Backpressure policy: the queue is **bounded**. ``publish`` awaits when the
queue is full, applying backpressure to the producer instead of dropping
events. Consumers must drain the queue (via ``subscribe``) in a timely
manner. This is the explicit, never-accidental policy required by the
Constitution.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator

from ...domain.observation.event import ObservationEvent


class ObservationBus:
    """Async publish‑subscribe bus for market observation events.

    * ``publish`` puts an :class:`ObservationEvent` onto an internal bounded
      queue, blocking (backpressure) when the queue is full.
    * ``subscribe`` returns an async iterator yielding events as they arrive.
    * Statistics such as processed count and average latency are kept for
      operational monitoring.
    """

    def __init__(self, maxsize: int = 1024):
        if maxsize <= 0:
            raise ValueError("ObservationBus maxsize must be a positive integer")
        self._queue: asyncio.Queue[ObservationEvent] = asyncio.Queue(maxsize=maxsize)
        self._processed: int = 0
        self._total_latency: float = 0.0
        # Per-source / per-symbol event flow counters for the operator surface.
        # Sources and symbols are bounded universes, so these cannot grow
        # without bound.
        self._by_source: dict[str, int] = {}
        self._by_symbol: dict[str, int] = {}

    async def publish(self, event: ObservationEvent) -> None:
        """Publish an event to the bus.

        Blocks when the queue is full, applying backpressure to the producer.
        The method records the ingestion timestamp for latency measurement.
        """
        event_timestamp = event.timestamp.timestamp()
        now = time.time()
        latency = now - event_timestamp
        self._total_latency += latency
        self._processed += 1
        source = getattr(event, "source_id", None) or "unknown"
        symbol = getattr(event, "symbol", None) or "unknown"
        self._by_source[source] = self._by_source.get(source, 0) + 1
        self._by_symbol[symbol] = self._by_symbol.get(symbol, 0) + 1
        await self._queue.put(event)

    async def subscribe(self) -> AsyncGenerator[ObservationEvent]:
        """Async iterator that yields events indefinitely.

        Consumers should break out of the loop on cancellation or when the
        application shuts down, then await ``aclose()`` on the generator.
        """
        while True:
            event = await self._queue.get()
            yield event
            self._queue.task_done()

    # Operational monitoring ------------------------------------------------------
    @property
    def processed_count(self) -> int:
        return self._processed

    @property
    def average_latency(self) -> float:
        if self._processed == 0:
            return 0.0
        return self._total_latency / self._processed

    @property
    def qsize(self) -> int:
        """Number of events currently buffered (awaiting consumption)."""
        return self._queue.qsize()

    @property
    def maxsize(self) -> int:
        """Upper bound on buffered events; ``0`` would mean unbounded."""
        return self._queue.maxsize

    @property
    def is_full(self) -> bool:
        """Whether the buffer is currently at capacity."""
        return self._queue.full()

    def stats(self) -> dict[str, object]:
        """Operational snapshot for the operator surface (bus + flow counters)."""
        top_symbols = sorted(self._by_symbol.items(), key=lambda kv: -kv[1])[:20]
        return {
            "processed": self._processed,
            "average_latency_ms": round(self.average_latency * 1000.0, 2),
            "queue_size": self.qsize,
            "queue_maxsize": self.maxsize,
            "queue_full": self.is_full,
            "sources": dict(sorted(self._by_source.items(), key=lambda kv: -kv[1])),
            "top_symbols": dict(top_symbols),
        }

    def __repr__(self) -> str:
        return (
            f"ObservationBus(processed={self._processed}, "
            f"avg_latency_ms={self.average_latency * 1000:.2f})"
        )
