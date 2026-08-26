# backend/cli.py
"""Operator console for the ATI research institution (wiring step 2026-08-17).

Before this module existed, the entire research/evidence factory
(dataset freeze -> quality gate -> locked OOS -> passport -> report) was
reachable only through tests. This CLI is the operator surface: one
command turns real history into an auditable evidence report, and the
passport ledger is readable for review.

Everything here is library/research-only. Nothing in the live path
imports this module; nothing it does touches live trading.

Usage
-----
    py -3 -m backend.cli --help
    py -3 -m backend.cli ingest btcusdt_1h.csv --dataset-id btcusdt --symbol btcusdt
    py -3 -m backend.cli evidence run --dataset-id btcusdt --dataset-version 1 \
        --passport-id STRAT-BTC-001 --experiment-id EXP-BTC-001 --claimed-by operator
    py -3 -m backend.cli evidence demo --passport-id STRAT-DEMO-001   # no real data needed
    py -3 -m backend.cli passports show STRAT-BTC-001
    py -3 -m backend.cli datasets list

Exit codes: 0 on success, 1 on any expected error (bad data, unknown
ids, refused duplicate passports).
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import random
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from backend.application.research.binance_klines_fetcher import BinanceKlinesFetcher
from backend.application.research.dataset_quality_service import DatasetQualityService
from backend.application.research.dataset_service import DatasetService
from backend.application.research.evidence_engine import EvidenceEngine
from backend.application.research.evidence_report import EvidenceReportWriter
from backend.application.research.evidence_run import (
    EvidenceRunConfig,
    EvidenceRunService,
)
from backend.application.research.experiment_lineage_service import ExperimentLineageService
from backend.application.research.historical_data_ingestor import HistoricalDataIngestor
from backend.application.research.leak_detector_service import LeakDetectorService
from backend.domain.research.historical_bar import HistoricalBar
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository
from backend.infrastructure.sqlite.experiment_repository import SqliteExperimentRepository
from backend.infrastructure.sqlite.passport_repository import SqlitePassportRepository

DEFAULT_DB = "data/evidence.db"
DEFAULT_REPORT_DIR = "reports/evidence"


class _CliContext:
    """One process's wiring: shared DB plus the research services."""

    def __init__(self, db_path: str) -> None:
        self.database = Database(Path(db_path))
        self.dataset_store = SqliteDatasetRepository(self.database)
        self.passport_store = SqlitePassportRepository(self.database)
        self.experiment_store = SqliteExperimentRepository(self.database)
        self.datasets = DatasetService(self.dataset_store)
        self.ingestor = HistoricalDataIngestor(self.datasets)
        self.experiments = ExperimentLineageService(self.experiment_store)

    def evidence_engine(self, report_dir: str) -> EvidenceEngine:
        return EvidenceEngine(self.passport_store)

    def evidence_run(self, report_dir: str) -> EvidenceRunService:
        return EvidenceRunService(
            self.dataset_store,
            EvidenceEngine(self.passport_store),
            EvidenceReportWriter(report_dir),
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI; returns the process exit code (0 = success)."""
    # Operator surface: suppress pipeline warmup chatter; the summaries
    # printed below are the report, not the evaluator's internals.
    logging.basicConfig(level=logging.ERROR)
    parser = argparse.ArgumentParser(
        prog="ati",
        description="ATI research institution operator console "
        "(research-only: nothing here touches the live path).",
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"evidence ledger database (default: {DEFAULT_DB})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_datasets = sub.add_parser("datasets", help="inspect the frozen dataset ledger")
    _inherit_db(p_datasets)
    ds_sub = p_datasets.add_subparsers(dest="dataset_command", required=True)
    ds_list = ds_sub.add_parser("list", help="list dataset ids and versions")
    _inherit_db(ds_list)
    ds_show = ds_sub.add_parser("show", help="show a dataset version and its locks")
    _inherit_db(ds_show)
    ds_show.add_argument("dataset_id")
    ds_show.add_argument("--version", type=int, default=None)
    ds_quality = ds_sub.add_parser(
        "quality",
        help="scan a frozen version for gaps, duplicates, and outliers (T1-1-4)",
    )
    _inherit_db(ds_quality)
    ds_quality.add_argument("dataset_id")
    ds_quality.add_argument("--version", type=int, required=True)
    ds_quality.add_argument("--expected-interval-seconds", type=float, default=None)
    ds_quality.add_argument("--outlier-fields", default=None, help="comma-separated payload fields")
    ds_quality.add_argument("--outlier-k", type=float, default=5.0)
    ds_quality.add_argument("--max-findings", type=int, default=100)
    ds_leaks = ds_sub.add_parser(
        "leaks",
        help="runtime leak audit: training loads vs test-period locks (T1-3-1/T1-5-1)",
    )
    _inherit_db(ds_leaks)
    ds_leaks.add_argument("dataset_id")

    p_ingest = sub.add_parser(
        "ingest",
        help="freeze an OHLCV klines CSV as the next RAW dataset version",
    )
    _inherit_db(p_ingest)
    p_ingest.add_argument("csv_path")
    p_ingest.add_argument("--dataset-id", required=True)
    p_ingest.add_argument("--symbol", required=True)
    p_ingest.add_argument("--available-at", default=None, help="download time (ISO); default now")
    p_ingest.add_argument(
        "--binance",
        action="store_true",
        help="headerless Binance klines columns: open_time(ms),open,high,low,close,volume",
    )

    p_fetch = sub.add_parser(
        "fetch",
        help="fetch real klines from the public Binance API and freeze a RAW dataset version",
    )
    _inherit_db(p_fetch)
    p_fetch.add_argument("symbol")
    p_fetch.add_argument("--dataset-id", required=True)
    p_fetch.add_argument("--interval", default="1h", help="kline interval (default: 1h)")
    p_fetch.add_argument("--limit", type=int, default=1000, help="bars per request (max 1000)")
    p_fetch.add_argument("--start-time", default=None, help="inclusive UTC start (ISO or epoch)")
    p_fetch.add_argument("--end-time", default=None, help="exclusive UTC end (ISO or epoch)")
    p_fetch.add_argument(
        "--keep-incomplete",
        action="store_true",
        help="keep the still-forming candle (off by default; datasets must not hold partial bars)",
    )
    p_fetch.add_argument("--available-at", default=None, help="download time (ISO); default now")

    p_evidence = sub.add_parser(
        "evidence",
        help="run the locked out-of-sample evidence pipeline to a passport",
    )
    _inherit_db(p_evidence)
    ev_sub = p_evidence.add_subparsers(dest="evidence_command", required=True)
    p_run = ev_sub.add_parser("run", help="run evidence on a frozen dataset version")
    _inherit_db(p_run)
    p_run.add_argument("--dataset-id", required=True)
    p_run.add_argument("--dataset-version", type=int, required=True)
    p_run.add_argument("--passport-id", required=True)
    p_run.add_argument("--experiment-id", required=True)
    p_run.add_argument("--claimed-by", required=True)
    p_run.add_argument("--symbol", required=True)
    p_run.add_argument("--train-size", type=int, default=100)
    p_run.add_argument("--test-size", type=int, default=20)
    p_run.add_argument(
        "--embargo",
        type=float,
        default=0.0,
        help="validation gap cleared after each test window, in bar intervals "
        "(surfaced in the report/passport cv_spec, T1-6-1)",
    )
    p_run.add_argument("--n-trials", type=int, default=1)
    p_run.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)
    p_run.add_argument("--expected-interval-seconds", type=int, default=None)
    p_run.add_argument(
        "--outlier-z-threshold",
        type=float,
        default=None,
        help="robust-z threshold for close-price outliers (default 5.0; "
        "recorded in the report — loosening is an explicit operator decision)",
    )
    p_demo = ev_sub.add_parser(
        "demo",
        help="run the full evidence pipeline on a deterministic synthetic series",
    )
    _inherit_db(p_demo)
    p_demo.add_argument("--passport-id", default="STRAT-DEMO-001")
    p_demo.add_argument("--symbol", default="btcusdt")
    p_demo.add_argument("--n-bars", type=int, default=400)
    p_demo.add_argument("--seed", type=int, default=7)
    p_demo.add_argument("--train-size", type=int, default=100)
    p_demo.add_argument("--test-size", type=int, default=20)
    p_demo.add_argument("--report-dir", default=DEFAULT_REPORT_DIR)

    p_passports = sub.add_parser("passports", help="inspect the strategy passport ledger")
    _inherit_db(p_passports)
    pp_sub = p_passports.add_subparsers(dest="passport_command", required=True)
    pp_list = pp_sub.add_parser("list", help="list issued passports")
    _inherit_db(pp_list)
    pp_show = pp_sub.add_parser("show", help="show one passport plus its lifecycle")
    _inherit_db(pp_show)
    pp_show.add_argument("passport_id")

    p_experiments = sub.add_parser(
        "experiments", help="inspect the research experiment registry (T1-4-1 lineage)"
    )
    _inherit_db(p_experiments)
    ex_sub = p_experiments.add_subparsers(dest="experiment_command", required=True)
    ex_lineage = ex_sub.add_parser(
        "lineage", help="walk the parent/child experiment DAG for one experiment"
    )
    _inherit_db(ex_lineage)
    ex_lineage.add_argument("experiment_id")

    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        ctx = _CliContext(args.db)
        if args.command == "datasets":
            return _datasets(ctx, args)
        if args.command == "ingest":
            return _ingest(ctx, args)
        if args.command == "fetch":
            return _fetch(ctx, args)
        if args.command == "evidence":
            return _evidence(ctx, args)
        if args.command == "passports":
            return _passports(ctx, args)
        if args.command == "experiments":
            return _experiments(ctx, args)
        return 1
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


# -- subcommands --------------------------------------------------------------


def _inherit_db(parser: argparse.ArgumentParser) -> None:
    """Accept ``--db`` after the subcommand too (argparse parents would
    force it before; operators should not need to remember the order)."""
    parser.add_argument("--db", default=argparse.SUPPRESS)


def _datasets(ctx: _CliContext, args: argparse.Namespace) -> int:
    if args.dataset_command == "list":
        ids = ctx.dataset_store.list_datasets()
        if not ids:
            print("no datasets in the ledger")
            return 0
        for dataset_id in ids:
            versions = ctx.dataset_store.list_versions(dataset_id)
            latest = versions[-1] if versions else None
            print(f"{dataset_id}: {len(versions)} version(s)", end="")
            if latest is not None:
                print(f" (latest v{latest.version}, {latest.record_count} records)")
            else:
                print()
        return 0

    if args.dataset_command == "quality":
        return _datasets_quality(ctx, args)

    if args.dataset_command == "leaks":
        return _datasets_leaks(ctx, args)

    versions = ctx.dataset_store.list_versions(args.dataset_id)
    if not versions:
        print(f"error: unknown dataset {args.dataset_id}", file=sys.stderr)
        return 1
    if args.version is not None:
        versions = [v for v in versions if v.version == args.version]
        if not versions:
            print(f"error: dataset {args.dataset_id} has no v{args.version}", file=sys.stderr)
            return 1
    for version in versions:
        print(_version_summary(version))
        locks = ctx.dataset_store.list_test_locks(args.dataset_id)
        for lock in locks:
            print(f"  locked test period: {lock.start.isoformat()} -> {lock.end.isoformat()}")
            print(f"    claimed by {lock.claimed_by} for {lock.experiment_id}")
    return 0


def _datasets_quality(ctx: _CliContext, args: argparse.Namespace) -> int:
    """Scan one frozen version and print the quality report (T1-1-4)."""
    fields = (
        tuple(f.strip() for f in args.outlier_fields.split(",") if f.strip())
        if args.outlier_fields
        else ()
    )
    report = DatasetQualityService(ctx.dataset_store).scan(
        args.dataset_id,
        args.version,
        expected_interval_seconds=args.expected_interval_seconds,
        outlier_fields=fields,
        outlier_k=args.outlier_k,
        max_findings_per_category=args.max_findings,
    )
    print(
        f"{report.dataset_id} v{report.version} ({report.kind.value}): "
        f"{report.record_count} records"
    )
    print(
        f"  gaps: {report.gap_count}  duplicates: {report.duplicate_count}  "
        f"outliers: {report.outlier_count}"
    )
    if report.clean:
        print("  clean: no quality findings")
        return 0
    for gap in report.gaps:
        print(
            f"  gap: {gap.after.isoformat(timespec='seconds')} -> "
            f"{gap.before.isoformat(timespec='seconds')} "
            f"({gap.gap_seconds:.0f}s, expected {gap.expected_seconds:.0f}s)"
        )
    for duplicate in report.duplicates:
        when = duplicate.source_timestamp.isoformat(timespec="seconds")
        print(f"  duplicate x{duplicate.count}: {when} (payload {duplicate.payload_hash[:12]}...)")
    for outlier in report.outliers:
        print(
            f"  outlier: {outlier.source_timestamp.isoformat(timespec='seconds')} "
            f"{outlier.field}={outlier.value:.4f} (dev {outlier.deviation:.2f})"
        )
    return 0


def _datasets_leaks(ctx: _CliContext, args: argparse.Namespace) -> int:
    """Audit training loads vs test-period locks and print the report (T1-3-1)."""
    report = LeakDetectorService(ctx.dataset_store).audit(args.dataset_id)
    print(f"{report.dataset_id}: {len(report.versions)} version(s) audited")
    for audit in report.versions:
        overlap = len(audit.overlapping_locks)
        state = "REFUSED" if audit.firewall_refused_training else "SERVED"
        print(
            f"  v{audit.version}: {audit.record_count} records "
            f"[{audit.source_start.isoformat(timespec='seconds')}.."
            f"{audit.source_end.isoformat(timespec='seconds')}] "
            f"{overlap} overlapping lock(s), training {state}, "
            f"{audit.locked_record_count} protected record(s)"
        )
    for coverage in report.coverages:
        print(
            f"  lock [{coverage.lock.start.isoformat(timespec='seconds')}.."
            f"{coverage.lock.end.isoformat(timespec='seconds')}] "
            f"protects {coverage.protected_record_count} record(s) "
            f"(claimed by {coverage.lock.claimed_by})"
        )
    for finding in report.findings:
        print(f"  {finding.kind.value}: {finding.detail}")
    if report.clean:
        print("  clean: no leak findings")
    return 0


def _ingest(ctx: _CliContext, args: argparse.Namespace) -> int:
    bars = _read_klines_csv(Path(args.csv_path), binance=args.binance)
    available_at = (
        datetime.fromisoformat(args.available_at).replace(tzinfo=UTC) if args.available_at else None
    )
    version = ctx.ingestor.freeze_raw_dataset(
        bars,
        dataset_id=args.dataset_id,
        symbol=args.symbol,
        available_at=available_at,
        metadata={"source": "operator CLI ingest", "source_file": args.csv_path},
    )
    print(f"frozen {args.dataset_id} v{version.version}: {version.record_count} records")
    print(_version_summary(version))
    return 0


def _fetch(ctx: _CliContext, args: argparse.Namespace) -> int:
    fetcher = BinanceKlinesFetcher()
    try:
        bars = fetcher.fetch(
            args.symbol,
            interval=args.interval,
            limit=args.limit,
            start_time=_parse_bar_time(args.start_time) if args.start_time else None,
            end_time=_parse_bar_time(args.end_time) if args.end_time else None,
            drop_incomplete=not args.keep_incomplete,
        )
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        fetcher.close()
    available_at = (
        datetime.fromisoformat(args.available_at).replace(tzinfo=UTC) if args.available_at else None
    )
    version = ctx.ingestor.freeze_raw_dataset(
        bars,
        dataset_id=args.dataset_id,
        symbol=args.symbol,
        available_at=available_at,
        metadata={
            "source": "binance public klines REST",
            "symbol": args.symbol,
            "interval": args.interval,
            "drop_incomplete": not args.keep_incomplete,
        },
    )
    print(f"frozen {args.dataset_id} v{version.version}: {version.record_count} records")
    print(_version_summary(version))
    return 0


def _evidence(ctx: _CliContext, args: argparse.Namespace) -> int:
    if args.evidence_command == "demo":
        dataset_id = f"demo-{args.symbol}"
        version = ctx.ingestor.freeze_raw_dataset(
            _synthetic_bars(args.symbol, args.n_bars, args.seed),
            dataset_id=dataset_id,
            symbol=args.symbol,
            metadata={"source": "synthetic demo series"},
        )
        print(f"frozen {dataset_id} v{version.version}: {version.record_count} records (synthetic)")
        return _run_evidence(
            ctx,
            EvidenceRunConfig(
                dataset_id=dataset_id,
                dataset_version=version.version,
                experiment_id=f"DEMO-{args.passport_id}",
                claimed_by="cli-demo",
                passport_id=args.passport_id,
                symbol=args.symbol,
                train_size=args.train_size,
                test_size=args.test_size,
                report_dir=args.report_dir,
            ),
            args.report_dir,
        )

    config = EvidenceRunConfig(
        dataset_id=args.dataset_id,
        dataset_version=args.dataset_version,
        experiment_id=args.experiment_id,
        claimed_by=args.claimed_by,
        passport_id=args.passport_id,
        symbol=args.symbol,
        train_size=args.train_size,
        test_size=args.test_size,
        embargo=args.embargo,
        n_trials=args.n_trials,
        report_dir=args.report_dir,
        expected_interval_seconds=args.expected_interval_seconds,
        outlier_z_threshold=args.outlier_z_threshold,
    )
    return _run_evidence(ctx, config, args.report_dir)


def _run_evidence(ctx: _CliContext, config: EvidenceRunConfig, report_dir: str) -> int:
    result = ctx.evidence_run(report_dir).run(config)
    print(_run_summary(result.summary()))
    return 0


def _passports(ctx: _CliContext, args: argparse.Namespace) -> int:
    if args.passport_command == "list":
        passports = ctx.passport_store.all_passports()
        if not passports:
            print("no passports in the ledger")
            return 0
        for record in passports:
            symbol = str(record.evidence.get("symbol", "?"))
            print(
                f"{record.passport_id}: {record.status.value} "
                f"({record.verdict.verdict.value}, {symbol})"
            )
        return 0

    passport = ctx.passport_store.load_passport(args.passport_id)
    if passport is None:
        print(f"error: unknown passport {args.passport_id}", file=sys.stderr)
        return 1
    print(json.dumps(passport.as_dict(), indent=2, sort_keys=True))
    events = ctx.passport_store.lifecycle(args.passport_id)
    if events:
        print("\nlifecycle:")
        for event in events:
            print(
                f"  {event.occurred_at.isoformat(timespec='seconds')} "
                f"{event.event_type}: "
                f"{(event.from_status.value if event.from_status else '-')} -> "
                f"{(event.to_status.value if event.to_status else '-')} "
                f"({event.reason})"
            )
    return 0


def _experiments(ctx: _CliContext, args: argparse.Namespace) -> int:
    """Walk the experiment DAG and print the lineage report (T1-4-1)."""
    lineage = ctx.experiments.lineage(args.experiment_id)
    print(f"{lineage.experiment_id} lineage")
    if lineage.ancestors:
        print("  ancestors (nearest first):")
        for record in lineage.ancestors:
            print(f"    {record.experiment_id} ({record.status.value})")
    else:
        print("  ancestors: none")
    if lineage.descendants:
        print("  descendants (nearest generation first):")
        for record in lineage.descendants:
            print(f"    {record.experiment_id} ({record.status.value})")
    else:
        print("  descendants: none")
    if lineage.dangling_parent:
        print("  warning: parent referenced but not in the registry (dangling lineage)")
    if lineage.cycle:
        print(f"  warning: cycle detected in parent chain: {', '.join(lineage.cycle_ids)}")
    if lineage.cycle or lineage.dangling_parent:
        return 1
    return 0


# -- helpers ------------------------------------------------------------------


def _version_summary(version: Any) -> str:
    return (
        f"  v{version.version}: {version.kind.value}, {version.record_count} records, "
        f"hash {version.content_hash[:12]}..."
    )


def _run_summary(summary: dict[str, Any]) -> str:
    pooled = summary["pooled"]
    deflated = pooled["deflated_sharpe"]
    deflated_text = f"{deflated:.3f}" if deflated is not None else "n/a"
    lines = [
        f"passport {summary['passport_id']} ({summary['symbol']}): "
        f"status={summary['status']}, verdict={summary['verdict']['verdict']}",
        f"  pooled: {pooled['n_folds']} folds, mean_return={pooled['mean_return_pct']:.4f}%, "
        f"excess={pooled['mean_excess_return_pct']:.4f}%, "
        f"positive_folds={pooled['positive_fold_rate']:.2f}, "
        f"beats_buy_and_hold={pooled['beats_buy_and_hold_rate']:.2f}, "
        f"deflated_sharpe={deflated_text}",
        f"  pbo={summary['pbo']['pbo']:.3f}, quality_usable={summary['quality_usable']}",
        f"  evidence report: {summary['report_file']}",
    ]
    return "\n".join(lines)


def _read_klines_csv(path: Path, *, binance: bool = False) -> list[HistoricalBar]:
    """Parse an OHLCV CSV into validated bars.

    With ``--binance`` the file is headerless Binance klines
    (open_time_ms, open, high, low, close, volume, ...). Otherwise the
    first row must be a header naming at least: open_time|timestamp,
    open, high, low, close, volume|base_volume.
    """
    if not path.exists():
        raise OSError(f"no such file: {path}")
    rows: list[list[str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.reader(handle):
            if not row or not row[0].strip():
                continue
            rows.append(row)
    if not rows:
        raise ValueError(f"no rows in {path}")
    if binance:
        return [_bar_from_binance_row(row, index) for index, row in enumerate(rows, start=1)]
    header = [cell.strip().lower() for cell in rows[0]]
    columns = {
        name: index
        for index, name in enumerate(header)
        if name
        in (
            "open_time",
            "timestamp",
            "datetime",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "base_volume",
        )
    }
    for required in ("open_time", "timestamp", "datetime", "date"):
        if required in columns:
            break
    else:
        raise ValueError("header must name a time column (open_time|timestamp|datetime|date)")
    for name in ("open", "high", "low", "close"):
        if name not in columns:
            raise ValueError(f"header must name a {name} column")
    if "volume" not in columns and "base_volume" not in columns:
        raise ValueError("header must name a volume|base_volume column")
    volume_column = columns["volume"] if "volume" in columns else columns["base_volume"]
    time_column = next(
        columns[name] for name in ("open_time", "timestamp", "datetime", "date") if name in columns
    )
    bars: list[HistoricalBar] = []
    for index, row in enumerate(rows[1:], start=1):
        try:
            bars.append(
                HistoricalBar(
                    timestamp=_parse_bar_time(row[time_column]),
                    open=float(row[columns["open"]]),
                    high=float(row[columns["high"]]),
                    low=float(row[columns["low"]]),
                    close=float(row[columns["close"]]),
                    volume=float(row[volume_column]),
                )
            )
        except (ValueError, IndexError) as exc:
            raise ValueError(f"invalid row {index + 1} in {path}: {exc}") from exc
    if not bars:
        raise ValueError(f"no data rows in {path}")
    return bars


def _bar_from_binance_row(row: list[str], index: int) -> HistoricalBar:
    try:
        return HistoricalBar(
            timestamp=datetime.fromtimestamp(float(row[0]) / 1000.0, tz=UTC),
            open=float(row[1]),
            high=float(row[2]),
            low=float(row[3]),
            close=float(row[4]),
            volume=float(row[5]),
        )
    except (ValueError, IndexError) as exc:
        raise ValueError(f"invalid Binance klines row {index}: {exc}") from exc


def _parse_bar_time(value: str) -> datetime:
    value = value.strip()
    try:
        seconds = float(value)
        return datetime.fromtimestamp(seconds, tz=UTC)
    except ValueError:
        pass
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=UTC)
    except ValueError:
        pass
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _synthetic_bars(symbol: str, n: int, seed: int) -> list[HistoricalBar]:
    """Deterministic seeded hourly series (random-walk, realistic OHLCV)."""
    rng = random.Random(seed)
    bars: list[HistoricalBar] = []
    timestamp = datetime(2024, 1, 1, tzinfo=UTC)
    price = 100.0
    for _ in range(n):
        close = price * (1.0 + rng.gauss(0.0003, 0.006))
        open_ = price
        high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, 0.002)))
        low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, 0.002)))
        bars.append(
            HistoricalBar(
                timestamp=timestamp,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=rng.uniform(50.0, 500.0),
            )
        )
        price = close
        timestamp += timedelta(hours=1)
    return bars


if __name__ == "__main__":
    raise SystemExit(main())
