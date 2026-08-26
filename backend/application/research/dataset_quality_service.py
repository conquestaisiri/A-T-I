# backend/application/research/dataset_quality_service.py
"""Dataset-quality scanning service (task T1-1-4).

The data-quality dashboard input for P5-005: an operator (or dashboard)
scans a frozen dataset version for gaps, duplicates, and outliers. The scan
is an audit: it declares ``AUDIT`` so the research firewall never refuses it
(an audit trains no model and must be allowed to read locked test data), but
the load is labelled, so the audit trail can prove no training happened.

The scan never mutates the store. Findings are bounded (capped per
category) so a pathological version yields a bounded report, and every count
is reported unbounded so the dashboard knows when a tuple was truncated.
"""

from __future__ import annotations

import hashlib
import json
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from backend.application.interfaces.dataset_store import DatasetStore
from backend.domain.research.dataset import DatasetPurpose
from backend.domain.research.dataset_quality import (
    DatasetQualityReport,
    DuplicateFinding,
    GapFinding,
    OutlierFinding,
)

DEFAULT_MAX_FINDINGS_PER_CATEGORY = 100
DEFAULT_GAP_TOLERANCE = 1.5
DEFAULT_OUTLIER_K = 5.0


class DatasetQualityService:
    """Scan frozen dataset versions for quality findings."""

    def __init__(self, store: DatasetStore) -> None:
        self._store = store

    def scan(
        self,
        dataset_id: str,
        version: int,
        *,
        expected_interval_seconds: float | None = None,
        gap_tolerance: float = DEFAULT_GAP_TOLERANCE,
        outlier_fields: Sequence[str] = (),
        outlier_k: float = DEFAULT_OUTLIER_K,
        max_findings_per_category: int = DEFAULT_MAX_FINDINGS_PER_CATEGORY,
    ) -> DatasetQualityReport:
        """Scan one frozen version.

        Parameters
        ----------
        dataset_id, version: str, int
            The frozen version to audit.
        expected_interval_seconds: float | None
            When set, source-time gaps larger than
            ``expected_interval_seconds * gap_tolerance`` are reported. None
            disables gap detection (a gap exists only relative to an
            expectation; the scan never invents one).
        gap_tolerance: float
            Multiple of the expected interval that separates a normal pause
            from a gap. Must be >= 1.0.
        outlier_fields: Sequence[str]
            Payload fields scanned for outliers; only numeric values are
            considered.
        outlier_k: float
            Robust threshold in units of scale; a value whose absolute
            deviation from the median exceeds ``k`` is a finding. Must be
            > 0.
        max_findings_per_category: int
            Upper bound on stored findings per category; counts stay exact.

        Raises
        ------
        ValueError
            On an unknown version or an invalid parameter (non-positive
            expected interval, tolerance < 1.0, k <= 0, max findings < 1).
        """
        if expected_interval_seconds is not None and expected_interval_seconds <= 0:
            raise ValueError("expected_interval_seconds must be > 0")
        if gap_tolerance < 1.0:
            raise ValueError("gap_tolerance must be >= 1.0")
        if outlier_k <= 0:
            raise ValueError("outlier_k must be > 0")
        if max_findings_per_category < 1:
            raise ValueError("max_findings_per_category must be >= 1")

        version_meta = next(
            (v for v in self._store.list_versions(dataset_id) if v.version == version),
            None,
        )
        if version_meta is None:
            raise ValueError(f"dataset {dataset_id} has no version {version}")

        records = self._store.load_records(
            dataset_id,
            version,
            purpose=DatasetPurpose.AUDIT,
        )

        gaps = self._scan_gaps(records, expected_interval_seconds, gap_tolerance)
        duplicates = self._scan_duplicates(records)
        outliers = self._scan_outliers(records, outlier_fields=outlier_fields, k=outlier_k)

        return DatasetQualityReport(
            dataset_id=dataset_id,
            version=version,
            kind=version_meta.kind,
            record_count=len(records),
            gaps=tuple(gaps[:max_findings_per_category]),
            duplicates=tuple(duplicates[:max_findings_per_category]),
            outliers=tuple(outliers[:max_findings_per_category]),
            gap_count=len(gaps),
            duplicate_count=len(duplicates),
            outlier_count=len(outliers),
            scanned_fields=tuple(outlier_fields),
            expected_interval_seconds=expected_interval_seconds,
        )

    # -- scans --------------------------------------------------------------

    @staticmethod
    def _scan_gaps(
        records: Sequence[Any],
        expected_interval_seconds: float | None,
        gap_tolerance: float,
    ) -> list[GapFinding]:
        if expected_interval_seconds is None:
            return []
        findings: list[GapFinding] = []
        threshold = expected_interval_seconds * gap_tolerance
        for after, before in zip(records, records[1:], strict=False):
            gap_seconds = (before.source_timestamp - after.source_timestamp).total_seconds()
            if gap_seconds > threshold:
                findings.append(
                    GapFinding(
                        after=after.source_timestamp,
                        before=before.source_timestamp,
                        gap_seconds=gap_seconds,
                        expected_seconds=expected_interval_seconds,
                    )
                )
        return findings

    @staticmethod
    def _scan_duplicates(records: Sequence[Any]) -> list[DuplicateFinding]:
        buckets: dict[tuple[Any, str], list[Any]] = {}
        for record in records:
            key = (
                record.source_timestamp,
                _canonical_hash(record.payload),
            )
            buckets.setdefault(key, []).append(record)
        findings = [
            DuplicateFinding(
                source_timestamp=timestamp,
                payload_hash=payload_hash,
                count=len(records_),
            )
            for (timestamp, payload_hash), records_ in buckets.items()
            if len(records_) > 1
        ]
        findings.sort(key=lambda f: (f.source_timestamp, f.payload_hash))
        return findings

    @staticmethod
    def _scan_outliers(
        records: Sequence[Any],
        *,
        outlier_fields: Sequence[str],
        k: float,
    ) -> list[OutlierFinding]:
        if not outlier_fields:
            return []
        findings: list[OutlierFinding] = []
        for field in outlier_fields:
            values: list[tuple[Any, float]] = []
            for record in records:
                raw = record.payload.get(field)
                if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                    continue
                values.append((record.source_timestamp, float(raw)))
            if not values:
                continue
            median = statistics.median(v for _, v in values)
            scale = _robust_scale([v for _, v in values], median)
            if scale == 0.0:
                continue  # no dispersion: nothing can deviate
            for timestamp, value in values:
                deviation = (value - median) / scale
                if abs(deviation) > k:
                    findings.append(
                        OutlierFinding(
                            source_timestamp=timestamp,
                            field=field,
                            value=value,
                            deviation=deviation,
                        )
                    )
        findings.sort(key=lambda f: (f.source_timestamp, f.field))
        return findings


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    """SHA-256 over the canonical JSON of a payload (sort_keys + compact)."""
    canonical = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _robust_scale(values: list[float], median: float) -> float:
    """Robust dispersion: 1.4826 * MAD, falling back to std when MAD is 0.

    The 1.4826 factor makes the MAD comparable to the standard deviation
    under normality. A zero MAD (e.g. a constant field) falls back to the
    standard deviation; a zero scale (constant field) reports no outliers.
    """
    deviations = [abs(v - median) for v in values]
    mad = statistics.median(deviations)
    if mad > 0.0:
        return 1.4826 * mad
    if len(values) >= 2:
        return statistics.stdev(values)
    return 0.0


__all__ = [
    "DEFAULT_GAP_TOLERANCE",
    "DEFAULT_MAX_FINDINGS_PER_CATEGORY",
    "DEFAULT_OUTLIER_K",
    "DatasetQualityService",
]
