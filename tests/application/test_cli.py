"""Tests for the operator console (wiring step 2026-08-17).

The CLI is the operator surface for the research institution: ingest
klines -> frozen RAW dataset -> locked OOS evidence run -> passport ->
report, plus read-only ledger views. It must work end to end on real
artifacts (tmp DB + tmp CSV), refuse duplicate passports, and exit
non-zero with a readable error on expected failures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from backend.cli import main


def write_klines_csv(path, n=160, *, binance=False, seed=3) -> str:
    import random

    rng = random.Random(seed)
    price = 100.0
    rows: list[str] = []
    if not binance:
        rows.append("open_time,open,high,low,close,volume")
    t = datetime(2024, 1, 1, tzinfo=UTC)
    for _ in range(n):
        close = price * (1.0 + rng.gauss(0.0003, 0.005))
        high = max(price, close) * 1.002
        low = min(price, close) * 0.998
        if binance:
            rows.append(
                f"{int(t.timestamp() * 1000)},{price:.4f},{high:.4f},{low:.4f},"
                f"{close:.4f},{rng.uniform(50, 500):.2f}"
            )
        else:
            rows.append(
                f"{t.isoformat()},{price:.4f},{high:.4f},{low:.4f},"
                f"{close:.4f},{rng.uniform(50, 500):.2f}"
            )
        price = close
        t += timedelta(hours=1)
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


def run(argv):
    return main(argv)


class TestIngest:
    def test_header_csv_freezes_version(self, tmp_path):
        csv_path = tmp_path / "klines.csv"
        write_klines_csv(csv_path)
        assert (
            run(
                [
                    "ingest",
                    str(csv_path),
                    "--dataset-id",
                    "btcusdt",
                    "--symbol",
                    "btcusdt",
                    "--db",
                    str(tmp_path / "ev.db"),
                ]
            )
            == 0
        )
        assert run(["datasets", "show", "btcusdt", "--db", str(tmp_path / "ev.db")]) == 0

    def test_binance_klines_freezes_version(self, tmp_path):
        csv_path = tmp_path / "binance.csv"
        write_klines_csv(csv_path, binance=True)
        assert (
            run(
                [
                    "ingest",
                    str(csv_path),
                    "--dataset-id",
                    "btcusdt",
                    "--symbol",
                    "btcusdt",
                    "--binance",
                    "--db",
                    str(tmp_path / "ev.db"),
                ]
            )
            == 0
        )

    def test_missing_file_fails(self, tmp_path):
        assert (
            run(
                [
                    "ingest",
                    str(tmp_path / "nope.csv"),
                    "--dataset-id",
                    "x",
                    "--symbol",
                    "x",
                    "--db",
                    str(tmp_path / "ev.db"),
                ]
            )
            == 1
        )

    def test_bad_header_fails(self, tmp_path):
        csv_path = tmp_path / "bad.csv"
        csv_path.write_text("foo,bar\n1,2\n", encoding="utf-8")
        assert (
            run(
                [
                    "ingest",
                    str(csv_path),
                    "--dataset-id",
                    "x",
                    "--symbol",
                    "x",
                    "--db",
                    str(tmp_path / "ev.db"),
                ]
            )
            == 1
        )


class TestEvidenceRun:
    def _db_args(self, tmp_path) -> list[str]:
        return ["--db", str(tmp_path / "ev.db")]

    def _ingest(self, tmp_path, n=200):
        csv_path = tmp_path / "klines.csv"
        write_klines_csv(csv_path, n=n)
        assert (
            run(
                [
                    "ingest",
                    str(csv_path),
                    "--dataset-id",
                    "btcusdt",
                    "--symbol",
                    "btcusdt",
                    *self._db_args(tmp_path),
                ]
            )
            == 0
        )

    def test_run_issues_passport_and_report(self, tmp_path):
        self._ingest(tmp_path)
        report_dir = tmp_path / "reports"
        assert (
            run(
                [
                    "evidence",
                    "run",
                    "--dataset-id",
                    "btcusdt",
                    "--dataset-version",
                    "1",
                    "--passport-id",
                    "STRAT-CLI-001",
                    "--experiment-id",
                    "EXP-CLI-001",
                    "--claimed-by",
                    "test",
                    "--symbol",
                    "btcusdt",
                    "--train-size",
                    "100",
                    "--test-size",
                    "20",
                    "--report-dir",
                    str(report_dir),
                    *self._db_args(tmp_path),
                ]
            )
            == 0
        )
        report_file = report_dir / "STRAT-CLI-001.json"
        assert report_file.exists()
        payload = json.loads(report_file.read_text(encoding="utf-8"))
        assert payload["passport"]["passport_id"] == "STRAT-CLI-001"
        assert payload["extra"]["run"]["claimed_by"] == "test"

    def test_duplicate_passport_refused(self, tmp_path):
        self._ingest(tmp_path)
        args = [
            "evidence",
            "run",
            "--dataset-id",
            "btcusdt",
            "--dataset-version",
            "1",
            "--passport-id",
            "STRAT-CLI-002",
            "--experiment-id",
            "EXP-CLI-002",
            "--claimed-by",
            "test",
            "--symbol",
            "btcusdt",
            "--report-dir",
            str(tmp_path / "reports"),
            *self._db_args(tmp_path),
        ]
        assert run(args) == 0
        assert run(args) == 1  # immutability: a second issue is refused

    def test_unknown_dataset_fails(self, tmp_path):
        assert (
            run(
                [
                    "evidence",
                    "run",
                    "--dataset-id",
                    "nope",
                    "--dataset-version",
                    "1",
                    "--passport-id",
                    "STRAT-CLI-003",
                    "--experiment-id",
                    "EXP",
                    "--claimed-by",
                    "test",
                    "--symbol",
                    "btcusdt",
                    *self._db_args(tmp_path),
                ]
            )
            == 1
        )


class TestEvidenceDemo:
    def test_demo_runs_full_pipeline(self, tmp_path):
        report_dir = tmp_path / "reports"
        assert (
            run(
                [
                    "evidence",
                    "demo",
                    "--passport-id",
                    "STRAT-DEMO-001",
                    "--n-bars",
                    "300",
                    "--seed",
                    "7",
                    "--report-dir",
                    str(report_dir),
                    "--db",
                    str(tmp_path / "ev.db"),
                ]
            )
            == 0
        )
        assert (report_dir / "STRAT-DEMO-001.json").exists()
        assert run(["passports", "show", "STRAT-DEMO-001", "--db", str(tmp_path / "ev.db")]) == 0

    def test_demo_uses_deterministic_seed(self, tmp_path):
        def run_demo(report_dir, db_name):
            return run(
                [
                    "evidence",
                    "demo",
                    "--passport-id",
                    "STRAT-DEMO-7",
                    "--n-bars",
                    "300",
                    "--seed",
                    "7",
                    "--report-dir",
                    str(report_dir),
                    "--db",
                    str(tmp_path / db_name),
                ]
            )

        assert run_demo(tmp_path / "r1", "ev1.db") == 0
        assert run_demo(tmp_path / "r2", "ev2.db") == 0
        r1 = json.loads((tmp_path / "r1" / "STRAT-DEMO-7.json").read_text(encoding="utf-8"))
        r2 = json.loads((tmp_path / "r2" / "STRAT-DEMO-7.json").read_text(encoding="utf-8"))
        assert r1["passport"]["evidence"]["pooled"] == r2["passport"]["evidence"]["pooled"]


class TestDatasets:
    def test_quality_scans_frozen_version(self, tmp_path):
        csv_path = tmp_path / "klines.csv"
        write_klines_csv(csv_path)
        db = str(tmp_path / "ev.db")
        assert (
            run(
                [
                    "ingest",
                    str(csv_path),
                    "--dataset-id",
                    "btcusdt",
                    "--symbol",
                    "btcusdt",
                    "--db",
                    db,
                ]
            )
            == 0
        )
        assert (
            run(
                [
                    "datasets",
                    "quality",
                    "btcusdt",
                    "--version",
                    "1",
                    "--expected-interval-seconds",
                    "3600",
                    "--outlier-fields",
                    "price,volume",
                    "--db",
                    db,
                ]
            )
            == 0
        )

    def test_quality_unknown_version_fails(self, tmp_path):
        csv_path = tmp_path / "klines.csv"
        write_klines_csv(csv_path)
        db = str(tmp_path / "ev.db")
        run(
            [
                "ingest",
                str(csv_path),
                "--dataset-id",
                "btcusdt",
                "--symbol",
                "btcusdt",
                "--db",
                db,
            ]
        )
        assert run(["datasets", "quality", "btcusdt", "--version", "99", "--db", db]) == 1

    def test_leaks_audits_clean_dataset(self, tmp_path):
        csv_path = tmp_path / "klines.csv"
        write_klines_csv(csv_path)
        db = str(tmp_path / "ev.db")
        assert (
            run(
                [
                    "ingest",
                    str(csv_path),
                    "--dataset-id",
                    "btcusdt",
                    "--symbol",
                    "btcusdt",
                    "--db",
                    db,
                ]
            )
            == 0
        )
        assert run(["datasets", "leaks", "btcusdt", "--db", db]) == 0

    def test_leaks_unknown_dataset_fails(self, tmp_path):
        assert run(["datasets", "leaks", "NOPE", "--db", str(tmp_path / "ev.db")]) == 1


class TestExperiments:
    def test_lineage_unknown_fails(self, tmp_path):
        assert run(["experiments", "lineage", "NOPE", "--db", str(tmp_path / "ev.db")]) == 1

    def test_lineage_walks_parent_chain(self, tmp_path):
        from datetime import UTC

        from backend.domain.research.experiment import (
            ExperimentGroup,
            ExperimentRecord,
            ExperimentStatus,
        )
        from backend.infrastructure.sqlite.database import Database
        from backend.infrastructure.sqlite.experiment_repository import SqliteExperimentRepository

        db = str(tmp_path / "ev.db")
        repo = SqliteExperimentRepository(Database(db))
        for exp_id, parent in (("exp-1", None), ("exp-2", "exp-1")):
            repo.save(
                ExperimentRecord(
                    experiment_id=exp_id,
                    created_at=datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC),
                    hypothesis="momentum feature adds signal after costs",
                    dataset_id="btcusdt",
                    dataset_version=1,
                    group=ExperimentGroup.TUNING,
                    scorer_name="threshold",
                    features=("trend",),
                    label_definition={"kind": "fixed_horizon", "horizon": 5},
                    cost_model={"half_spread_pct": 0.0002},
                    metrics={},
                    status=ExperimentStatus.RUNNING,
                    parent_experiment_id=parent,
                )
            )
        assert run(["experiments", "lineage", "exp-2", "--db", db]) == 0


class TestFetch:
    """The ``fetch`` subcommand must freeze a RAW dataset from the network.

    The network itself is stubbed at the module boundary; what matters is
    that fetch -> validate -> freeze is one honest, atomic operator step.
    """

    def _fake_fetcher(self, monkeypatch, n=50):
        import random

        from backend.domain.research.historical_bar import HistoricalBar

        rng = random.Random(7)
        price = 100.0
        bars = []
        t = datetime(2026, 1, 1, tzinfo=UTC)
        for _ in range(n):
            close = price * (1.0 + rng.gauss(0.0003, 0.005))
            bars.append(
                HistoricalBar(
                    timestamp=t,
                    open=price,
                    high=max(price, close) * 1.002,
                    low=min(price, close) * 0.998,
                    close=close,
                    volume=rng.uniform(50, 500),
                )
            )
            price = close
            t += timedelta(hours=1)

        class _StubFetcher:
            def fetch(self, symbol, **kwargs):
                assert symbol == "BTCUSDT"
                return bars

            def close(self):
                pass

        monkeypatch.setattr("backend.cli.BinanceKlinesFetcher", _StubFetcher)

    def test_fetch_freezes_dataset(self, tmp_path, monkeypatch):
        self._fake_fetcher(monkeypatch)
        db = str(tmp_path / "ev.db")
        assert (
            run(
                [
                    "fetch",
                    "BTCUSDT",
                    "--dataset-id",
                    "binance-btcusdt",
                    "--interval",
                    "1h",
                    "--db",
                    db,
                ]
            )
            == 0
        )
        assert run(["datasets", "show", "binance-btcusdt", "--db", db]) == 0

    def test_fetch_network_failure_fails(self, tmp_path, monkeypatch):
        class _BrokenFetcher:
            def fetch(self, symbol, **kwargs):
                raise RuntimeError("Binance klines request failed for BTCUSDT: boom")

            def close(self):
                pass

        monkeypatch.setattr("backend.cli.BinanceKlinesFetcher", _BrokenFetcher)
        assert (
            run(
                [
                    "fetch",
                    "BTCUSDT",
                    "--dataset-id",
                    "binance-btcusdt",
                    "--db",
                    str(tmp_path / "ev.db"),
                ]
            )
            == 1
        )


class TestPassports:
    def test_list_empty(self, tmp_path):
        assert run(["passports", "list", "--db", str(tmp_path / "ev.db")]) == 0

    def test_show_unknown_fails(self, tmp_path):
        assert run(["passports", "show", "NOPE", "--db", str(tmp_path / "ev.db")]) == 1

    def test_show_prints_lifecycle(self, tmp_path):
        report_dir = tmp_path / "reports"
        db = str(tmp_path / "ev.db")
        assert (
            run(
                [
                    "evidence",
                    "demo",
                    "--passport-id",
                    "STRAT-DEMO-002",
                    "--report-dir",
                    str(report_dir),
                    "--db",
                    db,
                ]
            )
            == 0
        )
        assert run(["passports", "list", "--db", db]) == 0
        assert run(["passports", "show", "STRAT-DEMO-002", "--db", db]) == 0
