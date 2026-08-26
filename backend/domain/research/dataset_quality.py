# backend/domain/research/dataset_quality.py
"""Dataset-quality scan contracts (task T1-1-4).

The data-quality dashboard input for P5-005: a frozen dataset version is
scanned for the three classic honesty problems a market dataset can hide:

- **Gaps** — source-time holes between consecutive records (missing bars or
  a dead feed). Detected only when the caller states the expected interval:
  a gap is a finding only relative to an expectation, never invented.
- **Duplicates** — records that are identical in source time AND payload
  (canonical hash). A shared timestamp with different payloads is not a
  duplicate: two distinct trades may legitimately share a millisecond.
- **Outliers** — numeric payload fields whose values deviate beyond a
  MAD-based robust threshold (the median is the reference, not the mean, so
  a few wild bars cannot pull the reference toward themselves).

The scan never mutates anything and never fabricates: every finding is a
tuple of frozen facts about the frozen version, and findings are capped so
a pathological version produces a bounded report, not an unbounded one.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.domain.research.dataset import DatasetKind


class FindingCategory(enum.StrEnum):
    """What kind of problem a finding reports."""

    GAP = "gap"
    DUPLICATE = "duplicate"
    OUTLIER = "outlier"


@dataclass(frozen=True, slots=True)
class GapFinding:
    """A source-time hole between two consecutive records.

    Attributes
    ----------
    after: datetime
        Source time of the record right before the hole.
    before: datetime
        Source time of the record right after the hole.
    gap_seconds: float
        Observed span ``before - after``.
    expected_seconds: float
        The expected interval the caller stated; the gap is ``gap_seconds``
        minus ``expected_seconds`` of missing data.
    """

    after: datetime
    before: datetime
    gap_seconds: float
    expected_seconds: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": FindingCategory.GAP.value,
            "after": self.after.isoformat(timespec="milliseconds"),
            "before": self.before.isoformat(timespec="milliseconds"),
            "gap_seconds": self.gap_seconds,
            "expected_seconds": self.expected_seconds,
        }


@dataclass(frozen=True, slots=True)
class DuplicateFinding:
    """One duplicated record (same source time AND same payload).

    Attributes
    ----------
    source_timestamp: datetime
        The shared source time of the duplicate records.
    payload_hash: str
        SHA-256 over the canonical JSON of the shared payload.
    count: int
        How many records carry this identical (time, payload) pair.
    """

    source_timestamp: datetime
    payload_hash: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": FindingCategory.DUPLICATE.value,
            "source_timestamp": self.source_timestamp.isoformat(timespec="milliseconds"),
            "payload_hash": self.payload_hash,
            "count": self.count,
        }


@dataclass(frozen=True, slots=True)
class OutlierFinding:
    """One numeric payload value beyond the robust threshold.

    Attributes
    ----------
    source_timestamp: datetime
        When the offending record occurred.
    field: str
        The payload field that deviated.
    value: float
        The observed value.
    deviation: float
        Signed robust z-score: ``(value - median) / scale`` where ``scale``
        is ``1.4826 * MAD`` (falling back to the standard deviation when the
        MAD is zero). A finding is recorded when ``|deviation| > k``.
    """

    source_timestamp: datetime
    field: str
    value: float
    deviation: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": FindingCategory.OUTLIER.value,
            "source_timestamp": self.source_timestamp.isoformat(timespec="milliseconds"),
            "field": self.field,
            "value": self.value,
            "deviation": self.deviation,
        }


@dataclass(frozen=True, slots=True)
class DatasetQualityReport:
    """The bounded result of one dataset-quality scan.

    Attributes
    ----------
    dataset_id, version: str, int
        The frozen version that was scanned.
    kind: DatasetKind
        Raw or normalized.
    record_count: int
        Records scanned.
    gaps, duplicates, outliers: tuple[Finding, ...]
        Findings, each capped at ``max_findings_per_category``; further
        findings are counted, not stored, so the report stays bounded.
    gap_count, duplicate_count, outlier_count: int
        True counts (unbounded) of each finding category, so the dashboard
        knows when the tuples were truncated.
    scanned_fields: tuple[str, ...]
        The numeric payload fields scanned for outliers.
    expected_interval_seconds: float | None
        The gap expectation used; None when gap detection was skipped.
    """

    dataset_id: str
    version: int
    kind: DatasetKind
    record_count: int
    gaps: tuple[GapFinding, ...]
    duplicates: tuple[DuplicateFinding, ...]
    outliers: tuple[OutlierFinding, ...]
    gap_count: int
    duplicate_count: int
    outlier_count: int
    scanned_fields: tuple[str, ...]
    expected_interval_seconds: float | None = None

    @property
    def clean(self) -> bool:
        """True when the version has no findings at all."""
        return self.gap_count == 0 and self.duplicate_count == 0 and self.outlier_count == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "kind": self.kind.value,
            "record_count": self.record_count,
            "gaps": [g.as_dict() for g in self.gaps],
            "duplicates": [d.as_dict() for d in self.duplicates],
            "outliers": [o.as_dict() for o in self.outliers],
            "gap_count": self.gap_count,
            "duplicate_count": self.duplicate_count,
            "outlier_count": self.outlier_count,
            "scanned_fields": list(self.scanned_fields),
            "expected_interval_seconds": self.expected_interval_seconds,
            "clean": self.clean,
        }


__all__ = [
    "DatasetQualityReport",
    "DuplicateFinding",
    "FindingCategory",
    "GapFinding",
    "OutlierFinding",
]
