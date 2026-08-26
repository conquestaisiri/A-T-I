# backend/domain/context/events/market_context_created_event.py
"""Domain event emitted when a new MarketContext is created."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.domain.context.market_context import MarketContext


@dataclass(frozen=True, slots=True)
class MarketContextCreatedEvent:
    """Payload published when ContextBuilder completes a context build.

    Attributes
    ----------
    symbol: str
        Market symbol associated with the context.
    context: MarketContext
        The immutable market context that was created.
    trigger_event_id: str | None
        Optional identifier of the observation event that triggered the build.
    created_at: datetime
        Timestamp when the context was created (derived from snapshot).
    """

    symbol: str
    context: MarketContext
    created_at: datetime
    trigger_event_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the event to a JSON-compatible dictionary."""
        return {
            "event_type": "MarketContextCreated",
            "symbol": self.symbol,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "trigger_event_id": self.trigger_event_id,
            "context": self.context.as_dict(),
        }
