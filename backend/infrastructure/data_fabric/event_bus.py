"""Enhanced event bus with health monitoring, replay, and persistence."""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import time
from collections import defaultdict
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from ...domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from ...domain.data_fabric.quality import HealthSnapshot


class EnhancedEventBus:
    """Enhanced async publish-subscribe bus with health monitoring and replay.

    Features:
    - Bounded per-subscriber queues with backpressure (explicit policy)
    - Fan-out: every event is delivered to *every* subscriber (true pub/sub)
    - Per-source health tracking
    - Event persistence for replay
    - Latency tracking
    - Cross-source statistics
    """

    def __init__(
        self,
        maxsize: int = 10000,
        persistence_enabled: bool = True,
        db_path: str = "data/trading_intelligence.db",
    ) -> None:
        if maxsize <= 0:
            raise ValueError("EventBus maxsize must be positive")
        # Each subscriber owns a private bounded queue; publish fans out to all.
        self._subscribers: dict[asyncio.Queue[NormalizedEvent], None] = {}
        self._maxsize = maxsize
        self._persistence_enabled = persistence_enabled

        # Statistics
        self._processed: int = 0
        self._total_latency_ms: float = 0.0
        self._by_source: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "total_latency_ms": 0.0,
                "last_event_at": None,
                "errors": 0,
            }
        )

        # Health monitoring
        self._source_health: dict[str, HealthSnapshot] = {}
        self._connection_states: dict[str, str] = {}
        self._quality_monitor: Any | None = None

        # Persistence (optional)
        self._db_path = db_path
        self._conn: sqlite3.Connection | None = None
        self._persist_queue: asyncio.Queue[NormalizedEvent] = asyncio.Queue()
        self._persist_raw_queue: asyncio.Queue[RawEnvelope] = asyncio.Queue()
        self._writer_task: asyncio.Task[None] | None = None
        if persistence_enabled:
            self._init_persistence()
            try:
                loop = asyncio.get_running_loop()
                self._writer_task = loop.create_task(self._batch_writer())
            except RuntimeError:
                pass

    def _init_persistence(self) -> None:
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.row_factory = sqlite3.Row

        # Raw envelopes table
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS raw_envelopes (
                envelope_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                venue TEXT,
                data_plane TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                received_at TEXT NOT NULL,
                source_timestamp TEXT,
                raw_payload TEXT NOT NULL,
                raw_headers TEXT NOT NULL,
                sequence INTEGER,
                stream_id TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_raw_envelopes_source_time
                ON raw_envelopes (source_id, received_at);

            CREATE TABLE IF NOT EXISTS normalized_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_name TEXT NOT NULL,
                venue TEXT,
                data_plane TEXT NOT NULL,
                asset_class TEXT NOT NULL,
                event_time TEXT NOT NULL,
                source_timestamp TEXT,
                received_at TEXT NOT NULL,
                ingested_at TEXT NOT NULL,
                processed_at TEXT,
                instrument_id TEXT,
                symbol TEXT,
                base_asset TEXT,
                quote_asset TEXT,
                payload TEXT NOT NULL,
                sequence INTEGER,
                stream_id TEXT,
                source_latency_ms REAL,
                ingestion_latency_ms REAL,
                processing_latency_ms REAL,
                quality_score REAL,
                raw_envelope_id TEXT,
                price REAL,
                bid REAL,
                ask REAL,
                quantity REAL,
                side TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_normalized_events_instrument_time
                ON normalized_events (instrument_id, event_time);
            CREATE INDEX IF NOT EXISTS idx_normalized_events_source_time
                ON normalized_events (source_id, event_time);
            CREATE INDEX IF NOT EXISTS idx_normalized_events_type_time
                ON normalized_events (event_type, event_time);
        """)
        self._conn.commit()

    async def _batch_writer(self) -> None:
        """Single-writer batch persistence every 50ms (WAL, no conn contention)."""
        while True:
            await asyncio.sleep(0.05)
            batch: list[NormalizedEvent] = []
            raw_batch: list[RawEnvelope] = []
            while not self._persist_queue.empty() and len(batch) < 100:
                try:
                    batch.append(self._persist_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            while not self._persist_raw_queue.empty() and len(raw_batch) < 100:
                try:
                    raw_batch.append(self._persist_raw_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if not batch and not raw_batch:
                continue
            try:
                if batch:
                    await asyncio.to_thread(self._flush_batch, batch)
                if raw_batch:
                    await asyncio.to_thread(self._flush_raw_batch, raw_batch)
            except Exception:
                pass

    def _flush_batch(self, batch: list[NormalizedEvent]) -> None:
        import json

        if not self._conn or not batch:
            return
        try:
            self._conn.executemany(
                """INSERT OR REPLACE INTO normalized_events
                (event_id, event_type, source_id, source_name, venue, data_plane,
                asset_class, event_time, source_timestamp, received_at, ingested_at,
                processed_at, instrument_id, symbol, base_asset, quote_asset, payload,
                sequence, stream_id, source_latency_ms, ingestion_latency_ms,
                processing_latency_ms, quality_score, raw_envelope_id, price, bid, ask,
                quantity, side)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        e.event_id,
                        e.event_type,
                        e.source_id,
                        e.source_name,
                        e.venue,
                        e.data_plane.value,
                        e.asset_class.value,
                        e.event_time.isoformat(),
                        e.source_timestamp.isoformat() if e.source_timestamp else None,
                        e.received_at.isoformat(),
                        e.ingested_at.isoformat(),
                        e.processed_at.isoformat() if e.processed_at else None,
                        e.instrument_id,
                        e.symbol,
                        e.base_asset,
                        e.quote_asset,
                        json.dumps(e.payload),
                        e.sequence,
                        e.stream_id,
                        e.source_latency_ms,
                        e.ingestion_latency_ms,
                        e.processing_latency_ms,
                        e.quality_score,
                        e.raw_envelope_id,
                        e.price,
                        e.bid,
                        e.ask,
                        e.quantity,
                        e.side,
                    )
                    for e in batch
                ],
            )
            self._conn.commit()
        except Exception:
            pass

    def _flush_raw_batch(self, batch: list[RawEnvelope]) -> None:
        import json

        if not self._conn or not batch:
            return
        try:
            self._conn.executemany(
                """INSERT OR REPLACE INTO raw_envelopes
                (envelope_id, source_id, source_name, venue, data_plane, asset_class, received_at, source_timestamp, raw_payload, raw_headers, sequence, stream_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        e.envelope_id,
                        e.source_id,
                        e.source_name,
                        e.venue,
                        e.data_plane.value,
                        e.asset_class.value,
                        e.received_at.isoformat(),
                        e.source_timestamp.isoformat() if e.source_timestamp else None,
                        json.dumps(e.raw_payload),
                        json.dumps(e.raw_headers),
                        e.sequence,
                        e.stream_id,
                    )
                    for e in batch
                ],
            )
            self._conn.commit()
        except Exception:
            pass

    async def publish(self, event: NormalizedEvent) -> None:
        """Publish a normalized event to the bus.

        Blocks when queue is full (backpressure).
        Records latency and updates source health.
        """
        now = time.time()
        event_timestamp = event.event_time.timestamp()
        latency_ms = (now - event_timestamp) * 1000

        self._total_latency_ms += latency_ms
        self._processed += 1

        source_stats = self._by_source[event.source_id]
        source_stats["count"] += 1
        source_stats["total_latency_ms"] += latency_ms
        source_stats["last_event_at"] = datetime.now(UTC)

        # Update health snapshot
        self._update_health_on_publish(event, latency_ms)
        if self._quality_monitor is not None:
            try:
                self._quality_monitor.record_event(event)
            except Exception:
                pass

        # Fan out first — hot path delivers before persistence so subscribers
        # never wait for SQLite fsync (God-mode: market path never blocked).
        for queue in list(self._subscribers):
            await queue.put(event)

        # Persist off hot path via batch queue (single writer, 50ms batch)
        if self._persistence_enabled and self._conn:
            if self._writer_task is None or self._writer_task.done():
                try:
                    loop = asyncio.get_running_loop()
                    self._writer_task = loop.create_task(self._batch_writer())
                except RuntimeError:
                    pass
            with contextlib.suppress(asyncio.QueueFull):
                self._persist_queue.put_nowait(event)

    async def publish_raw(self, envelope: RawEnvelope) -> None:
        """Persist raw envelope without publishing to bus."""
        if self._persistence_enabled and self._conn:
            try:
                self._persist_raw_queue.put_nowait(envelope)
            except asyncio.QueueFull:
                pass

    async def await_flush(self, timeout: float = 2.0) -> None:
        """Block until persist queues drain (test helper)."""
        # If writer not running (e.g., no event loop at init in tests), flush directly
        if self._writer_task is None or self._writer_task.done():
            batch: list[NormalizedEvent] = []
            raw_batch: list[RawEnvelope] = []
            while not self._persist_queue.empty():
                try:
                    batch.append(self._persist_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            while not self._persist_raw_queue.empty():
                try:
                    raw_batch.append(self._persist_raw_queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if batch:
                await asyncio.to_thread(self._flush_batch, batch)
            if raw_batch:
                await asyncio.to_thread(self._flush_raw_batch, raw_batch)
            return
        start = time.time()
        while (
            not self._persist_queue.empty() or not self._persist_raw_queue.empty()
        ) and time.time() - start < timeout:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.06)

    def _update_health_on_publish(self, event: NormalizedEvent, latency_ms: float) -> None:
        """Update health snapshot on event publish."""
        health = self._source_health.get(event.source_id)
        if health is None:
            health = HealthSnapshot(
                source_id=event.source_id,
                source_name=event.source_name,
                venue=event.venue,
                asset_class=event.asset_class.value,
                connection_state="LIVE",
                freshness_state="LIVE",
                latency_ms=latency_ms,
                messages_per_sec=0.0,
            )
            self._source_health[event.source_id] = health
        else:
            # Update existing - create new immutable snapshot
            self._source_health[event.source_id] = HealthSnapshot(
                source_id=health.source_id,
                source_name=health.source_name,
                venue=health.venue,
                asset_class=health.asset_class,
                connection_state=health.connection_state,
                freshness_state=health.freshness_state,
                timestamp=datetime.now(UTC),
                latency_ms=latency_ms,
                messages_per_sec=health.messages_per_sec,
                queue_depth=self._queue_depth(),
                reconnect_count=health.reconnect_count,
                last_error=health.last_error,
                last_event_at=datetime.now(UTC),
            )

    def _queue_depth(self) -> int:
        """Total events buffered across all subscriber queues.

        With fan-out each subscriber holds a copy, so the aggregate is the
        sum of all per-subscriber buffers. This bounds the memory the bus may
        hold on behalf of its consumers (backpressure policy).
        """
        return sum(queue.qsize() for queue in self._subscribers)

    async def _persist_event(self, event: NormalizedEvent) -> None:
        """Persist normalized event to SQLite without blocking the event loop.

        Delegates the blocking SQLite write to a thread via ``asyncio.to_thread``
        so the hot publish path never blocks on fsync. Batch persistence is the
        primary path; this helper remains for direct single-event callers.
        """
        if self._conn is None:
            return
        try:
            await asyncio.to_thread(self._flush_batch, [event])
        except Exception:
            pass  # Don't let persistence failures block the bus

    async def _persist_raw(self, envelope: RawEnvelope) -> None:
        """Persist raw envelope to SQLite without blocking the event loop."""
        if self._conn is None:
            return
        try:
            await asyncio.to_thread(self._flush_raw_batch, [envelope])
        except Exception:
            pass

    def subscribe(self) -> AsyncGenerator[NormalizedEvent]:
        """Async iterator yielding events.

        Each call registers a private bounded queue and receives a copy of
        every published event (fan-out). On generator close (including task
        cancellation) the subscriber is deregistered so no queue leaks.
        """
        # The queue is created eagerly (not inside the async generator body)
        # so events published before the first ``anext()`` are still delivered.
        queue: asyncio.Queue[NormalizedEvent] = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers[queue] = None

        async def _iterate() -> AsyncGenerator[NormalizedEvent]:
            try:
                while True:
                    event = await queue.get()
                    yield event
                    queue.task_done()
            finally:
                self._subscribers.pop(queue, None)

        return _iterate()

    async def replay(
        self,
        start_time: datetime,
        end_time: datetime,
        instruments: list[str] | None = None,
        event_types: list[str] | None = None,
        sources: list[str] | None = None,
    ) -> AsyncGenerator[NormalizedEvent]:
        """Replay historical events from persistence."""
        if not self._persistence_enabled or not self._conn:
            raise RuntimeError("Persistence not enabled")

        import json

        query = """
            SELECT * FROM normalized_events
            WHERE event_time >= ? AND event_time <= ?
        """
        params = [start_time.isoformat(), end_time.isoformat()]

        if instruments:
            placeholders = ",".join("?" * len(instruments))
            query += f" AND instrument_id IN ({placeholders})"
            params.extend(instruments)
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            query += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
        if sources:
            placeholders = ",".join("?" * len(sources))
            query += f" AND source_id IN ({placeholders})"
            params.extend(sources)

        query += " ORDER BY event_time ASC"

        rows = self._conn.execute(query, params).fetchall()
        for row in rows:
            event = NormalizedEvent(
                event_id=row["event_id"],
                event_type=row["event_type"],
                source_id=row["source_id"],
                source_name=row["source_name"],
                venue=row["venue"],
                data_plane=row["data_plane"],
                asset_class=row["asset_class"],
                event_time=datetime.fromisoformat(row["event_time"]),
                source_timestamp=datetime.fromisoformat(row["source_timestamp"])
                if row["source_timestamp"]
                else None,
                received_at=datetime.fromisoformat(row["received_at"]),
                ingested_at=datetime.fromisoformat(row["ingested_at"]),
                processed_at=datetime.fromisoformat(row["processed_at"])
                if row["processed_at"]
                else None,
                instrument_id=row["instrument_id"],
                symbol=row["symbol"],
                base_asset=row["base_asset"],
                quote_asset=row["quote_asset"],
                payload=json.loads(row["payload"]),
                sequence=row["sequence"],
                stream_id=row["stream_id"],
                source_latency_ms=row["source_latency_ms"],
                ingestion_latency_ms=row["ingestion_latency_ms"],
                processing_latency_ms=row["processing_latency_ms"],
                quality_score=row["quality_score"],
                raw_envelope_id=row["raw_envelope_id"],
                price=row["price"],
                bid=row["bid"],
                ask=row["ask"],
                quantity=row["quantity"],
                side=row["side"],
            )
            yield event

    def get_health(self, source_id: str | None = None) -> list[HealthSnapshot]:
        """Get health snapshots."""
        if source_id:
            h = self._source_health.get(source_id)
            return [h] if h else []
        return list(self._source_health.values())

    def get_all_health(self) -> list[HealthSnapshot]:
        """Get all health snapshots."""
        return list(self._source_health.values())

    def update_connection_state(self, source_id: str, state: str) -> None:
        """Update connection state for a source."""
        self._connection_states[source_id] = state
        if source_id in self._source_health:
            h = self._source_health[source_id]
            self._source_health[source_id] = HealthSnapshot(
                source_id=h.source_id,
                source_name=h.source_name,
                venue=h.venue,
                asset_class=h.asset_class,
                connection_state=state,
                freshness_state=h.freshness_state,
                timestamp=datetime.now(UTC),
                latency_ms=h.latency_ms,
                messages_per_sec=h.messages_per_sec,
                queue_depth=self._queue_depth(),
                reconnect_count=h.reconnect_count,
                last_error=h.last_error,
                last_event_at=h.last_event_at,
            )

    def record_reconnect(self, source_id: str) -> None:
        """Record a reconnection event."""
        if source_id in self._source_health:
            h = self._source_health[source_id]
            self._source_health[source_id] = HealthSnapshot(
                source_id=h.source_id,
                source_name=h.source_name,
                venue=h.venue,
                asset_class=h.asset_class,
                connection_state=h.connection_state,
                freshness_state=h.freshness_state,
                timestamp=datetime.now(UTC),
                latency_ms=h.latency_ms,
                messages_per_sec=h.messages_per_sec,
                queue_depth=self._queue_depth(),
                reconnect_count=h.reconnect_count + 1,
                last_error=h.last_error,
                last_event_at=h.last_event_at,
            )

    def record_error(self, source_id: str, error: str) -> None:
        """Record an error for a source."""
        self._by_source[source_id]["errors"] += 1
        if source_id in self._source_health:
            h = self._source_health[source_id]
            self._source_health[source_id] = HealthSnapshot(
                source_id=h.source_id,
                source_name=h.source_name,
                venue=h.venue,
                asset_class=h.asset_class,
                connection_state="ERROR",
                freshness_state=h.freshness_state,
                timestamp=datetime.now(UTC),
                latency_ms=h.latency_ms,
                messages_per_sec=h.messages_per_sec,
                queue_depth=self._queue_depth(),
                reconnect_count=h.reconnect_count,
                last_error=error,
                last_event_at=h.last_event_at,
            )

    def set_quality_monitor(self, monitor: Any) -> None:
        """Attach QualityMonitor so publish records quality (087)."""
        self._quality_monitor = monitor

    def get_stats(self) -> dict[str, Any]:
        """Get bus statistics."""
        return {
            "processed_total": self._processed,
            "avg_latency_ms": self._total_latency_ms / self._processed
            if self._processed > 0
            else 0.0,
            "queue_size": self._queue_depth(),
            "queue_maxsize": self._maxsize,
            "queue_utilization": self._queue_depth() / self._maxsize,
            "sources": {
                src: {
                    "count": stats["count"],
                    "avg_latency_ms": stats["total_latency_ms"] / stats["count"]
                    if stats["count"] > 0
                    else 0.0,
                    "last_event_at": stats["last_event_at"].isoformat()
                    if stats["last_event_at"]
                    else None,
                    "errors": stats["errors"],
                }
                for src, stats in self._by_source.items()
            },
            "source_health": {sid: h.to_dict() for sid, h in self._source_health.items()},
        }

    def __repr__(self) -> str:
        return (
            f"EnhancedEventBus(processed={self._processed}, "
            f"avg_latency_ms={self.get_stats()['avg_latency_ms']:.2f}, "
            f"queue={self._queue_depth()}/{self._maxsize})"
        )
