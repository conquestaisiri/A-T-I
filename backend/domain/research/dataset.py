# backend/domain/research/dataset.py
"""Versioned historical dataset contract (task P1-001).

The research factory's foundation. A dataset is a typed, versioned, immutable
snapshot of market observations that keeps the information a causal researcher
needs to avoid leakage:

- **Raw vs normalized**: a dataset is either ``RAW`` (the observation events
  exactly as received from the source) or ``NORMALIZED`` (features engineered
  from raw data). The two are distinguishable and never mixed in one dataset.
- **Versioned and immutable**: every snapshot belongs to a ``DatasetVersion``
  identified by a content hash. Once a version is frozen it can never be
  mutated; a new snapshot creates a new version.
- **Point-in-time correctness**: every record carries both ``source_timestamp``
  (when the market event happened) and ``available_at`` (when the data became
  known to this system). Labels and features must only ever use data with
  ``available_at <= decision time``; this is the core anti-leakage invariant.

A dataset is identified by a stable ``dataset_id`` (e.g. ``binance-btcusdt``).
"""

from __future__ import annotations

import enum
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class DatasetKind(enum.StrEnum):
    """Whether a dataset holds raw observations or engineered features."""

    RAW = "raw"
    NORMALIZED = "normalized"


class DatasetPurpose(enum.StrEnum):
    """What a data load is for — the firewall's access-control label.

    ``TRAINING`` (the default, fail-safe) means the records will influence a
    model; such loads are refused when they overlap a locked test period
    (task P5-002). ``TEST`` means the records are the locked scoring data
    themselves and are always served. ``AUDIT`` means the records are read
    for auditing only (quality scans, dashboards): no model is influenced,
    so locked data is served — but the load is labelled so an auditor can
    prove the scan never trained on the locked period.
    """

    TRAINING = "training"
    TEST = "test"
    AUDIT = "audit"


@dataclass(frozen=True, slots=True)
class TestPeriodLock:
    """An immutable claim that a source-time period is a locked test set.

    Attributes
    ----------
    dataset_id: str
        Dataset the claim applies to.
    start: datetime
        Start of the locked source-time period (inclusive).
    end: datetime
        End of the locked source-time period (inclusive).
    experiment_id: str
        The experiment that claimed this period as its test set.
    claimed_by: str
        Who made the claim (operator/researcher id).
    claimed_at: datetime
        When the claim was recorded.
    """

    dataset_id: str
    start: datetime
    end: datetime
    experiment_id: str
    claimed_by: str
    claimed_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "start": self.start.isoformat(timespec="milliseconds"),
            "end": self.end.isoformat(timespec="milliseconds"),
            "experiment_id": self.experiment_id,
            "claimed_by": self.claimed_by,
            "claimed_at": self.claimed_at.isoformat(timespec="milliseconds"),
        }


@dataclass(frozen=True, slots=True)
class DatasetRecord:
    """One immutable row of a dataset.

    Attributes
    ----------
    dataset_id: str
        Stable identifier of the dataset this record belongs to.
    source_timestamp: datetime
        When the underlying market event occurred (exchange time).
    available_at: datetime
        When this data became known to the system (point-in-time). For live
        capture this is ingestion time; for backfilled data it is the download
        time. Labels must be computed only from records whose
        ``available_at`` precedes the decision.
    payload: Mapping[str, Any]
        Raw observation payload or normalized feature vector.
    kind: DatasetKind
        Whether this is a raw or normalized record.
    """

    dataset_id: str
    source_timestamp: datetime
    available_at: datetime
    payload: Mapping[str, Any]
    kind: DatasetKind

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "source_timestamp": self.source_timestamp.isoformat(timespec="milliseconds"),
            "available_at": self.available_at.isoformat(timespec="milliseconds"),
            "payload": dict(self.payload),
            "kind": self.kind.value,
        }


@dataclass(frozen=True, slots=True)
class DatasetVersion:
    """An immutable, content-addressed snapshot of a dataset.

    Parameters
    ----------
    dataset_id: str
        Stable dataset identifier.
    version: int
        Monotonic snapshot number within the dataset.
    kind: DatasetKind
        Raw or normalized.
    content_hash: str
        SHA-256 over the canonical JSON of every record in the snapshot. Two
        versions with the same hash are byte-identical; a changed hash means
        the data changed.
    record_count: int
        Number of records in this snapshot.
    source_start: datetime
        Earliest source timestamp in the snapshot.
    source_end: datetime
        Latest source timestamp in the snapshot.
    created_at: datetime
        When this snapshot was frozen.
    metadata: Mapping[str, Any]
        Provenance: symbol, exchange, bar/event type, generation parameters.
    """

    dataset_id: str
    version: int
    kind: DatasetKind
    content_hash: str
    record_count: int
    source_start: datetime
    source_end: datetime
    created_at: datetime
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def range_seconds(self) -> float:
        return max(0.0, (self.source_end - self.source_start).total_seconds())

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "kind": self.kind.value,
            "content_hash": self.content_hash,
            "record_count": self.record_count,
            "source_start": self.source_start.isoformat(timespec="milliseconds"),
            "source_end": self.source_end.isoformat(timespec="milliseconds"),
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "metadata": dict(self.metadata),
        }


def compute_content_hash(records: list[DatasetRecord]) -> str:
    """Compute the canonical SHA-256 content hash of a record set.

    Order-independent: records are sorted by (source_timestamp,
    available_at, payload JSON) before hashing so identical snapshots hash
    identically regardless of input order. Uses milliseconds precision to match
    as_dict serialisation.
    """

    def _key(r: DatasetRecord) -> tuple[str, str, str]:
        return (
            r.source_timestamp.isoformat(timespec="milliseconds"),
            r.available_at.isoformat(timespec="milliseconds"),
            json.dumps(r.payload, sort_keys=True, separators=(",", ":")),
        )

    lines = []
    for record in sorted(records, key=_key):
        line = (
            f"{record.dataset_id}|{record.kind.value}|"
            f"{record.source_timestamp.isoformat(timespec='milliseconds')}|"
            f"{record.available_at.isoformat(timespec='milliseconds')}|"
            f"{json.dumps(record.payload, sort_keys=True, separators=(',', ':'))}"
        )
        lines.append(line)
    canonical = "\n".join(lines)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
