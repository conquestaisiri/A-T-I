# backend/application/research/dataset_event_adapter.py
"""Dataset records -> observation events adapter (task P5-005).

The OOS evaluator consumes ``ObservationEvent`` streams; the dataset store
serves ``DatasetRecord`` payloads. This adapter is the single seam that
round-trips RAW records back into events (and back into bars) so the
evaluator can replay a frozen dataset version exactly as it was frozen.
"""

from __future__ import annotations

from collections.abc import Sequence

from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.dataset import DatasetRecord
from backend.domain.research.historical_bar import HistoricalBar


def records_to_events(
    records: Sequence[DatasetRecord],
    *,
    expected_symbol: str | None = None,
) -> list[ObservationEvent]:
    """Reconstruct observation events from RAW dataset record payloads.

    Every payload must carry a ``symbol`` and a positive numeric ``price``;
    records must be chronological (the store already orders them by source
    time — this function re-verifies). Raises ``ValueError`` on malformed
    payloads or mixed symbols.
    """
    records = list(records)
    if not records:
        raise ValueError("cannot adapt an empty record set")
    events: list[ObservationEvent] = []
    previous_ts = None
    for record in records:
        payload = dict(record.payload)
        symbol = payload.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("record payload missing 'symbol' string field")
        if expected_symbol is not None and symbol != expected_symbol:
            raise ValueError(f"record symbol {symbol!r} != expected symbol {expected_symbol!r}")
        price = payload.get("price")
        if not isinstance(price, (int, float)) or price <= 0.0:
            raise ValueError(f"record for {symbol} missing a positive numeric 'price'")
        if previous_ts is not None and record.source_timestamp < previous_ts:
            raise ValueError("records must be chronological by source_timestamp")
        previous_ts = record.source_timestamp
        events.append(
            ObservationEvent(
                source_id=str(payload.get("source", record.dataset_id)),
                source_name=str(payload.get("source", record.dataset_id)),
                event_type=ObservationEventType.TRADE,
                timestamp=record.source_timestamp,
                payload=payload,
            )
        )
    return events


def records_to_bars(records: Sequence[DatasetRecord]) -> list[HistoricalBar]:
    """Reconstruct the original OHLCV bars from RAW record payloads.

    Used by the evidence run to re-run the data-quality gate on exactly
    what was frozen. Raises ``ValueError`` when a payload lacks the OHLCV
    fields (only RAW ingestor payloads round-trip).
    """
    bars: list[HistoricalBar] = []
    for record in records:
        payload = record.payload
        try:
            bars.append(
                HistoricalBar(
                    timestamp=record.source_timestamp,
                    open=float(payload["open"]),
                    high=float(payload["high"]),
                    low=float(payload["low"]),
                    close=float(payload["close"]),
                    volume=float(payload["volume"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(
                f"record at {record.source_timestamp.isoformat()} lacks valid "
                "OHLCV payload fields; only RAW ingestor payloads round-trip to bars"
            ) from exc
    return bars
