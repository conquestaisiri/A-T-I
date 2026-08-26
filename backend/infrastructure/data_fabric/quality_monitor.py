"""Data quality monitoring and cross-source anomaly detection."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import statistics
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ...domain.data_fabric.envelope import NormalizedEvent
from ...domain.data_fabric.quality import QualityMetrics
from ...infrastructure.data_fabric.event_bus import EnhancedEventBus

logger = logging.getLogger(__name__)


class QualityMonitor:
    """Computes and tracks data quality metrics for all sources."""

    def __init__(self, event_bus: EnhancedEventBus, window_minutes: int = 5) -> None:
        self._event_bus = event_bus
        self._window = timedelta(minutes=window_minutes)
        self._running = False
        self._task: asyncio.Task[Any] | None = None

        # Rolling windows for metrics computation
        self._latencies: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=1000))
        self._message_times: dict[str, deque[datetime]] = defaultdict(lambda: deque(maxlen=1000))
        self._sequences: dict[str, deque[int]] = defaultdict(lambda: deque(maxlen=1000))
        self._prices: dict[str, dict[str, deque[tuple[datetime, float]]]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=100))
        )

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._compute_loop())

    async def _compute_loop(self) -> None:
        """Periodic quality computation loop."""
        while self._running:
            await asyncio.sleep(60)  # Compute every minute
            if not self._running:
                break
            # Metrics are computed on-demand via get_metrics()
            # This loop just keeps the service alive for anomaly detection

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def record_event(self, event: NormalizedEvent) -> None:
        """Record an event for quality computation."""
        source_id = event.source_id
        now = datetime.now(UTC)

        # Latency
        latency_ms = getattr(event, "source_latency_ms", 0)
        if latency_ms:
            self._latencies[source_id].append(latency_ms)

        # Message timing
        self._message_times[source_id].append(now)

        # Sequence tracking
        seq = getattr(event, "sequence", None)
        if seq is not None:
            self._sequences[source_id].append(seq)

        # Price tracking for cross-source comparison
        if hasattr(event, "price") and event.price is not None:
            instrument = event.instrument_id or event.symbol
            self._prices[source_id][instrument].append((now, event.price))

    def get_metrics(self, source_id: str) -> QualityMetrics:
        """Compute quality metrics for a source."""
        now = datetime.now(UTC)
        window_start = now - self._window

        latencies = [latency for latency in self._latencies.get(source_id, [])]
        message_times = [t for t in self._message_times.get(source_id, []) if t >= window_start]
        sequences = list(self._sequences.get(source_id, []))

        # Latency percentiles
        statistics.median(latencies) if latencies else 0
        latency_p95 = (
            statistics.quantiles(latencies, n=20)[18]
            if len(latencies) >= 20
            else (max(latencies) if latencies else 0)
        )
        latency_p99 = (
            statistics.quantiles(latencies, n=100)[98]
            if len(latencies) >= 100
            else (max(latencies) if latencies else 0)
        )
        current_latency = latencies[-1] if latencies else 0

        # Message rate
        rate = len(message_times) / self._window.total_seconds() if message_times else 0

        # Sequence gaps
        gaps = 0
        if len(sequences) >= 2:
            for i in range(1, len(sequences)):
                if sequences[i] - sequences[i - 1] > 1:
                    gaps += sequences[i] - sequences[i - 1] - 1

        # Duplicate detection (same sequence number)
        seen = set()
        duplicates = 0
        for seq in sequences:
            if seq in seen:
                duplicates += 1
            seen.add(seq)

        # Freshness
        last_event_age = 0.0
        if message_times:
            last_event_age = (now - max(message_times)).total_seconds() * 1000

        # Cross-source disagreement
        disagreement_bps = self._compute_disagreement(source_id)

        # Quality score (composite)
        quality = self._compute_quality_score(
            rate=rate,
            latency_p95=latency_p95,
            gaps=gaps,
            duplicates=duplicates,
            last_event_age=last_event_age,
        )

        # Freshness state
        if last_event_age < 1000:
            freshness = "LIVE"
        elif last_event_age < 5000:
            freshness = "DEGRADED"
        elif last_event_age < 30000:
            freshness = "STALE"
        else:
            freshness = "DISCONNECTED"

        return QualityMetrics(
            source_id=source_id,
            computed_at=datetime.now(UTC),
            uptime_fraction=1.0,  # Would track separately
            message_rate_per_sec=rate,
            latency_p50_ms=statistics.median(latencies) if latencies else 0,
            latency_p95_ms=latency_p95,
            latency_p99_ms=latency_p99,
            current_latency_ms=current_latency,
            sequence_gaps=gaps,
            duplicate_count=duplicates,
            out_of_order_count=0,
            missing_field_count=0,
            timestamp_anomalies=0,
            last_event_age_ms=last_event_age,
            stale_events_count=0,
            cross_source_disagreement_bps=disagreement_bps,
            consensus_participation=1.0,
            quality_score=quality,
            freshness_state=freshness,
        )

    def _compute_disagreement(self, source_id: str) -> float:
        """Compute cross-source price disagreement in basis points."""
        max_disagreement = 0.0
        source_prices = self._prices.get(source_id, {})

        for instrument, prices in source_prices.items():
            if len(prices) < 2:
                continue
            # Get latest price from this source
            latest_price = prices[-1][1]
            # Compare with other sources' latest prices
            for other_source, other_instruments in self._prices.items():
                if other_source == source_id:
                    continue
                other_prices = other_instruments.get(instrument)
                if other_prices and len(other_prices) >= 1:
                    other_latest = other_prices[-1][1]
                    if latest_price > 0:
                        diff_bps = abs(latest_price - other_latest) / latest_price * 10000
                        max_disagreement = max(max_disagreement, diff_bps)

        return max_disagreement

    def _compute_quality_score(
        self,
        rate: float,
        latency_p95: float,
        gaps: int,
        duplicates: int,
        last_event_age: float,
    ) -> float:
        """Compute composite quality score (0.0 - 1.0)."""
        score = 1.0

        # Rate penalty (expect at least 0.1 msg/sec for live feeds)
        if rate < 0.1:
            score *= 0.5
        elif rate < 1.0:
            score *= 0.8

        # Latency penalty
        if latency_p95 > 500:
            score *= 0.6
        elif latency_p95 > 200:
            score *= 0.8
        elif latency_p95 > 100:
            score *= 0.9

        # Gap penalty
        if gaps > 10:
            score *= 0.5
        elif gaps > 0:
            score *= 0.8

        # Duplicate penalty
        if duplicates > 5:
            score *= 0.6
        elif duplicates > 0:
            score *= 0.9

        # Freshness penalty
        if last_event_age > 30000:
            score *= 0.3
        elif last_event_age > 10000:
            score *= 0.6
        elif last_event_age > 5000:
            score *= 0.8

        return max(0.0, min(1.0, score))

    def get_all_metrics(self) -> dict[str, QualityMetrics]:
        """Get metrics for all tracked sources."""
        sources = (
            set(self._latencies.keys())
            | set(self._message_times.keys())
            | set(self._sequences.keys())
        )
        return {src: self.get_metrics(src) for src in sources}


class AnomalyDetector:
    """Detects anomalies across sources: price divergence, stale feeds, gaps."""

    def __init__(self, event_bus: EnhancedEventBus, quality_monitor: QualityMonitor) -> None:
        self._event_bus = event_bus
        self._quality_monitor = quality_monitor
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._anomaly_callbacks: list[Callable[[dict[str, Any]], Awaitable[Any]]] = []

    def add_callback(self, callback: Callable[[dict[str, Any]], Awaitable[Any]]) -> None:
        self._anomaly_callbacks.append(callback)

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._detect_loop())

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _detect_loop(self) -> None:
        while self._running:
            await asyncio.sleep(30)  # Check every 30 seconds
            if not self._running:
                break
            try:
                await self._check_anomalies()
            except Exception as e:
                logger.warning("Anomaly detection error: %s", e)

    async def _check_anomalies(self) -> None:
        metrics = self._quality_monitor.get_all_metrics()

        for source_id, metric in metrics.items():
            # Stale data
            if metric.freshness_state in ("STALE", "DISCONNECTED"):
                await self._emit_anomaly(
                    "STALE_FEED",
                    source_id,
                    f"Feed stale for {metric.last_event_age_ms / 1000:.0f}s",
                    severity="WARNING",
                )

            # High latency
            if metric.latency_p95_ms > 1000:
                await self._emit_anomaly(
                    "HIGH_LATENCY",
                    source_id,
                    f"P95 latency {metric.latency_p95_ms:.0f}ms",
                    severity="WARNING",
                )

            # Sequence gaps
            if metric.sequence_gaps > 5:
                await self._emit_anomaly(
                    "SEQUENCE_GAPS",
                    source_id,
                    f"{metric.sequence_gaps} sequence gaps detected",
                    severity="WARNING",
                )

        # Cross-source price divergence
        await self._check_price_divergence()

    async def _check_price_divergence(self) -> None:
        # Collect latest prices per instrument per source
        defaultdict(dict)

        # This would need access to the event bus's price tracking
        # For now, placeholder - the QualityMonitor already tracks this

    async def _emit_anomaly(
        self,
        anomaly_type: str,
        source_id: str,
        message: str,
        severity: str = "INFO",
    ) -> None:
        anomaly = {
            "type": anomaly_type,
            "source_id": source_id,
            "message": message,
            "severity": severity,
            "timestamp": datetime.now(UTC).isoformat(),
        }
        logger.warning("ANOMALY [%s] %s: %s", severity, source_id, message)
        for callback in self._anomaly_callbacks:
            with contextlib.suppress(Exception):
                await callback(anomaly)


class DataQualityService:
    """Integrated data quality monitoring service."""

    def __init__(self, event_bus: EnhancedEventBus) -> None:
        self._quality_monitor = QualityMonitor(event_bus)
        self._anomaly_detector = AnomalyDetector(event_bus, self._quality_monitor)
        self._running = False
        # Wire the monitor into the bus publish path — without this the bus's
        # quality hook never fires and all metrics stay permanently empty.
        event_bus.set_quality_monitor(self._quality_monitor)

    async def start(self) -> None:
        await self._quality_monitor.start()
        await self._anomaly_detector.start()
        self._running = True

    async def stop(self) -> None:
        await self._quality_monitor.stop()
        await self._anomaly_detector.stop()
        self._running = False

    def record_event(self, event: NormalizedEvent) -> None:
        self._quality_monitor.record_event(event)

    def get_metrics(self, source_id: str) -> Any:
        return self._quality_monitor.get_metrics(source_id)

    def get_all_metrics(self) -> dict[str, Any]:
        return self._quality_monitor.get_all_metrics()

    def add_anomaly_callback(self, callback: Callable[[dict[str, Any]], Awaitable[Any]]) -> None:
        self._anomaly_detector.add_callback(callback)
