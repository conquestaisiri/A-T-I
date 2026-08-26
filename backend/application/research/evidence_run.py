# backend/application/research/evidence_run.py
"""Evidence run orchestrator (task P5-005): dataset store -> evidence report.

This is the wiring the critique's Tier-1 #8 demands: one command turns a
frozen RAW dataset version into an auditable evidence report. The run:

1. loads the frozen version's records (TEST purpose — the firewall serves
   the locked test set to the experiment that owns it);
2. re-runs the data-quality gate (6-4) on the exact records that were
   frozen, and refuses to continue when the series is unusable;
3. claims the out-of-sample window (the last ``test_size`` bars) as a
   locked test period of the dataset (P5-002) — after this, no training
   load can ever cover it;
4. runs the real decision pipeline out-of-sample over the reconstructed
   events (P1-009 evaluator, shared cost ruler, walk-forward folds);
5. scores the reasoner variant family for PBO/selection bias (P5-001);
6. issues the strategy passport with the conservative verdict (P5-003) and
   writes the append-only operator report file.

Nothing here touches the live path: the run is a research evaluation only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.application.interfaces.dataset_store import DatasetStore
from backend.application.research.data_quality_report import (
    DataQualityReport,
    assess_data_quality,
)
from backend.application.research.dataset_event_adapter import (
    records_to_bars,
    records_to_events,
)
from backend.application.research.decision_pipeline_evaluator import (
    DecisionPipelineEvaluator,
    OutOfSampleReport,
    VariantsReport,
)
from backend.application.research.evidence_engine import EvidenceEngine
from backend.application.research.evidence_report import EvidenceReportWriter
from backend.application.research.regime_oos_evidence import RegimeOosEvidenceBuilder
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.research.dataset import DatasetPurpose, DatasetVersion
from backend.domain.research.historical_bar import HistoricalBar
from backend.domain.research.passport import StrategyPassport


@dataclass(frozen=True, slots=True)
class EvidenceRunConfig:
    """One evidence run's declared scope (everything is explicit)."""

    dataset_id: str
    dataset_version: int
    experiment_id: str
    claimed_by: str
    passport_id: str
    symbol: str
    train_size: int = 100
    test_size: int = 20
    embargo: float = 0.0
    n_trials: int = 1
    starting_equity: float = 100_000.0
    report_dir: str = "reports/evidence"
    expected_interval_seconds: int | None = None
    outlier_z_threshold: float | None = None


@dataclass(frozen=True, slots=True)
class EvidenceRunResult:
    """The complete outcome of one evidence run."""

    passport: StrategyPassport
    report: OutOfSampleReport
    variants: VariantsReport
    quality: DataQualityReport
    dataset_version: DatasetVersion
    locked_test_period: tuple[str, str] | None
    report_path: Path

    def summary(self) -> dict[str, Any]:
        """A one-screen operator summary of the run."""
        return {
            "passport_id": self.passport.passport_id,
            "symbol": self.report.symbol,
            "status": self.passport.status.value,
            "verdict": self.passport.verdict.as_dict(),
            "pooled": {
                "n_folds": self.report.pooled.n_folds,
                "mean_return_pct": self.report.pooled.mean_return_pct,
                "mean_excess_return_pct": self.report.pooled.mean_excess_return_pct,
                "positive_fold_rate": self.report.pooled.positive_fold_rate,
                "beats_buy_and_hold_rate": self.report.pooled.beats_buy_and_hold_rate,
                "deflated_sharpe": self.report.pooled.deflated_sharpe,
                "mean_max_drawdown_pct": self.report.pooled.mean_max_drawdown_pct,
            },
            "pbo": self.variants.pbo.as_dict(),
            "quality_usable": self.quality.is_usable,
            "report_file": str(self.report_path),
        }


class EvidenceRunService:
    """Wire the dataset store, evaluator, evidence engine and report writer.

    Parameters
    ----------
    store: DatasetStore
        The frozen dataset ledger (P1-001).
    engine: EvidenceEngine
        The passport issuer (P5-003).
    writer: EvidenceReportWriter | None
        Append-only operator report archive (P5-003f).
    """

    def __init__(
        self,
        store: DatasetStore,
        engine: EvidenceEngine,
        writer: EvidenceReportWriter | None = None,
    ) -> None:
        self._store = store
        self._engine = engine
        self._writer = writer or EvidenceReportWriter()

    def run(self, config: EvidenceRunConfig) -> EvidenceRunResult:
        """Execute the evidence run and return its full outcome."""
        records = self._store.load_records(
            config.dataset_id,
            config.dataset_version,
            purpose=DatasetPurpose.TEST,
        )
        if not records:
            raise ValueError(
                f"dataset {config.dataset_id} v{config.dataset_version} has no records"
            )
        events = records_to_events(records, expected_symbol=config.symbol)
        bars = records_to_bars(records)
        quality = assess_data_quality(
            bars,
            dataset_id=config.dataset_id,
            expected_interval_seconds=config.expected_interval_seconds,
            outlier_z_threshold=config.outlier_z_threshold or 5.0,
        )
        if not quality.is_usable:
            raise ValueError(
                f"data-quality gate failed for {config.dataset_id} "
                f"v{config.dataset_version}: {'; '.join(quality.issues)}"
            )
        if len(bars) < config.train_size + config.test_size:
            raise ValueError(
                f"series of {len(bars)} bars is too short for "
                f"train_size={config.train_size} + test_size={config.test_size}"
            )

        locked = self._lock_out_of_sample_window(bars, config)

        evaluator = DecisionPipelineEvaluator(
            cv=WalkForwardCV(
                train_size=config.train_size,
                test_size=config.test_size,
                embargo=config.embargo,
            ),
            starting_equity=config.starting_equity,
            n_trials=config.n_trials,
        )
        report = evaluator.evaluate(events)
        variants = evaluator.evaluate_variants(events, _solver_variant_factories())
        regime_evidence = RegimeOosEvidenceBuilder().build(report, [bar.close for bar in bars])

        passport = self._engine.issue_passport(
            passport_id=config.passport_id,
            hypothesis=(
                f"rule-based momentum/volatility pipeline on {config.symbol} "
                f"({config.dataset_id} v{config.dataset_version})"
            ),
            dataset_id=config.dataset_id,
            dataset_version=config.dataset_version,
            features=("trend", "momentum", "volatility", "volume", "liquidity"),
            model=report.pooled.reasoner,
            trial_count=config.n_trials,
            report=report,
            pbo=variants.pbo,
            regime_evidence=regime_evidence.as_dict(),
            experiment_id=config.experiment_id,
            train_period=_period(bars[: -config.test_size] if config.test_size else bars),
            validation_period=None,
            test_period=locked,
        )
        path = self._writer.write(
            passport,
            self._engine.lifecycle(passport.passport_id),
            extra={
                "run": {
                    "dataset_id": config.dataset_id,
                    "dataset_version": config.dataset_version,
                    "experiment_id": config.experiment_id,
                    "claimed_by": config.claimed_by,
                },
                "quality": quality.as_dict(),
                "out_of_sample_report": report.as_dict(),
                "variants_report": variants.as_dict(),
            },
        )
        return EvidenceRunResult(
            passport=passport,
            report=report,
            variants=variants,
            quality=quality,
            dataset_version=self._require_version(config.dataset_id, config.dataset_version),
            locked_test_period=locked,
            report_path=path,
        )

    def _lock_out_of_sample_window(
        self,
        bars: list[HistoricalBar],
        config: EvidenceRunConfig,
    ) -> tuple[str, str] | None:
        """Claim the final ``test_size`` bars as the locked test period.

        The claim is recorded once per dataset; a pre-existing lock for the
        same window is reused (it was already claimed). Returns the ISO-8601
        [start, end] window or None when the series is too short.
        """
        if len(bars) <= config.test_size:
            return None
        window_bars = bars[-config.test_size :]
        start = window_bars[0].timestamp
        end = window_bars[-1].timestamp
        for lock in self._store.list_test_locks(config.dataset_id):
            if lock.start == start and lock.end == end:
                return (
                    start.isoformat(timespec="milliseconds"),
                    end.isoformat(timespec="milliseconds"),
                )
        self._store.lock_test_period(
            dataset_id=config.dataset_id,
            start=start,
            end=end,
            experiment_id=config.experiment_id,
            claimed_by=config.claimed_by,
            claimed_at=datetime.now(UTC),
        )
        return (start.isoformat(timespec="milliseconds"), end.isoformat(timespec="milliseconds"))

    def _require_version(self, dataset_id: str, version: int) -> DatasetVersion:
        for candidate in self._store.list_versions(dataset_id):
            if candidate.version == version:
                return candidate
        raise ValueError(f"dataset {dataset_id} v{version} not found")


def _period(bars: list[HistoricalBar]) -> tuple[str, str] | None:
    if not bars:
        return None
    return (
        bars[0].timestamp.isoformat(timespec="milliseconds"),
        bars[-1].timestamp.isoformat(timespec="milliseconds"),
    )


def _solver_variant_factories() -> dict[str, Any]:
    """The reasoner variant family used for the PBO check.

    Three configurations of the deterministic rule-based solver: the
    default house configuration plus a conservative and an aggressive
    threshold setting. The PBO family then answers whether picking the best
    of these on past folds survives on future folds.
    """
    from collections.abc import Callable, Sequence

    from backend.application.backtest.report import ReplayStep
    from backend.application.decision.rule_based_solver import RuleBasedSolver, SolverConfig
    from backend.application.interfaces.ai_reasoner import AIReasoner

    def _factory(**overrides: Any) -> Callable[[Sequence[ReplayStep], Sequence[float]], AIReasoner]:
        def _build(train_steps: Sequence[ReplayStep], train_prices: Sequence[float]) -> AIReasoner:
            return RuleBasedSolver(SolverConfig(**overrides))

        return _build

    return {
        "default": _factory(),
        "conservative": _factory(
            momentum_entry_pct=0.10,
            risk_per_trade_pct=0.01,
            risk_reward_ratio=3.0,
        ),
        "aggressive": _factory(
            momentum_entry_pct=0.02,
            risk_per_trade_pct=0.03,
            risk_reward_ratio=1.5,
        ),
    }
