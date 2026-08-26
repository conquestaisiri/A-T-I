"""Tests for the data-quality gate (6-4, P5-005)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.application.research.data_quality_report import assess_data_quality
from backend.domain.research.historical_bar import HistoricalBar

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)


def bars(n: int, *, step: timedelta = STEP, start: datetime = T0) -> list[HistoricalBar]:
    result: list[HistoricalBar] = []
    price = 100.0
    for i in range(n):
        ts = start + i * step
        result.append(
            HistoricalBar(
                timestamp=ts,
                open=price,
                high=price + 1.0,
                low=price - 1.0,
                close=price + 0.5,
                volume=100.0,
            )
        )
        price += 0.5
    return result


def test_clean_series_is_usable():
    report = assess_data_quality(bars(120), expected_interval_seconds=60)
    assert report.is_usable
    assert report.n_bars == 120
    assert report.missing_bars == 0
    assert report.duplicate_timestamps == 0
    assert report.close_outliers == 0
    assert report.issues == ()


def test_interval_inferred_from_median():
    report = assess_data_quality(bars(10))
    assert report.expected_interval_seconds == 60


def test_gap_detected_and_missing_bars_counted():
    series = bars(5)
    series.insert(3, bars(1, start=series[2].timestamp + 5 * STEP)[0])
    report = assess_data_quality(series, expected_interval_seconds=60)
    assert not report.is_usable
    assert len(report.gaps) == 1
    assert report.missing_bars == 2
    assert any("gaps=1" in issue for issue in report.issues)


def test_duplicate_timestamps_flagged():
    series = bars(5)
    dup = bars(1, start=series[2].timestamp)[0]
    series.insert(3, dup)
    report = assess_data_quality(series, expected_interval_seconds=60)
    assert not report.is_usable
    assert report.duplicate_timestamps == 1
    assert any("duplicate" in issue for issue in report.issues)


def test_non_positive_price_seam_stays_zero():
    # The bar contract forbids broken prices, so the reserved seam is 0 on
    # every assessable series (see module docstring).
    report = assess_data_quality(bars(5), expected_interval_seconds=60)
    assert report.non_positive_prices == 0
    assert report.is_usable


def test_close_outlier_flagged():
    series = bars(20)
    spike = 10500.0  # ~100x the neighbourhood close
    series[10] = _replace(series[10], high=spike, close=spike)
    report = assess_data_quality(series, expected_interval_seconds=60)
    assert not report.is_usable
    assert report.close_outliers >= 1


def test_empty_series_unusable():
    report = assess_data_quality([])
    assert not report.is_usable
    assert "empty_series" in report.issues


def test_unsorted_series_sorted_for_assessment():
    series = bars(3)
    reversed_series = list(reversed(series))
    report = assess_data_quality(reversed_series, expected_interval_seconds=60)
    assert report.is_usable
    assert report.span_start == T0.isoformat(timespec="milliseconds")


def test_report_as_dict():
    report = assess_data_quality(bars(3), dataset_id="btcusdt", expected_interval_seconds=60)
    data = report.as_dict()
    assert data["dataset_id"] == "btcusdt"
    assert data["is_usable"] is True
    assert data["n_bars"] == 3


def _replace(bar, **overrides) -> HistoricalBar:
    fields = {
        "timestamp": bar.timestamp,
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
        "volume": bar.volume,
    }
    fields.update(overrides)
    return HistoricalBar(**fields)
