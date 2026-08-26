# backend/application/research/data_quality_report.py
"""Data-quality gate for real history (cross-cutting concern 6-4, P5-005).

Before any historical series feeds the OOS evaluator, it must survive a
quality check the operator can read: gaps, duplicate timestamps, broken
prices and close-price outliers are measured, named, and aggregated into an
``is_usable`` verdict. A series that fails the gate is refused by the
evidence run — no honest evaluation on data that might be fiction.

The check is stdlib-only and deterministic, mirroring the house estimator
constraint set (market_impact, VPIN).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.domain.research.historical_bar import HistoricalBar


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """Scorecard of one historical series against the quality gate.

    Attributes
    ----------
    dataset_id: str
        Identifier the series belongs to.
    n_bars: int
        Number of bars assessed.
    span_start, span_end: str
        ISO-8601 source-time span of the series.
    expected_interval_seconds: int | None
        The interval the series claims (None = inferred from the median
        bar spacing).
    missing_bars: int
        Bars implied by the intervals that are absent (gaps beyond the
        tolerance ratio).
    max_gap_seconds: float
        The largest gap between consecutive bars.
    gaps: tuple[tuple[str, str], ...]
        Each gap beyond tolerance as (previous bar time, next bar time).
    duplicate_timestamps: int
        Number of bars sharing a timestamp with an earlier bar.
    non_positive_prices: int
        Always 0 today: the :class:`HistoricalBar` contract forbids broken
        prices at construction, so this field is a reserved seam for any
        future payload path that bypasses the contract.
    close_outliers: int
        Number of bars whose close return is beyond ``outlier_z_threshold``
        standard deviations from the mean close-to-close return.
    outlier_z_threshold: float
        The robust-z threshold used to flag close-price outliers (recorded
        so the report is auditable: a looser gate is a documented operator
        decision, never a silent default).
    issues: tuple[str, ...]
        Every named problem found (empty when the series is clean).
    is_usable: bool
        True only when no issue was found.
    """

    dataset_id: str
    n_bars: int
    span_start: str
    span_end: str
    expected_interval_seconds: int | None
    missing_bars: int
    max_gap_seconds: float
    gaps: tuple[tuple[str, str], ...]
    duplicate_timestamps: int
    non_positive_prices: int
    close_outliers: int
    outlier_z_threshold: float
    issues: tuple[str, ...]
    is_usable: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "n_bars": self.n_bars,
            "span_start": self.span_start,
            "span_end": self.span_end,
            "expected_interval_seconds": self.expected_interval_seconds,
            "missing_bars": self.missing_bars,
            "max_gap_seconds": round(self.max_gap_seconds, 6),
            "gaps": [list(g) for g in self.gaps],
            "duplicate_timestamps": self.duplicate_timestamps,
            "non_positive_prices": self.non_positive_prices,
            "close_outliers": self.close_outliers,
            "outlier_z_threshold": self.outlier_z_threshold,
            "issues": list(self.issues),
            "is_usable": self.is_usable,
        }


def assess_data_quality(
    bars: Sequence[HistoricalBar],
    *,
    dataset_id: str = "",
    expected_interval_seconds: int | None = None,
    interval_tolerance_ratio: float = 2.0,
    outlier_z_threshold: float = 5.0,
) -> DataQualityReport:
    """Assess a bar series against the data-quality gate (6-4).

    Parameters
    ----------
    bars: Sequence[HistoricalBar]
        The series to assess (may be unsorted; assessment sorts a copy).
    dataset_id: str
        Identifier carried into the report.
    expected_interval_seconds: int | None
        The nominal bar interval. When None, the median spacing of the
        series is inferred and used as the reference.
    interval_tolerance_ratio: float
        A spacing above ``reference * ratio`` counts as a gap.
    outlier_z_threshold: float
        Close-to-close returns beyond this many standard deviations from
        the mean are flagged as outliers.
    """
    if not bars:
        return DataQualityReport(
            dataset_id=dataset_id,
            n_bars=0,
            span_start="",
            span_end="",
            expected_interval_seconds=expected_interval_seconds,
            missing_bars=0,
            max_gap_seconds=0.0,
            gaps=(),
            duplicate_timestamps=0,
            non_positive_prices=0,
            close_outliers=0,
            outlier_z_threshold=outlier_z_threshold,
            issues=("empty_series",),
            is_usable=False,
        )
    ordered = sorted(bars, key=lambda b: b.timestamp)
    issues: list[str] = []
    duplicates = _count_duplicates(ordered)
    if duplicates:
        issues.append(f"duplicate_timestamps={duplicates}")

    spacings = [
        (next_bar.timestamp - ordered[i].timestamp).total_seconds()
        for i, next_bar in enumerate(ordered[1:])
    ]
    reference = (
        float(expected_interval_seconds)
        if expected_interval_seconds
        else (statistics.median(spacings) if spacings else 0.0)
    )
    gaps: list[tuple[str, str]] = []
    missing = 0
    max_gap = 0.0
    for i, seconds in enumerate(spacings):
        max_gap = max(max_gap, seconds)
        if seconds > interval_tolerance_ratio * reference:
            gaps.append(
                (
                    ordered[i].timestamp.isoformat(timespec="milliseconds"),
                    ordered[i + 1].timestamp.isoformat(timespec="milliseconds"),
                )
            )
            missing += max(1, round(seconds / reference) - 1) if reference > 0 else 1
    if gaps:
        issues.append(f"gaps={len(gaps)} (missing_bars={missing})")
    if missing:
        issues.append(f"missing_bars={missing}")

    outliers = _count_close_outliers(ordered, outlier_z_threshold)
    if outliers:
        issues.append(f"close_outliers={outliers}")

    return DataQualityReport(
        dataset_id=dataset_id,
        n_bars=len(ordered),
        span_start=ordered[0].timestamp.isoformat(timespec="milliseconds"),
        span_end=ordered[-1].timestamp.isoformat(timespec="milliseconds"),
        expected_interval_seconds=(
            int(reference) if expected_interval_seconds is None else expected_interval_seconds
        ),
        missing_bars=missing,
        max_gap_seconds=max_gap,
        gaps=tuple(gaps),
        duplicate_timestamps=duplicates,
        non_positive_prices=0,
        close_outliers=outliers,
        outlier_z_threshold=outlier_z_threshold,
        issues=tuple(issues),
        is_usable=not issues,
    )


def _count_duplicates(bars: Sequence[HistoricalBar]) -> int:
    seen: set[str] = set()
    duplicates = 0
    for bar in bars:
        key = bar.timestamp.isoformat(timespec="microseconds")
        if key in seen:
            duplicates += 1
        else:
            seen.add(key)
    return duplicates


def _count_close_outliers(bars: Sequence[HistoricalBar], z_threshold: float) -> int:
    """Count close-return outliers with a MAD-based robust z-score.

    A plain z-score fails exactly when a single extreme return dominates the
    sample: the outlier inflates the standard deviation and hides itself.
    The median-absolute-deviation variant (``|r - median| / (1.4826 * MAD)``)
    stays bounded for the bulk of the series, so a genuine spike is flagged.
    """
    closes = [bar.close for bar in bars]
    returns = [
        (closes[i + 1] - closes[i]) / closes[i] for i in range(len(closes) - 1) if closes[i] > 0.0
    ]
    if len(returns) < 3:
        return 0
    median = statistics.median(returns)
    deviations = [abs(r - median) for r in returns]
    mad = statistics.median(deviations)
    if mad == 0.0:
        return sum(1 for r in returns if r != median)
    scale = 1.4826 * mad
    return sum(1 for r in returns if abs(r - median) / scale > z_threshold)
