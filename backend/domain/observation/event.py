from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ObservationEventType(enum.StrEnum):
    """Enumeration of supported observation event types."""

    TRADE = "trade"
    TICKER = "ticker"
    ORDER_BOOK = "order_book"
    CANDLE = "candle"
    NEWS = "news"
    MACRO = "macro"
    SENTIMENT = "sentiment"
    ONCHAIN = "onchain"
    INTERNAL = "internal"


class ObservationEvent(BaseModel):
    """Canonical event emitted by every observation source.

    All adapters must produce an instance of this model. It is immutable and
    serialisable (JSON) so it can be placed on the observation bus.
    """

    model_config = ConfigDict(frozen=True)

    source_id: str = Field(
        ..., description="Unique identifier of the source as defined in the Source Registry."
    )
    source_name: str = Field(..., description="Human‑readable name of the source.")
    event_type: ObservationEventType = Field(
        ..., description="Type of observation – trade, ticker, etc."
    )
    timestamp: datetime = Field(..., description="Event timestamp in UTC.")
    payload: dict[str, Any] = Field(
        ...,
        description=(
            "Raw payload specific to the event type. The schema is source‑specific "
            "but must be JSON‑serialisable."
        ),
    )

    @property
    def event_key(self) -> str:
        """Deterministic unique key used for at-least-once deduplication.

        Prefers the venue's native trade id when available, otherwise falls
        back to ``source_id:event_type:timestamp``. The key is stable across
        replays of the same underlying market event and is scoped to the
        symbol so identical trade ids on different markets never collide.
        """
        symbol = self.payload.get("symbol")
        scope = symbol if isinstance(symbol, str) else "unknown"
        trade_id = self.payload.get("trade_id")
        if trade_id is not None:
            return f"{scope}:{self.source_id}:{self.event_type.value}:{trade_id}"
        timestamp_ms = self.timestamp.isoformat(timespec="milliseconds")
        return f"{scope}:{self.source_id}:{self.event_type.value}:{timestamp_ms}"
