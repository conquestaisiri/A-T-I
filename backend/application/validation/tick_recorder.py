# backend/application/validation/tick_recorder.py
"""Tick data recorder for L2 order book deltas (task P0-006).

Records L2 delta events to disk for historical research and backtesting.
Every day not recording is a day of unrecoverable research data.

Format: JSONL (one JSON object per line), one file per symbol and date
(``{symbol}/{YYYY-MM-DD}.jsonl``). JSONL is pickle-free, safe to read,
appendable, and human auditable — unlike the legacy numpy object-array
``.npz`` captures which silently required pickle to read.

Legacy ``.npz`` captures are never loaded on the write path: they are
quarantined (renamed ``.npz.legacy``) on first access so the active data
path never touches pickle. An explicit :func:`migrate_legacy_tick_data`
converts legacy captures to JSONL for operators who want the old bytes.

Record schema:
    timestamp: epoch seconds (float)
    symbol: str
    side: 'bid' | 'ask'
    price: float
    old_size: float or null (previous size when known)
    new_size: float (resulting size)
    size: float (== new_size; retained for backward compatibility)
    action: 'add' | 'update' | 'remove'
    sequence: int or null (synthetic delta_seq when present)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from backend.domain.observation.event import ObservationEvent, ObservationEventType

logger = logging.getLogger(__name__)

_RECORD_FIELDS = (
    "timestamp",
    "symbol",
    "side",
    "price",
    "old_size",
    "new_size",
    "size",
    "action",
    "sequence",
)


class TickRecorder:
    """Records L2 delta events to disk for historical research."""

    def __init__(self, *, data_dir: str | Path = "data/ticks", buffer_size: int = 1000) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._buffer: list[dict[str, Any]] = []
        self._buffer_size = buffer_size

    def record_event(self, event: ObservationEvent) -> None:
        """Record an order-book delta event."""
        if event.event_type is not ObservationEventType.ORDER_BOOK:
            return

        if not event.payload.get("delta", False):
            return

        symbol = event.payload.get("symbol", "UNKNOWN")
        sequence = event.payload.get("delta_seq")
        timestamp = event.timestamp.timestamp()

        for side, deltas in [
            ("bid", event.payload.get("bids", [])),
            ("ask", event.payload.get("asks", [])),
        ]:
            for delta in deltas:
                new_size = delta.get("new_size", delta.get("size", 0.0))
                self._buffer.append(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "side": side,
                        "price": delta.get("price", 0.0),
                        "old_size": delta.get("old_size", None),
                        "new_size": new_size,
                        "size": new_size,
                        "action": delta.get("action", ""),
                        "sequence": sequence,
                    }
                )

        # Flush buffer periodically
        if len(self._buffer) >= self._buffer_size:
            self._flush()

    @staticmethod
    def _sanitize_symbol(symbol: str) -> str:
        """Sanitize symbol for filesystem use."""
        clean = str(symbol).replace("/", "-").replace("\\", "-")
        clean = Path(clean).name.strip()
        if not clean or clean in {".", ".."} or ".." in clean:
            return "UNKNOWN"
        return clean[:30]

    def _flush(self) -> None:
        """Write buffered events to disk as JSONL, one file per symbol/date."""
        if not self._buffer:
            return

        by_symbol_date: dict[tuple[str, date], list[dict[str, Any]]] = {}
        for event in self._buffer:
            ts = datetime.fromtimestamp(event["timestamp"], tz=UTC).date()
            safe_sym = self._sanitize_symbol(str(event["symbol"]))
            by_symbol_date.setdefault((safe_sym, ts), []).append(event)

        flushed = 0
        for (symbol, date_key), events in sorted(by_symbol_date.items()):
            symbol_dir = self._data_dir / symbol
            symbol_dir.mkdir(parents=True, exist_ok=True)
            filepath = symbol_dir / f"{date_key.isoformat()}.jsonl"

            _quarantine_legacy(filepath)

            with filepath.open("a", encoding="utf-8") as fh:
                for event in events:
                    fh.write(json.dumps(event, separators=(",", ":")) + "\n")
            flushed += len(events)

        self._buffer.clear()
        logger.debug("Flushed %d tick events to disk", flushed)

    def close(self) -> None:
        """Flush remaining buffer."""
        self._flush()

    def get_stats(self) -> dict[str, Any]:
        """Get recording statistics over JSONL files."""
        stats = {}
        for symbol_dir in self._data_dir.iterdir():
            if symbol_dir.is_dir():
                files = list(symbol_dir.glob("*.jsonl"))
                total_size = sum(f.stat().st_size for f in files)
                stats[symbol_dir.name] = {
                    "files": len(files),
                    "total_bytes": total_size,
                }
        return stats


def _quarantine_legacy(jsonl_path: Path) -> None:
    """Move a legacy ``.npz`` capture out of the active data path.

    Legacy captures are never loaded on the write path: the old numpy object
    arrays required pickle, and silently loading them reintroduces the risk
    the JSONL format removes. The bytes are preserved as ``.npz.legacy`` and
    can be migrated explicitly via :func:`migrate_legacy_tick_data`.
    """
    npz_path = jsonl_path.with_suffix(".npz")
    if not npz_path.is_file():
        return
    legacy_path = npz_path.with_suffix(".npz.legacy")
    if not legacy_path.exists():
        npz_path.rename(legacy_path)
    else:
        npz_path.unlink()
    logger.warning(
        "Quarantined legacy tick capture %s -> %s; migrate with "
        "migrate_legacy_tick_data() to recover records",
        npz_path,
        legacy_path,
    )


def migrate_legacy_tick_data(data_dir: str | Path, symbol: str) -> int:  # noqa: ARG001
    """Convert quarantined legacy ``.npz`` captures for ``symbol`` to JSONL.

    Explicit opt-in migration for the operator's own research captures. Loads
    legacy captures with numpy (which uses pickle internally) and writes the
    records as JSONL. Returns the number of files migrated. Files that cannot
    be read are left in place and logged.

    Raises ``ImportError`` if numpy is not installed.
    """
    import numpy as np

    symbol_dir = Path(data_dir) / symbol
    if not symbol_dir.is_dir():
        return 0

    migrated = 0
    for npz_path in sorted(symbol_dir.glob("*.npz.legacy")):
        base = npz_path.name[: -len(".npz.legacy")]
        jsonl_path = npz_path.with_name(base + ".jsonl")
        try:
            with np.load(npz_path, allow_pickle=True) as data:
                records = data["data"].tolist()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not migrate legacy capture %s: %s", npz_path, exc)
            continue

        if not isinstance(records, list):
            logger.warning("Legacy capture %s does not hold a record list; skipping", npz_path)
            continue

        with jsonl_path.open("w", encoding="utf-8") as fh:
            for record in records:
                fh.write(json.dumps(record, separators=(",", ":")) + "\n")
        npz_path.rename(npz_path.with_name(base + ".npz.migrated"))
        migrated += 1

    return migrated


def load_tick_data(
    data_dir: str | Path,
    symbol: str,
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    """Load recorded tick data for a symbol and date range.

    Reads only JSONL files; no pickle or numpy is required.
    """
    safe = TickRecorder._sanitize_symbol(symbol)
    symbol_dir = Path(data_dir) / safe
    if not symbol_dir.is_dir():
        return []

    events: list[dict[str, Any]] = []
    current = start_date
    while current <= end_date:
        filepath = symbol_dir / f"{current.isoformat()}.jsonl"
        if filepath.is_file():
            with filepath.open(encoding="utf-8") as fh:
                for line in fh:
                    if line.strip():
                        events.append(json.loads(line))
        current = date.fromordinal(current.toordinal() + 1)

    return events
