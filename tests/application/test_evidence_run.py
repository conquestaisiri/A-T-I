"""End-to-end evidence run tests (P5-005): dataset store -> evidence report.

The full wiring must work in one command: frozen RAW dataset version ->
data-quality gate -> locked out-of-sample window (firewall claim) -> OOS
evaluation of the real pipeline -> PBO variant family -> auditable passport
+ append-only report file. The firewall must hold afterwards: a training
load covering the locked window is refused at data-access time.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.dataset_service import DatasetService
from backend.application.research.decision_pipeline_evaluator import OutOfSampleReport
from backend.application.research.evidence_engine import EvidenceEngine
from backend.application.research.evidence_report import EvidenceReportWriter
from backend.application.research.evidence_run import (
    EvidenceRunConfig,
    EvidenceRunService,
)
from backend.application.research.historical_data_ingestor import (
    HistoricalDataIngestor,
)
from backend.domain.research.dataset import DatasetVersion
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import PassportStatus
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository
from backend.infrastructure.sqlite.passport_repository import SqlitePassportRepository

T0 = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
STEP = timedelta(minutes=1)


def bars(n: int, *, seed: int = 42, start: datetime = T0) -> list:
    from backend.domain.research.historical_bar import HistoricalBar

    rng = random.Random(seed)
    result: list[HistoricalBar] = []
    price = 100.0
    for i in range(n):
        ts = start + i * STEP
        change = rng.gauss(0.0, 0.001)
        open_price = price
        close_price = max(0.01, price * (1.0 + change))
        high = max(open_price, close_price) * (1.0 + abs(rng.gauss(0.0, 0.0005)))
        low = min(open_price, close_price) * (1.0 - abs(rng.gauss(0.0, 0.0005)))
        result.append(
            HistoricalBar(
                timestamp=ts,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=rng.uniform(50.0, 200.0),
            )
        )
        price = close_price
    return result


def freeze(tmp_path, series) -> tuple[DatasetService, DatasetVersion]:
    database = Database(tmp_path / "data.db")
    dataset_service = DatasetService(SqliteDatasetRepository(database))
    ingestor = HistoricalDataIngestor(dataset_service)
    version = ingestor.freeze_raw_dataset(
        series,
        dataset_id="btcusdt",
        symbol="btcusdt",
        available_at=T0 + timedelta(days=1),
        metadata={"source": "test_history"},
    )
    return dataset_service, version


def service(tmp_path) -> EvidenceRunService:
    database = Database(tmp_path / "data.db")
    store = SqliteDatasetRepository(database)
    engine = EvidenceEngine(SqlitePassportRepository(database))
    writer = EvidenceReportWriter(tmp_path / "reports" / "evidence")
    return EvidenceRunService(store, engine, writer)


def config(**overrides: Any) -> EvidenceRunConfig:
    fields: dict[str, Any] = dict(
        dataset_id="btcusdt",
        dataset_version=1,
        experiment_id="EXP-100",
        claimed_by="chief-architect",
        passport_id="STRAT-000185",
        symbol="btcusdt",
        train_size=60,
        test_size=20,
        n_trials=3,
        expected_interval_seconds=60,
    )
    fields.update(overrides)
    return EvidenceRunConfig(**fields)


def test_full_evidence_run(tmp_path):
    service_under_test = service(tmp_path)
    _, _ = freeze(tmp_path, bars(200))
    result = service_under_test.run(config())

    assert result.quality.is_usable
    assert result.quality.n_bars == 200
    assert result.quality.expected_interval_seconds == 60

    assert result.passport.passport_id == "STRAT-000185"
    assert result.passport.status in (PassportStatus.CANDIDATE, PassportStatus.RETIRED)
    assert result.passport.dataset_version == 1
    assert result.passport.test_period == result.locked_test_period
    assert result.passport.verdict.reasons, "verdict must carry its reasons"

    assert result.variants.pbo.pbo >= 0.0
    assert {v.name for v in result.variants.variants} == {
        "default",
        "conservative",
        "aggressive",
    }

    assert result.report_path.exists()
    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["report_type"] == "strategy_passport"
    assert payload["extra"]["quality"]["is_usable"] is True
    assert payload["extra"]["variants_report"]["pbo"]["pbo"] == result.variants.pbo.pbo

    summary = result.summary()
    assert summary["passport_id"] == "STRAT-000185"
    assert summary["quality_usable"] is True


def test_out_of_sample_window_locked_and_firewall_holds(tmp_path):
    service_under_test = service(tmp_path)
    dataset_service, version = freeze(tmp_path, bars(200))
    assert version.version == 1
    service_under_test.run(config())

    locks = dataset_service.test_locks("btcusdt")
    assert len(locks) == 1
    locked_window = locks[0]

    with pytest.raises(ValueError, match="locked test"):
        dataset_service.records_available_by("btcusdt", 1, cutoff=T0 + timedelta(days=2))

    # The same window is not claimed twice.
    service_under_test.run(config(passport_id="STRAT-000186"))
    assert len(dataset_service.test_locks("btcusdt")) == 1
    assert dataset_service.test_locks("btcusdt")[0] == locked_window


def test_quality_gate_refuses_gappy_data(tmp_path):
    service_under_test = service(tmp_path)
    series = bars(200)
    series[-1] = bars(1, start=T0 + 205 * STEP)[0]  # 5-minute hole before the last bar
    _, _ = freeze(tmp_path, series)
    with pytest.raises(ValueError, match="data-quality gate failed"):
        service_under_test.run(config())


def test_quality_gate_refuses_empty_data(tmp_path):
    service_under_test = service(tmp_path)
    with pytest.raises(ValueError, match="no records"):
        service_under_test.run(config())


def test_too_short_series_refused(tmp_path):
    service_under_test = service(tmp_path)
    _, _ = freeze(tmp_path, bars(50))
    with pytest.raises(ValueError, match="too short"):
        service_under_test.run(config())


def test_wrong_symbol_refused(tmp_path):
    service_under_test = service(tmp_path)
    _, _ = freeze(tmp_path, bars(200))
    with pytest.raises(ValueError, match="expected symbol"):
        service_under_test.run(config(symbol="ethusdt"))


def test_embargo_surfaced_in_passport_and_report(tmp_path):
    """T1-6-1: the applied validation gap must be visible in the evidence."""
    service_under_test = service(tmp_path)
    _, _ = freeze(tmp_path, bars(200))
    result = service_under_test.run(config(embargo=4.0))

    assert result.passport.evidence["cv_spec"]["embargo"] == 4.0
    assert result.report.cv_spec["embargo"] == 4.0
    assert result.passport.evidence["cv_spec"]["method"] == "walk_forward"

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert payload["passport"]["evidence"]["cv_spec"]["embargo"] == 4.0
    assert payload["extra"]["out_of_sample_report"]["cv_spec"]["embargo"] == 4.0


def test_report_archive_is_append_only(tmp_path):
    database = Database(tmp_path / "data.db")
    store = SqliteDatasetRepository(database)
    engine = EvidenceEngine(SqlitePassportRepository(database))
    writer = EvidenceReportWriter(tmp_path / "reports" / "evidence")
    run_service = EvidenceRunService(store, engine, writer)
    ingestor = HistoricalDataIngestor(DatasetService(store))
    ingestor.freeze_raw_dataset(
        bars(200),
        dataset_id="btcusdt",
        symbol="btcusdt",
        available_at=T0 + timedelta(days=1),
    )
    run_service.run(config())
    # The engine refuses to re-issue an existing passport id (immutability)...
    with pytest.raises(ValueError, match="already exists"):
        run_service.run(config())
    # ...and the writer refuses to overwrite a report file on disk, so a
    # second passport with the same id can never clobber the first report.
    engine.issue_passport(
        passport_id="STRAT-000187",
        hypothesis="archive probe",
        dataset_id="btcusdt",
        dataset_version=1,
        features=(),
        model="RuleBasedSolver",
        trial_count=1,
        report=result_report(),
        now=T0,
    )
    probe_passport = engine.passport("STRAT-000187")
    assert probe_passport is not None
    writer.write(probe_passport, engine.lifecycle("STRAT-000187"))
    with pytest.raises(ValueError, match="append-only"):
        writer.write(probe_passport, engine.lifecycle("STRAT-000187"))


def result_report() -> OutOfSampleReport:
    return OutOfSampleReport(
        symbol="btcusdt",
        costs=EvaluationCosts(half_spread_pct=0.0002, taker_fee_pct=0.0004),
        cv_spec={"train_size": 60, "test_size": 20, "expanding": True},
        folds=(),
        pooled=PooledEvidence(
            n_folds=6,
            total_test_bars=120,
            total_trades=0,
            total_wins=0,
            total_losses=0,
            total_fees=0.0,
            total_slippage_bps=0.0,
            gross_profit=0.0,
            gross_loss=0.0,
            mean_return_pct=0.1,
            median_return_pct=0.0,
            mean_excess_return_pct=0.0,
            positive_fold_rate=0.5,
            beats_buy_and_hold_rate=0.5,
            mean_max_drawdown_pct=-5.0,
            deflated_sharpe=0.5,
            reasoner="RuleBasedSolver",
            cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
        ),
    )
