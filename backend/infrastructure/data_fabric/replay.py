"""Replay engine for exact historical event reproduction."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from typing import Any

from ...domain.data_fabric.envelope import NormalizedEvent
from ...infrastructure.data_fabric.event_bus import EnhancedEventBus

logger = logging.getLogger(__name__)


class ReplayEngine:
    """Exact historical event replay engine.

    Replays events in strict event-time order with configurable speed.
    Used for backtesting, research, and debugging.
    """

    def __init__(self, event_bus: EnhancedEventBus) -> None:
        self._event_bus = event_bus
        self._running = False

    async def replay(
        self,
        start_time: datetime,
        end_time: datetime,
        instruments: list[str] | None = None,
        event_types: list[str] | None = None,
        sources: list[str] | None = None,
        speed_factor: float = 1.0,
        publish_to_bus: bool = True,
    ) -> AsyncGenerator[NormalizedEvent]:
        """Replay events in event-time order.

        Args:
            start_time: Replay start (inclusive)
            end_time: Replay end (inclusive)
            instruments: Filter by instrument IDs
            event_types: Filter by event types
            sources: Filter by source IDs
            speed_factor: Replay speed (1.0 = real-time, >1 = faster, <1 = slower)
            publish_to_bus: Whether to publish replayed events to the event bus
        """
        self._running = True
        prev_event_time = None

        try:
            async for event in self._event_bus.replay(
                start_time=start_time,
                end_time=end_time,
                instruments=instruments,
                event_types=event_types,
                sources=sources,
            ):
                if not self._running:
                    break

                # Calculate delay to maintain event-time ordering
                if prev_event_time is not None and speed_factor > 0:
                    delta = (event.event_time - prev_event_time).total_seconds()
                    if delta > 0:
                        await asyncio.sleep(delta / speed_factor)

                if publish_to_bus:
                    # Create replay copy with new event_id and replay marker
                    replay_event = NormalizedEvent(
                        event_id=f"replay_{event.event_id}",
                        event_type=event.event_type,
                        source_id=event.source_id,
                        source_name=event.source_name,
                        venue=event.venue,
                        data_plane=event.data_plane,
                        asset_class=event.asset_class,
                        event_time=event.event_time,
                        source_timestamp=event.source_timestamp,
                        received_at=datetime.now(UTC),  # replay time
                        ingested_at=datetime.now(UTC),
                        instrument_id=event.instrument_id,
                        symbol=event.symbol,
                        base_asset=event.base_asset,
                        quote_asset=event.quote_asset,
                        payload={
                            **event.payload,
                            "replay": True,
                            "original_event_id": event.event_id,
                        },
                        sequence=event.sequence,
                        stream_id=event.stream_id,
                        source_latency_ms=event.source_latency_ms,
                        ingestion_latency_ms=0,
                        processing_latency_ms=0,
                        quality_score=event.quality_score,
                        raw_envelope_id=event.raw_envelope_id,
                        price=event.price,
                        bid=event.bid,
                        ask=event.ask,
                        quantity=event.quantity,
                        side=event.side,
                    )
                    await self._event_bus.publish(replay_event)

                yield event
                prev_event_time = event.event_time

        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    def stop(self) -> None:
        self._running = False


class ReplaySession:
    """Represents a replay session with metadata."""

    def __init__(
        self,
        session_id: str,
        start_time: datetime,
        end_time: datetime,
        instruments: list[str] | None,
        event_types: list[str] | None,
        sources: list[str] | None,
        speed_factor: float,
        total_events: int = 0,
    ) -> None:
        self.session_id = session_id
        self.start_time = start_time
        self.end_time = end_time
        self.instruments = instruments or []
        self.event_types = event_types or []
        self.sources = sources or []
        self.speed_factor = speed_factor
        self.total_events = total_events
        self.events_replayed = 0
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.status = "pending"  # pending, running, completed, failed, cancelled

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "start_time": self.start_time.isoformat(),
            "end_time": self.end_time.isoformat(),
            "instruments": self.instruments,
            "event_types": self.event_types,
            "sources": self.sources,
            "speed_factor": self.speed_factor,
            "total_events": self.total_events,
            "events_replayed": self.events_replayed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "status": self.status,
        }


class ReplayManager:
    """Manages replay sessions and provides API for replay control."""

    def __init__(self, event_bus: EnhancedEventBus) -> None:
        self._engine = ReplayEngine(event_bus)
        self._sessions: dict[str, ReplaySession] = {}

    async def create_session(
        self,
        start_time: datetime,
        end_time: datetime,
        instruments: list[str] | None = None,
        event_types: list[str] | None = None,
        sources: list[str] | None = None,
        speed_factor: float = 1.0,
    ) -> ReplaySession:
        """Create a new replay session."""
        session_id = f"replay_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
        session = ReplaySession(
            session_id=session_id,
            start_time=start_time,
            end_time=end_time,
            instruments=instruments,
            event_types=event_types,
            sources=sources,
            speed_factor=speed_factor,
        )
        self._sessions[session_id] = session
        return session

    async def run_session(self, session_id: str) -> ReplaySession:
        """Run a replay session."""
        session = self._sessions.get(session_id)
        if not session:
            raise ValueError(f"Session not found: {session_id}")

        session.status = "running"
        session.started_at = datetime.now(UTC)

        try:
            count = 0
            async for _ in self._engine.replay(
                start_time=session.start_time,
                end_time=session.end_time,
                instruments=session.instruments,
                event_types=session.event_types,
                sources=session.sources,
                speed_factor=session.speed_factor,
            ):
                count += 1
                session.events_replayed = count

            session.status = "completed"
            session.total_events = count
            session.completed_at = datetime.now(UTC)
        except Exception:
            session.status = "failed"
            session.completed_at = datetime.now(UTC)
            raise

        return session

    def get_session(self, session_id: str) -> ReplaySession | None:
        return self._sessions.get(session_id)

    def list_sessions(self) -> list[ReplaySession]:
        return list(self._sessions.values())

    async def cancel_session(self, session_id: str) -> bool:
        session = self._sessions.get(session_id)
        if session and session.status == "running":
            session.status = "cancelled"
            session.completed_at = datetime.now(UTC)
            self._engine.stop()
            return True
        return False
