"""Tests for the dataset-quality scanner (T1-1-4).

The scanner audits a frozen version for gaps, duplicates, and outliers.
It must:
- detect source-time gaps relative to an expected interval (never invent one);
- detect duplicates only when source time AND payload are identical;
- detect numeric outliers via a robust (median/MAD) threshold;
- never refuse to read a locked test period (audit loads are always served);
- stay bounded (findings capped, counts exact) on pathological versions.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.dataset_quality_service import DatasetQualityService
from backend.application.research.dataset_service import DatasetService
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.dataset import DatasetPurpose
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository


def event(ts: datetime, trade_id: int, price: float) -> ObservationEvent:
    return ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts,
        payload={"symbol": "btcusdt", "trade_id": trade_id, "price": price, "quantity": 1.0},
    )


@pytest.fixture
def store(tmp_path) -> SqliteDatasetRepository:
    return SqliteDatasetRepository(Database(tmp_path / "quality.db"))


@pytest.fixture
def service(store) -> DatasetQualityService:
    return DatasetQualityService(store)


def t0() -> datetime:
    return datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)


class TestScanValidation:
    def test_unknown_version_refused(self, service) -> None:
        with pytest.raises(ValueError, match="no version"):
            service.scan("btcusdt", 99)

    def test_single_record_version_scans_clean(self, service) -> None:
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=[event(t0(), 1, 100.0)], available_at=t0()
        )
        report = service.scan("btcusdt", 1)
        assert report.record_count == 1
        assert report.clean is True

    def test_invalid_parameters_refused(self, service) -> None:
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=[event(t0(), 1, 100.0)], available_at=t0()
        )
        with pytest.raises(ValueError, match="expected_interval_seconds"):
            service.scan("btcusdt", 1, expected_interval_seconds=0)
        with pytest.raises(ValueError, match="gap_tolerance"):
            service.scan("btcusdt", 1, expected_interval_seconds=60, gap_tolerance=0.5)
        with pytest.raises(ValueError, match="outlier_k"):
            service.scan("btcusdt", 1, outlier_fields=("price",), outlier_k=0)
        with pytest.raises(ValueError, match="max_findings"):
            service.scan("btcusdt", 1, max_findings_per_category=0)


class TestGapDetection:
    def test_reports_gap_when_expectation_stated(self, service) -> None:
        base = t0()
        events = [
            event(base + timedelta(hours=0), 1, 100.0),
            event(base + timedelta(hours=1), 2, 101.0),
            event(base + timedelta(hours=4), 3, 102.0),  # 3h hole on hourly data
            event(base + timedelta(hours=5), 4, 103.0),
        ]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1, expected_interval_seconds=3600)
        assert report.gap_count == 1
        gap = report.gaps[0]
        assert gap.gap_seconds == 3 * 3600
        assert gap.expected_seconds == 3600

    def test_no_gap_when_expectation_not_stated(self, service) -> None:
        base = t0()
        events = [
            event(base + timedelta(hours=0), 1, 100.0),
            event(base + timedelta(hours=4), 2, 101.0),
        ]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1)
        assert report.gap_count == 0
        assert report.expected_interval_seconds is None

    def test_tolerance_suppresses_small_pauses(self, service) -> None:
        base = t0()
        events = [
            event(base + timedelta(hours=0), 1, 100.0),
            event(base + timedelta(hours=1), 2, 101.0),
            event(base + timedelta(hours=2), 3, 102.0),
            event(base + timedelta(hours=2, minutes=30), 4, 103.0),  # 30m pause, tolerance 1.5
        ]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1, expected_interval_seconds=3600, gap_tolerance=1.5)
        assert report.gap_count == 0


class TestDuplicateDetection:
    def test_duplicate_same_time_and_payload_reported(self, service) -> None:
        base = t0()
        dup = event(base, 7, 100.0)
        events = [dup, dup, dup, event(base + timedelta(hours=1), 8, 101.0)]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1)
        assert report.duplicate_count == 1
        duplicate = report.duplicates[0]
        assert duplicate.source_timestamp == base
        assert duplicate.count == 3

    def test_same_time_different_payload_is_not_duplicate(self, service) -> None:
        base = t0()
        events = [
            event(base, 1, 100.0),
            event(base, 2, 101.0),  # same millisecond, different trade
            event(base + timedelta(hours=1), 3, 102.0),
        ]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1)
        assert report.duplicate_count == 0

    def test_duplicate_requires_identical_payload_hash(self, service) -> None:
        base = t0()
        first = event(base, 1, 100.0)
        second = event(base, 2, 100.0)  # same time, different trade_id -> different payload
        events = [first, second, event(base + timedelta(hours=1), 3, 102.0)]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1)
        assert report.duplicate_count == 0


class TestOutlierDetection:
    def test_reports_outlier_beyond_robust_threshold(self, service) -> None:
        base = t0()
        events = [event(base + timedelta(hours=i), i, 100.0 + i) for i in range(1, 21)]
        events.append(event(base + timedelta(hours=21), 99, 200.0))  # 2x spike
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1, outlier_fields=("price",))
        assert report.outlier_count == 1
        outlier = report.outliers[0]
        assert outlier.field == "price"
        assert outlier.value == 200.0
        assert outlier.deviation > 5.0  # default k=5

    def test_no_outliers_without_field_selection(self, service) -> None:
        base = t0()
        events = [event(base + timedelta(hours=i), i, 100.0 + i) for i in range(1, 21)]
        events.append(event(base + timedelta(hours=21), 99, 200.0))
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1)
        assert report.outlier_count == 0
        assert report.scanned_fields == ()

    def test_non_numeric_field_is_ignored(self, service) -> None:
        base = t0()
        events = [event(base + timedelta(hours=i), i, 100.0 + i) for i in range(1, 21)]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1, outlier_fields=("symbol",))
        assert report.outlier_count == 0

    def test_constant_field_has_no_outliers(self, service) -> None:
        base = t0()
        events = [event(base + timedelta(hours=i), i, 100.0) for i in range(1, 21)]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1, outlier_fields=("price",))
        assert report.outlier_count == 0  # zero dispersion: nothing can deviate


class TestBoundedReport:
    def test_findings_capped_counts_exact(self, service) -> None:
        base = t0()
        events = [event(base + timedelta(hours=i), i, 100.0 + i) for i in range(1, 30)]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan(
            "btcusdt",
            1,
            outlier_fields=("price",),
            outlier_k=0.01,  # almost everything deviates
            max_findings_per_category=5,
        )
        assert len(report.outliers) == 5
        assert report.outlier_count == 28  # exact count preserved (median itself is not an outlier)
        assert report.as_dict()["outlier_count"] == 28

    def test_as_dict_round_trip(self, service) -> None:
        base = t0()
        events = [event(base + timedelta(hours=i), i, 100.0 + i) for i in range(1, 10)]
        DatasetService(service._store).build_raw_dataset(
            dataset_id="btcusdt", events=events, available_at=base
        )
        report = service.scan("btcusdt", 1, outlier_fields=("price",))
        data = report.as_dict()
        assert data["dataset_id"] == "btcusdt"
        assert data["version"] == 1
        assert data["kind"] == "raw"
        assert data["clean"] is True


class TestAuditCanReadLockedData:
    def test_scan_serves_locked_test_period(self, store) -> None:
        base = t0()
        dataset = DatasetService(store)
        dataset.build_raw_dataset(
            dataset_id="btcusdt",
            events=[event(base + timedelta(hours=i), i, 100.0 + i) for i in range(1, 10)],
            available_at=base,
        )
        dataset.lock_test_period(
            dataset_id="btcusdt",
            start=base + timedelta(hours=2),
            end=base + timedelta(hours=5),
            experiment_id="EXP-LOCK",
            claimed_by="operator",
            claimed_at=base,
        )
        # A training load over the whole version is refused (firewall)...
        with pytest.raises(ValueError, match="locked"):
            store.load_records("btcusdt", 1, purpose=DatasetPurpose.TRAINING)
        # ...but the audit scan reads it: it trains no model, so it is served.
        service = DatasetQualityService(store)
        report = service.scan("btcusdt", 1, expected_interval_seconds=3600)
        assert report.record_count == 9
        assert report.clean is True
