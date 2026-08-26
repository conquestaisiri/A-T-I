"""Base connector classes for Data Fabric."""

from __future__ import annotations

import abc
import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ....domain.data_fabric.enums import ConnectionState, FreshnessState
from ....domain.data_fabric.envelope import NormalizedEvent, RawEnvelope
from ....domain.data_fabric.quality import HealthSnapshot
from ....domain.data_fabric.source import SourceConfig
from ....infrastructure.data_fabric.event_bus import EnhancedEventBus

logger = logging.getLogger(__name__)


@dataclass
class ConnectorState:
    """Runtime state of a connector."""

    source_id: str
    connection_state: ConnectionState = ConnectionState.DISCONNECTED
    freshness_state: FreshnessState = FreshnessState.UNKNOWN
    last_event_at: datetime | None = None
    messages_received: int = 0
    messages_published: int = 0
    errors: int = 0
    reconnect_count: int = 0
    current_latency_ms: float = 0.0
    last_error: str | None = None
    started_at: datetime | None = None
    last_message_at: datetime | None = None


class BaseConnector(abc.ABC):
    """Abstract base for all data fabric connectors.

    Provides:
    - Connection lifecycle (connect, disconnect, reconnect)
    - Health monitoring
    - Exponential backoff reconnection
    - Event publishing via EnhancedEventBus
    - Raw envelope preservation
    """

    def __init__(
        self,
        config: SourceConfig,
        event_bus: EnhancedEventBus,
        instrument_master: Any = None,
    ) -> None:
        self.config = config
        self.event_bus = event_bus
        self.instrument_master = instrument_master
        self._state = ConnectorState(source_id=config.source_id)
        self._running = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._reconnect_task: asyncio.Task[Any] | None = None
        self._health_task: asyncio.Task[Any] | None = None

    @property
    def state(self) -> ConnectorState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._running

    @abc.abstractmethod
    async def _connect_impl(self) -> None:
        """Implement connection logic. Raise on failure."""

    @abc.abstractmethod
    async def _disconnect_impl(self) -> None:
        """Implement disconnection logic."""

    @abc.abstractmethod
    async def _subscribe_impl(self) -> None:
        """Implement subscription logic after connection."""

    async def _handle_message(self, raw: dict[str, Any]) -> None:
        """Handle incoming message, normalize and publish.

        Default implementation is a no-op: connectors that read from a
        websocket/RSS loop override ``_run`` and process messages there.
        """
        del raw

    async def start(self) -> None:
        """Start the connector."""
        if self._running:
            return
        self._running = True
        self._state.started_at = datetime.now(UTC)
        self._state.connection_state = ConnectionState.CONNECTING
        self.event_bus.update_connection_state(self.config.source_id, "CONNECTING")

        try:
            await self._connect_impl()
            await self._subscribe_impl()
            self._state.connection_state = ConnectionState.CONNECTED
            self._state.freshness_state = FreshnessState.LIVE
            self.event_bus.update_connection_state(self.config.source_id, "LIVE")

            # Start health monitor
            self._health_task = asyncio.create_task(self._health_monitor())

            # Start message processing as a background task. The connector's
            # ``_run`` is a long-lived loop (WebSocket read loop, poll loop,
            # stream reader); awaiting it here would block ``fabric.start()``
            # and starve every downstream service (bridge, market loop) that
            # must start after the fabric. The task is tracked so ``stop``
            # cancels it.
            run_task = asyncio.create_task(self._run())
            self._tasks.append(run_task)

        except Exception as e:
            self._state.connection_state = ConnectionState.ERROR
            self._state.last_error = str(e)
            self.event_bus.record_error(self.config.source_id, str(e))
            self.event_bus.update_connection_state(self.config.source_id, "ERROR")
            await self._schedule_reconnect()
            raise

    async def stop(self) -> None:
        """Stop the connector gracefully."""
        self._running = False

        # Cancel health monitor
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task

        # Cancel reconnect task
        if self._reconnect_task:
            self._reconnect_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconnect_task

        # Cancel all tasks
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        await self._disconnect_impl()
        self._state.connection_state = ConnectionState.DISCONNECTED
        self.event_bus.update_connection_state(self.config.source_id, "DISCONNECTED")

    @abc.abstractmethod
    async def _run(self) -> None:
        """Main run loop - override for WebSocket listeners."""

    async def _schedule_reconnect(self) -> None:
        """Schedule exponential backoff reconnection."""
        if self._reconnect_task:
            self._reconnect_task.cancel()

        async def _reconnect_loop() -> None:
            attempt = 0
            while self._running and attempt < self.config.max_reconnect_attempts:
                delay = min(
                    self.config.reconnect_base_delay_seconds * (2**attempt),
                    self.config.reconnect_max_delay_seconds,
                )
                logger.warning(
                    "Reconnecting %s in %.1fs (attempt %d/%d)",
                    self.config.source_name,
                    delay,
                    attempt + 1,
                    self.config.max_reconnect_attempts,
                )
                self._state.connection_state = ConnectionState.RECONNECTING
                self._state.reconnect_count = attempt + 1
                self.event_bus.update_connection_state(self.config.source_id, "RECONNECTING")
                self.event_bus.record_reconnect(self.config.source_id)

                await asyncio.sleep(delay)

                try:
                    await self._connect_impl()
                    await self._subscribe_impl()
                    self._state.connection_state = ConnectionState.CONNECTED
                    self._state.freshness_state = FreshnessState.LIVE
                    self.event_bus.update_connection_state(self.config.source_id, "LIVE")
                    logger.info("Reconnected %s successfully", self.config.source_name)
                    return
                except Exception as e:
                    logger.warning(
                        "Reconnect attempt %d failed for %s: %s",
                        attempt + 1,
                        self.config.source_name,
                        e,
                    )
                    attempt += 1

            # All attempts exhausted
            self._state.connection_state = ConnectionState.ERROR
            self.event_bus.update_connection_state(self.config.source_id, "ERROR")
            logger.error("All reconnect attempts exhausted for %s", self.config.source_name)

        self._reconnect_task = asyncio.create_task(_reconnect_loop())

    async def _health_monitor(self) -> None:
        """Monitor freshness and update health state."""
        while self._running:
            await asyncio.sleep(10)  # Check every 10 seconds

            if self._state.last_message_at:
                age = (datetime.now(UTC) - self._state.last_message_at).total_seconds()
                if age > self.config.stale_after_seconds:
                    if self._state.freshness_state != FreshnessState.STALE:
                        self._state.freshness_state = FreshnessState.STALE
                        self._state.connection_state = ConnectionState.STALE
                        self.event_bus.update_connection_state(self.config.source_id, "STALE")
                elif age > self.config.stale_after_seconds / 2:
                    if self._state.freshness_state == FreshnessState.LIVE:
                        self._state.freshness_state = FreshnessState.DEGRADED
                        self._state.connection_state = ConnectionState.DEGRADED
                        self.event_bus.update_connection_state(self.config.source_id, "DEGRADED")
                else:
                    if self._state.freshness_state != FreshnessState.LIVE:
                        self._state.freshness_state = FreshnessState.LIVE
                        self._state.connection_state = ConnectionState.CONNECTED
                        self.event_bus.update_connection_state(self.config.source_id, "LIVE")

    async def _publish_normalized(self, event: NormalizedEvent) -> None:
        """Publish normalized event and preserve raw envelope."""
        self._state.messages_published += 1
        self._state.last_message_at = datetime.now(UTC)
        self._state.current_latency_ms = event.source_latency_ms
        await self.event_bus.publish(event)

    async def _publish_raw(self, envelope: RawEnvelope) -> None:
        """Preserve raw envelope."""
        await self.event_bus.publish_raw(envelope)

    def get_health(self) -> HealthSnapshot:
        """Get current health snapshot."""
        return HealthSnapshot(
            source_id=self.config.source_id,
            source_name=self.config.source_name,
            venue=self.config.venue,
            asset_class=self.config.asset_class.value,
            connection_state=self._state.connection_state.value,
            freshness_state=self._state.freshness_state.value,
            timestamp=datetime.now(UTC),
            latency_ms=self._state.current_latency_ms,
            messages_per_sec=self._state.messages_received
            / max(1, (datetime.now(UTC) - self._state.started_at).total_seconds())
            if self._state.started_at
            else 0.0,
            queue_depth=0,
            reconnect_count=self._state.reconnect_count,
            last_error=self._state.last_error,
            last_event_at=self._state.last_event_at,
        )
