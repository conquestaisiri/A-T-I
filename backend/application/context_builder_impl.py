# backend/application/context_builder_impl.py
"""Pure orchestration layer for the Context Builder pipeline.

ObservationEvent -> WindowManager -> ContextSnapshot -> FeatureEngine
-> MarketContext -> MarketContextCreatedEvent -> EventBus.publish
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from backend.application.interfaces.context_builder import ContextBuilder
from backend.application.interfaces.event_bus import EventBus
from backend.application.interfaces.feature_engine import FeatureEngine
from backend.application.interfaces.window_manager import WindowManager
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.events.market_context_created_event import MarketContextCreatedEvent
from backend.domain.context.market_context import MarketContext
from backend.domain.observation.event import ObservationEvent

logger = logging.getLogger(__name__)

MARKET_CONTEXT_CREATED = "MarketContextCreated"


class ContextBuilderImpl(ContextBuilder):
    """Orchestrates window management, feature execution, and event publication.

    This class contains no business logic, feature computation, or AI reasoning.
    """

    def __init__(
        self,
        window_manager: WindowManager,
        feature_engine: FeatureEngine,
        event_bus: EventBus,
    ) -> None:
        self._window_manager = window_manager
        self._feature_engine = feature_engine
        self._event_bus = event_bus

    def handle(self, event: ObservationEvent) -> MarketContext:
        """Process a single observation event through the context pipeline."""
        symbol = event.payload.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise KeyError("ObservationEvent payload missing required 'symbol' string field")

        self._window_manager.add(event)
        snapshot = self._window_manager.snapshot(symbol)
        execution_result = self._feature_engine.run(snapshot)

        features_map: Mapping[str, ContextFeature] = {
            feature.name: feature for feature in execution_result.features
        }
        context = MarketContext(
            snapshot=snapshot,
            features=tuple(features_map.items()),
            created_at=snapshot.end_timestamp,
        )

        created_event = MarketContextCreatedEvent(
            symbol=symbol,
            context=context,
            created_at=context.created_at,
            trigger_event_id=_trigger_event_id(event),
        )
        self._event_bus.publish(MARKET_CONTEXT_CREATED, created_event)

        logger.debug(
            "MarketContext created for %s with %d features",
            symbol,
            len(features_map),
        )
        return context


def _trigger_event_id(event: ObservationEvent) -> str | None:
    """Build a deterministic trigger identifier from the observation event."""
    trade_id = event.payload.get("trade_id")
    if trade_id is not None:
        return f"{event.source_id}:{trade_id}"
    return f"{event.source_id}:{event.timestamp.isoformat(timespec='milliseconds')}"
