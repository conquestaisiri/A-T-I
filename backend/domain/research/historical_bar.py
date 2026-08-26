# backend/domain/research/historical_bar.py
"""Historical OHLCV bar contract (task P5-005, real-data evidence run).

The OOS evaluator consumes ``ObservationEvent`` streams; real history
arrives as OHLCV bars. This contract is the canonical bar shape every
ingestor must produce — one bar per interval with an aware UTC timestamp —
so ingestion is deterministic and the data-quality gate (6-4) can measure
gaps, duplicates and outliers against a single, validated structure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class HistoricalBar:
    """One validated OHLCV interval.

    Attributes
    ----------
    timestamp: datetime
        Bar open time, aware UTC (required — naive timestamps are refused).
    open, high, low, close: float
        Prices of the interval, all positive, with high >= max(open, close)
        and low <= min(open, close).
    volume: float
        Traded volume of the interval, non-negative.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None:
            raise ValueError("bar timestamp must be timezone-aware (UTC)")
        for name, value in (
            ("open", self.open),
            ("high", self.high),
            ("low", self.low),
            ("close", self.close),
        ):
            if not isinstance(value, (int, float)) or value <= 0.0:
                raise ValueError(f"bar {name} must be a positive number")
        if self.high < self.low:
            raise ValueError(f"bar high {self.high} below low {self.low}")
        if self.high < max(self.open, self.close):
            raise ValueError(f"bar high {self.high} below open/close")
        if self.low > min(self.open, self.close):
            raise ValueError(f"bar low {self.low} above open/close")
        if not isinstance(self.volume, (int, float)) or self.volume < 0.0:
            raise ValueError("bar volume must be a non-negative number")

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(timespec="milliseconds"),
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }

    @classmethod
    def from_dict(cls, data: Any) -> HistoricalBar:
        """Reconstruct a bar from :meth:`as_dict` output."""
        items = dict(data)
        return cls(
            timestamp=datetime.fromisoformat(str(items["timestamp"])),
            open=float(items["open"]),
            high=float(items["high"]),
            low=float(items["low"]),
            close=float(items["close"]),
            volume=float(items["volume"]),
        )
