"""Data quality metrics and health snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class QualityMetrics:
    """Computed quality metrics for a data source.

    All values are computed automatically from observed behavior.
    """

    source_id: str
    computed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    # Availability
    uptime_fraction: float = 1.0  # fraction of time connected
    message_rate_per_sec: float = 0.0  # messages/second observed

    # Latency
    latency_p50_ms: float = 0.0
    latency_p95_ms: float = 0.0
    latency_p99_ms: float = 0.0
    current_latency_ms: float = 0.0

    # Integrity
    sequence_gaps: int = 0
    duplicate_count: int = 0
    out_of_order_count: int = 0
    missing_field_count: int = 0
    timestamp_anomalies: int = 0

    # Freshness
    last_event_age_ms: float = 0.0
    stale_events_count: int = 0

    # Cross-source
    cross_source_disagreement_bps: float = 0.0
    consensus_participation: float = 1.0

    # Composite
    quality_score: float = 1.0  # 0.0 - 1.0
    freshness_state: str = "LIVE"  # LIVE | DEGRADED | STALE | DISCONNECTED | UNKNOWN

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "source_id": self.source_id,
            "computed_at": self.computed_at.isoformat(),
            "uptime_fraction": self.uptime_fraction,
            "message_rate_per_sec": self.message_rate_per_sec,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "current_latency_ms": self.current_latency_ms,
            "sequence_gaps": self.sequence_gaps,
            "duplicate_count": self.duplicate_count,
            "out_of_order_count": self.out_of_order_count,
            "missing_field_count": self.missing_field_count,
            "timestamp_anomalies": self.timestamp_anomalies,
            "last_event_age_ms": self.last_event_age_ms,
            "stale_events_count": self.stale_events_count,
            "cross_source_disagreement_bps": self.cross_source_disagreement_bps,
            "consensus_participation": self.consensus_participation,
            "quality_score": self.quality_score,
            "freshness_state": self.freshness_state,
        }


@dataclass(frozen=True, slots=True)
class HealthSnapshot:
    """Point-in-time health snapshot for a source."""

    source_id: str
    source_name: str
    venue: str | None
    asset_class: str
    connection_state: str
    freshness_state: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    latency_ms: float = 0.0
    messages_per_sec: float = 0.0
    queue_depth: int = 0
    reconnect_count: int = 0
    last_error: str | None = None
    last_event_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_name": self.source_name,
            "venue": self.venue,
            "asset_class": self.asset_class,
            "connection_state": self.connection_state,
            "freshness_state": self.freshness_state,
            "timestamp": self.timestamp.isoformat(),
            "latency_ms": self.latency_ms,
            "messages_per_sec": self.messages_per_sec,
            "queue_depth": self.queue_depth,
            "reconnect_count": self.reconnect_count,
            "last_error": self.last_error,
            "last_event_at": self.last_event_at.isoformat() if self.last_event_at else None,
        }
