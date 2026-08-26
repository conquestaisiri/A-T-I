# backend/application/research/decision_pipeline_evaluator.py
"""Out-of-sample evaluation of the real decision pipeline (evidence priority 1).

What this is
------------
The critique (docs/ATI_Architecture_Critique.md) names one thing to attack
relentlessly: *"show me the evidence that this machine can make money without
fooling itself."* This evaluator is that evidence harness. It runs the **real
decision pipeline** (``DecisionPipelineService`` → risk gate → paper simulator,
the exact path ``main.py`` wires for live paper trading) over historical
observation events, but only ever *scores* the pipeline on test windows it was
never given the chance to influence.

How out-of-sample is enforced
-----------------------------
- A ``WalkForwardCV`` split (expanding window, past-to-future only) partitions
  the bar index space into train/test folds. Test windows always come strictly
  after their training prefix (never the reverse).
- Each fold runs a **fresh** ``BacktestRunner`` (fresh pipeline + simulator +
  equity) over only the fold's test steps, so no fold's result can leak into
  another fold's equity or state (ADR 0007 replay determinism).
- Every fold's pipeline performance is compared against the **shared costed
  baselines** (P1-003) on the same test prices and the same cost ruler, so the
  pipeline is never graded against a free reference.
- ``PooledEvidence`` (domain/research/oos_evaluation.py) aggregates honestly:
  means/medians of per-fold returns, positive-fold rate, beats-buy-and-hold
  rate, and total fees/slippage/profit-factor/expectancy. Nothing is claimed
  on in-sample numbers because nothing in-sample is reported.

Why a fresh reasoner per fold
-----------------------------
A learned reasoner must be fitted on the fold's training window only and then
scored on the fold's test window. ``reasoner_factory`` receives the training
steps (and the training prices) and returns a fresh reasoner; the default
returns the deterministic ``RuleBasedSolver`` (stateless, so the factory seam
exists for when a learnable reasoner is added). This makes the harness the
single honest seam where AI contribution (critique priority 3) will be
measured: rules-only vs AI-only vs rules+AI, all on identical OOS folds.
"""

from __future__ import annotations

import logging
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np

from backend.application.backtest.report import BacktestReport, ReplayStep
from backend.application.context.bootstrap import (
    build_backtest_runner,
    build_replay_steps,
)
from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.research.baseline_evaluation import (
    BaselineEvaluator,
    BaselineStrategy,
    BuyAndHoldBaseline,
    EvaluationCosts,
    MomentumBaseline,
    MovingAverageCrossoverBaseline,
)
from backend.application.simulation.paper_fill_engine import PaperFeeConfig
from backend.application.validation.purged_cv import (
    CombinatorialPurgedCV,
    PurgedKFold,
    WalkForwardCV,
)
from backend.domain.observation.event import ObservationEvent
from backend.domain.research.evaluation import BaselineResult
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.pbo import PboResult, compute_deflated_sharpe, compute_pbo

logger = logging.getLogger(__name__)

ReasonerFactory = Callable[[Sequence[ReplayStep], Sequence[float]], AIReasoner]


def default_reasoner_factory(
    train_steps: Sequence[ReplayStep], train_prices: Sequence[float]
) -> AIReasoner:
    """Return the deterministic rule-based solver (stateless, ADR 0009)."""
    from backend.application.decision.rule_based_solver import RuleBasedSolver, SolverConfig

    return RuleBasedSolver(SolverConfig())


@dataclass(frozen=True, slots=True)
class FoldOutcome:
    """One out-of-sample fold: the pipeline's report plus its baselines.

    Attributes
    ----------
    fold: int
        Zero-based fold index.
    train_range: tuple[int, int]
        Half-open [start, end) bar range used for training (context warm-up).
    test_range: tuple[int, int]
        Half-open [start, end) bar range scored out-of-sample.
    report: BacktestReport
        The real pipeline's report over the test window (fresh runner).
    baselines: tuple[BaselineResult, ...]
        Costed baselines scored on the same test prices and same costs,
        highest-excess-first (includes the costed buy-and-hold reference).
    """

    fold: int
    train_range: tuple[int, int]
    test_range: tuple[int, int]
    report: BacktestReport
    baselines: tuple[BaselineResult, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialise the fold outcome (report + baselines) to a dictionary."""
        return {
            "fold": self.fold,
            "train_range": list(self.train_range),
            "test_range": list(self.test_range),
            "report": _report_as_dict(self.report),
            "baselines": [b.as_dict() for b in self.baselines],
        }


@dataclass(frozen=True, slots=True)
class OutOfSampleReport:
    """The complete out-of-sample evidence report.

    Attributes
    ----------
    symbol: str
        Symbol evaluated.
    costs: EvaluationCosts
        The shared cost ruler applied to pipeline and baselines.
    cv_spec: dict[str, object]
        The walk-forward configuration used (train_size/test_size/expanding/...).
    folds: tuple[FoldOutcome, ...]
        Per-fold evidence, in fold order.
    pooled: PooledEvidence
        The honest aggregate scorecard across all folds.
    """

    symbol: str
    costs: EvaluationCosts
    cv_spec: dict[str, object]
    folds: tuple[FoldOutcome, ...]
    pooled: PooledEvidence

    def as_dict(self) -> dict[str, object]:
        """Serialise the full report to a plain dictionary."""
        return {
            "symbol": self.symbol,
            "costs": {
                "half_spread_pct": self.costs.half_spread_pct,
                "taker_fee_pct": self.costs.taker_fee_pct,
            },
            "cv_spec": dict(self.cv_spec),
            "folds": [f.as_dict() for f in self.folds],
            "pooled": self.pooled.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class VariantEvidence:
    """One reasoner variant's full OOS report on the shared folds.

    Attributes
    ----------
    name: str
        Variant name (key of the factory mapping).
    report: OutOfSampleReport
        The variant's complete out-of-sample report on identical folds.
    fold_returns: tuple[float, ...]
        Per-fold total returns (percent), aligned with every other variant.
    """

    name: str
    report: OutOfSampleReport
    fold_returns: tuple[float, ...]

    def as_dict(self) -> dict[str, object]:
        """Serialise the variant evidence to a plain dictionary."""
        return {
            "name": self.name,
            "fold_returns": list(self.fold_returns),
            "report": self.report.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class VariantsReport:
    """PBO comparison of reasoner variants on identical out-of-sample folds.

    Attributes
    ----------
    symbol: str
        Symbol evaluated.
    variants: tuple[VariantEvidence, ...]
        Per-variant evidence, in mapping order.
    pbo: PboResult
        Probability of Backtest Overfitting across the variant family
        (P5-001): whether choosing the best variant on past folds survives
        on future folds.
    """

    symbol: str
    variants: tuple[VariantEvidence, ...]
    pbo: PboResult

    def as_dict(self) -> dict[str, object]:
        """Serialise the variants report to a plain dictionary."""
        return {
            "symbol": self.symbol,
            "variants": [v.as_dict() for v in self.variants],
            "pbo": self.pbo.as_dict(),
        }


class DecisionPipelineEvaluator:
    """Score the real decision pipeline out-of-sample on historical events.

    Parameters
    ----------
    costs: EvaluationCosts | None
        Shared cost ruler (defaults to ``EvaluationCosts.realistic()``).
    cv: WalkForwardCV | None
        Walk-forward splitter (defaults to an expanding 100-train / 20-test
        window). Must be past-to-future; other splitters are rejected.
    baselines: Sequence[BaselineStrategy] | None
        Baselines to compare against (default: momentum, MA crossover).
        The costed buy-and-hold reference is always included.
    starting_equity: float
        Fresh equity given to each fold's runner.
    reasoner_factory: ReasonerFactory | None
        Called per fold with (train_steps, train_prices); must return a fresh
        reasoner. Defaults to the deterministic ``RuleBasedSolver``.
    pipeline_fee_config: PaperFeeConfig | None
        Execution costs charged to the real pipeline's paper fills. Defaults
        to a taker rate matching ``costs.taker_fee_pct`` and impact matching
        ``costs.half_spread_pct`` so the pipeline is graded under the same
        cost ruler as the baselines instead of a fee-free special case.
    n_trials: int
        Number of strategies/experiments that competed to produce this
        candidate (P5-001 multiple-testing deflation). 1 = no deflation;
        the pooled Deflated Sharpe is computed when at least 4 folds exist.
    """

    def __init__(
        self,
        *,
        costs: EvaluationCosts | None = None,
        cv: WalkForwardCV | None = None,
        baselines: Sequence[BaselineStrategy] | None = None,
        starting_equity: float = 100_000.0,
        reasoner_factory: ReasonerFactory | None = None,
        pipeline_fee_config: PaperFeeConfig | None = None,
        n_trials: int = 1,
    ) -> None:
        self._costs = costs or EvaluationCosts.realistic()
        self._cv = cv or WalkForwardCV(train_size=100, test_size=20)
        if not isinstance(self._cv, WalkForwardCV):
            raise ValueError("only past-to-future WalkForwardCV splitting is supported")
        self._baselines: list[BaselineStrategy] = (
            list(baselines)
            if baselines is not None
            else [MomentumBaseline(lookback=10), MovingAverageCrossoverBaseline(fast=5, slow=20)]
        )
        if self._starting_equity_is_invalid(starting_equity):
            raise ValueError("starting_equity must be positive")
        self._starting_equity = starting_equity
        self._reasoner_factory = reasoner_factory or default_reasoner_factory
        self._pipeline_fee_config = pipeline_fee_config or PaperFeeConfig(
            taker_fee_rate=self._costs.taker_fee_pct,
            impact_bps=self._costs.half_spread_pct * 10_000.0,
        )
        if n_trials < 1:
            raise ValueError("n_trials must be >= 1")
        self._n_trials = n_trials

    @staticmethod
    def _starting_equity_is_invalid(value: float) -> bool:
        return not isinstance(value, (int, float)) or value <= 0.0

    def evaluate(self, events: Sequence[ObservationEvent]) -> OutOfSampleReport:
        """Run the out-of-sample evaluation over ``events``.

        Events must be one symbol's history (chronological). The full series is
        replayed once through the real context builder to warm up features;
        each fold then scores only its own test steps with a fresh runner.
        """
        events = list(events)
        if not events:
            raise ValueError("evaluation requires at least one observation event")
        steps, symbol = build_replay_steps(events)
        prices = _prices(events, symbol)

        indices = np.arange(len(steps))
        folds_cv = self._cv.split(indices)

        fold_outcomes: list[FoldOutcome] = []
        for fold_index, (train_idx, test_idx) in enumerate(folds_cv):
            train_steps = [steps[i] for i in train_idx]
            train_prices = [prices[i] for i in train_idx]
            test_steps = [steps[i] for i in test_idx]
            test_prices = [prices[i] for i in test_idx]

            reasoner = self._reasoner_factory(train_steps, train_prices)
            runner = build_backtest_runner(
                ":memory:",
                symbol=symbol,
                starting_equity=self._starting_equity,
                reasoner=reasoner,
                fee_config=self._pipeline_fee_config,
            )
            report = runner.run(test_steps)

            baselines = self._score_baselines(test_prices)
            fold_outcomes.append(
                FoldOutcome(
                    fold=fold_index,
                    train_range=(int(train_idx[0]), int(train_idx[-1]) + 1),
                    test_range=(int(test_idx[0]), int(test_idx[-1]) + 1),
                    report=report,
                    baselines=baselines,
                )
            )

        if not fold_outcomes:
            raise ValueError(
                "walk-forward produced no folds; increase the series length "
                "relative to train_size + test_size"
            )

        pooled = self._pool(fold_outcomes)
        return OutOfSampleReport(
            symbol=symbol,
            costs=self._costs,
            cv_spec=_cv_spec(self._cv),
            folds=tuple(fold_outcomes),
            pooled=pooled,
        )

    def _score_baselines(self, test_prices: Sequence[float]) -> tuple[BaselineResult, ...]:
        """Score the configured baselines + costed buy-and-hold on ``test_prices``."""
        evaluator = BaselineEvaluator(self._costs)
        results: list[BaselineResult] = []
        seen: set[str] = set()
        for strategy in [*self._baselines, BuyAndHoldBaseline()]:
            if strategy.name in seen:
                continue
            seen.add(strategy.name)
            results.append(
                evaluator.evaluate(
                    strategy=strategy,
                    prices=test_prices,
                    starting_equity=self._starting_equity,
                )
            )
        results.sort(key=lambda r: r.excess_return_pct, reverse=True)
        return tuple(results)

    def _pool(self, folds: Sequence[FoldOutcome]) -> PooledEvidence:
        """Aggregate per-fold evidence into the honest scorecard."""
        returns = [f.report.returns_pct for f in folds]
        excess: list[float] = []
        for fold in folds:
            reference = next((b for b in fold.baselines if b.name == "buy_and_hold"), None)
            if reference is not None:
                excess.append(fold.report.returns_pct - reference.total_return_pct)
        total_test_bars = sum(f.report.steps for f in folds)
        total_trades = sum(f.report.trades_closed for f in folds)
        total_wins = sum(f.report.wins for f in folds)
        total_losses = sum(f.report.losses for f in folds)
        total_fees = sum(f.report.total_fees for f in folds)
        total_slippage = sum(f.report.total_slippage_bps for f in folds)
        gross_profit = sum(f.report.gross_profit for f in folds)
        gross_loss = sum(f.report.gross_loss for f in folds)

        mean_return = _mean(returns)
        median_return = _median(returns)
        mean_excess = _mean(excess) if excess else 0.0
        positive_fold_rate = sum(1.0 for r in returns if r > 0.0) / len(returns) if returns else 0.0
        beats_bh_rate = sum(1.0 for e in excess if e > 0.0) / len(excess) if excess else 0.0
        mean_dd = _mean([f.report.max_drawdown_pct for f in folds])
        deflated_sharpe = self._compute_deflated_sharpe(returns)

        return PooledEvidence(
            n_folds=len(folds),
            total_test_bars=total_test_bars,
            total_trades=total_trades,
            total_wins=total_wins,
            total_losses=total_losses,
            total_fees=round(total_fees, 6),
            total_slippage_bps=round(total_slippage, 6),
            gross_profit=round(gross_profit, 6),
            gross_loss=round(gross_loss, 6),
            mean_return_pct=round(mean_return, 6),
            median_return_pct=round(median_return, 6),
            mean_excess_return_pct=round(mean_excess, 6),
            positive_fold_rate=round(positive_fold_rate, 6),
            beats_buy_and_hold_rate=round(beats_bh_rate, 6),
            mean_max_drawdown_pct=round(mean_dd, 6),
            deflated_sharpe=deflated_sharpe,
            reasoner=self._reasoner_name(),
            cost_model={
                "half_spread_pct": self._costs.half_spread_pct,
                "taker_fee_pct": self._costs.taker_fee_pct,
            },
        )

    def _compute_deflated_sharpe(self, returns: Sequence[float]) -> float | None:
        """Deflated Sharpe of the pooled fold returns, None when inestimable.

        Requires at least 4 folds and non-degenerate fold returns (the DSR
        math itself rejects fewer observations or zero variance).
        """
        if len(returns) < 4:
            return None
        try:
            dsr = compute_deflated_sharpe(returns, n_trials=self._n_trials)
        except ValueError:
            return None
        return round(dsr.dsr, 6)

    def evaluate_variants(
        self,
        events: Sequence[ObservationEvent],
        reasoner_factories: Mapping[str, ReasonerFactory],
        *,
        n_select_fraction: float = 0.5,
        n_splits: int = 100,
        seed: int | None = 42,
        metric: str = "mean",
    ) -> VariantsReport:
        """Score several reasoner variants on identical OOS folds + PBO.

        Every variant runs the exact same walk-forward splits (same folds,
        same costs, same baselines), producing one per-fold return per
        variant. The PBO family is then computed across variants: in-sample
        (first half of folds) vs out-of-sample (second half) ranking, so the
        report answers "does picking the best reasoner on past folds survive
        on future folds?" — the honest selection-bias question the critique
        demands before any reasoner is promoted.
        """
        if len(reasoner_factories) < 2:
            raise ValueError("at least two reasoner variants are required for PBO")
        reports: list[tuple[str, OutOfSampleReport]] = []
        for name, factory in reasoner_factories.items():
            evaluator = self._clone(factory)
            reports.append((name, evaluator.evaluate(events)))
        fold_returns = {
            name: tuple(fold.report.returns_pct for fold in report.folds)
            for name, report in reports
        }
        sizes = {len(returns) for returns in fold_returns.values()}
        if len(sizes) != 1:
            raise ValueError("all variants must produce the same number of folds")
        matrix = [list(fold_returns[name]) for name, _ in reports]
        pbo = compute_pbo(
            matrix,
            n_select_fraction=n_select_fraction,
            n_splits=n_splits,
            seed=seed,
            metric=metric,
        )
        return VariantsReport(
            symbol=reports[0][1].symbol,
            variants=tuple(
                VariantEvidence(name=name, report=report, fold_returns=fold_returns[name])
                for name, report in reports
            ),
            pbo=pbo,
        )

    def _clone(self, reasoner_factory: ReasonerFactory) -> DecisionPipelineEvaluator:
        """A copy of this evaluator with a different reasoner factory."""
        return DecisionPipelineEvaluator(
            costs=self._costs,
            cv=self._cv,
            baselines=self._baselines,
            starting_equity=self._starting_equity,
            reasoner_factory=reasoner_factory,
            pipeline_fee_config=self._pipeline_fee_config,
            n_trials=self._n_trials,
        )

    def _reasoner_name(self) -> str:
        """Best-effort reasoner name for the pooled report."""
        try:
            probe = self._reasoner_factory([], [])
            return type(probe).__name__
        except Exception:  # noqa: BLE001
            return "reasoner"


def _prices(events: Sequence[ObservationEvent], symbol: str) -> list[float]:
    """Extract the price series from trade events, validating chronology."""
    prices: list[float] = []
    last_ts = None
    for event in events:
        if event.payload.get("symbol") != symbol:
            raise ValueError(f"event symbol {event.payload.get('symbol')!r} != {symbol!r}")
        price = event.payload.get("price")
        if not isinstance(price, (int, float)) or price <= 0.0:
            raise ValueError(f"event for {symbol} missing a positive numeric 'price'")
        if last_ts is not None and event.timestamp < last_ts:
            raise ValueError("events must be chronological (timestamp must not go backwards)")
        last_ts = event.timestamp
        prices.append(float(price))
    return prices


def _cv_spec(
    cv: WalkForwardCV | PurgedKFold | CombinatorialPurgedCV,
) -> dict[str, object]:
    """Serialize the splitter's settings for the report/passport (T1-6-1).

    Every splitter exposes ``as_dict()`` so the evidence report carries the
    exact gap/embargo settings that were applied — the report is the proof of
    how validation was run, including what leakage protection was in force.
    """
    return dict(cv.as_dict())


def _mean(values: Sequence[float]) -> float:
    return statistics.fmean(values) if values else 0.0


def _median(values: Sequence[float]) -> float:
    return statistics.median(values) if values else 0.0


def _report_as_dict(report: BacktestReport) -> dict[str, object]:
    return {
        "symbol": report.symbol,
        "steps": report.steps,
        "starting_equity": report.starting_equity,
        "final_equity": report.final_equity,
        "total_pnl": report.total_pnl,
        "returns_pct": report.returns_pct,
        "max_drawdown_pct": report.max_drawdown_pct,
        "trades_opened": report.trades_opened,
        "trades_closed": report.trades_closed,
        "wins": report.wins,
        "losses": report.losses,
        "flats": report.flats,
        "approved": report.approved,
        "rejected": report.rejected,
        "win_rate": report.win_rate,
        "profit_factor": report.profit_factor,
        "net_expectancy": report.net_expectancy,
        "total_fees": report.total_fees,
        "total_slippage_bps": report.total_slippage_bps,
        "gross_profit": report.gross_profit,
        "gross_loss": report.gross_loss,
    }
