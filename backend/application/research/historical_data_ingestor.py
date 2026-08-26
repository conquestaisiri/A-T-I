# backend/application/research/historical_data_ingestor.py
"""Historical bar ingestion (task P5-005): OHLCV bars -> frozen dataset.

Real history arrives as OHLCV bars; the research pipeline replays
``ObservationEvent`` streams. This ingestor converts validated
:class:`HistoricalBar` series into trade-shaped observation events (close
price, bar volume, bar timestamp) carrying the full OHLCV payload, then
freezes them as an immutable RAW dataset version through the
``DatasetService`` (P1-001) — the versioned, content-addressed store the
OOS evaluator will read.

Honesty rules enforced here (mirrors the evaluator's own checks):
- bars must be chronological with strictly increasing timestamps;
- every bar must already satisfy the :class:`HistoricalBar` contract
  (positive prices, high/low consistency, aware timestamps);
- the frozen version stamps the backfill's ``available_at`` (download
  time), so point-in-time queries can never backdate knowledge.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from backend.application.research.dataset_service import DatasetService
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.dataset import DatasetVersion
from backend.domain.research.historical_bar import HistoricalBar


class HistoricalDataIngestor:
    """Convert validated OHLCV bars into observation events and datasets.

    Parameters
    ----------
    dataset_service: DatasetService
        The versioned dataset builder (P1-001) used to freeze RAW versions.
    """

    def __init__(self, dataset_service: DatasetService) -> None:
        self._dataset_service = dataset_service

    def bars_to_events(
        self,
        bars: Sequence[HistoricalBar],
        *,
        symbol: str,
        source_id: str = "historical",
    ) -> list[ObservationEvent]:
        """Turn validated bars into chronological TRADE observation events.

        Each bar becomes one event priced at its close, carrying the full
        OHLCV payload so downstream quality checks and diagnostics can
        reproduce the original series. Raises ``ValueError`` on an empty
        series, unsorted timestamps, or duplicate timestamps.
        """
        bars = list(bars)
        if not bars:
            raise ValueError("cannot ingest an empty bar series")
        events: list[ObservationEvent] = []
        for index, bar in enumerate(bars):
            if index > 0 and bar.timestamp <= bars[index - 1].timestamp:
                raise ValueError(
                    "bars must be chronological with strictly increasing "
                    f"timestamps (violation at {bar.timestamp.isoformat()})"
                )
            payload: dict[str, Any] = {
                "symbol": symbol,
                "trade_id": index + 1,
                "price": bar.close,
                "quantity": bar.volume,
                "trade_time": bar.timestamp.isoformat(timespec="milliseconds"),
                "is_market_maker": False,
                "open": bar.open,
                "high": bar.high,
                "low": bar.low,
                "close": bar.close,
                "volume": bar.volume,
                "source": source_id,
            }
            events.append(
                ObservationEvent(
                    source_id=source_id,
                    source_name=source_id,
                    event_type=ObservationEventType.TRADE,
                    timestamp=bar.timestamp,
                    payload=payload,
                )
            )
        return events

    def freeze_raw_dataset(
        self,
        bars: Sequence[HistoricalBar],
        *,
        dataset_id: str,
        symbol: str,
        available_at: datetime | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> DatasetVersion:
        """Freeze the bar series as the next RAW dataset version.

        ``available_at`` is the backfill's download time (defaults to now).
        Returns the frozen version record.
        """
        events = self.bars_to_events(bars, symbol=symbol)
        return self._dataset_service.build_raw_dataset(
            dataset_id=dataset_id,
            events=events,
            metadata=metadata,
            available_at=available_at,
        )
