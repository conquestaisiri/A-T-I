# backend/application/research/reasoner_ablation.py
"""AI reasoner incremental-contribution quantification (task P5-007).

The Strategic Review's standing demand (priority 3): *measure the AI
reasoner's incremental contribution against non-AI baselines; keep only what
measurably improves.* This harness is that measurement. It runs a family of
reasoner variants — rules-only, quant-only, and (operator-supplied) AI-only /
quant+AI — on **identical** out-of-sample folds via the evaluator's
reasoner-factory seam, then compares every variant against the baseline and
verdicts its contribution.

What the verdicts mean
----------------------
- IMPROVES: the variant beats the baseline on mean excess return, does not
  lose on positive-fold rate, wins a majority of folds pairwise, and its
  Deflated Sharpe is higher than the baseline's (both must be estimable —
  without a Deflated Sharpe, an improvement claim is refused, mirroring the
  evidence engine's OBSERVE stance).
- DEGRADES: the variant clearly loses on mean excess return and loses a
  majority of folds pairwise.
- INCONCLUSIVE: mixed evidence (or a missing Deflated Sharpe) — no claim.

Nothing here promotes anything: a contribution verdict is evidence, not a
status change. Promotion still runs through the evidence engine (P5-003);
the report's ``keep`` list only records which variants *measured* positive
contribution on this evaluation.

Which variants ship by default
------------------------------
The default family is deterministic and testable today: ``rules_only``
(``RuleBasedSolver``, the production baseline) and ``quant_only``
(``QuantMomentumScorer``, the pure-signal cell). The AI cells — ``ai_only``
(an LLM reasoner over the raw context) and ``quant_plus_ai`` (an LLM reasoner
with quant signals injected) — require a live model endpoint and are
operator-supplied factories; they plug into the exact same mapping. Running
an ablation without them simply does not claim anything about AI.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.decision_pipeline_evaluator import (
    DecisionPipelineEvaluator,
    OutOfSampleReport,
    ReasonerFactory,
    VariantEvidence,
)
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.observation.event import ObservationEvent
from backend.domain.research.pbo import PboResult


class ContributionVerdict(StrEnum):
    """One variant's measured contribution over the baseline."""

    IMPROVES = "improves"
    DEGRADES = "degrades"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class AblationVariantResult:
    """One variant's contribution evidence on the shared folds.

    Attributes
    ----------
    name: str
        Variant name (key of the factory mapping).
    report: OutOfSampleReport
        The variant's full out-of-sample report on identical folds.
    fold_returns: tuple[float, ...]
        Per-fold total returns, aligned with every other variant.
    delta_mean_excess_pct: float
        Variant mean excess return minus the baseline's (percent).
    delta_positive_fold_rate: float
        Variant positive-fold rate minus the baseline's.
    delta_deflated_sharpe: float | None
        Variant DSR minus the baseline's; None when either is inestimable.
    paired_beat_rate: float
        Fraction of folds where the variant beat the baseline.
    verdict: ContributionVerdict
        The honest contribution verdict.
    reasons: tuple[str, ...]
        The numbers that produced the verdict (audit trail).
    """

    name: str
    report: OutOfSampleReport
    fold_returns: tuple[float, ...]
    delta_mean_excess_pct: float
    delta_positive_fold_rate: float
    delta_deflated_sharpe: float | None
    paired_beat_rate: float
    verdict: ContributionVerdict
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Serialise the variant result to a plain dictionary."""
        return {
            "name": self.name,
            "fold_returns": list(self.fold_returns),
            "delta_mean_excess_pct": round(self.delta_mean_excess_pct, 6),
            "delta_positive_fold_rate": round(self.delta_positive_fold_rate, 6),
            "delta_deflated_sharpe": (
                round(self.delta_deflated_sharpe, 6)
                if self.delta_deflated_sharpe is not None
                else None
            ),
            "paired_beat_rate": round(self.paired_beat_rate, 6),
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class AblationReport:
    """The full ablation: every variant's contribution over the baseline.

    Attributes
    ----------
    symbol: str
        Symbol evaluated.
    cv_spec: dict[str, object]
        The walk-forward configuration shared by every variant.
    baseline_name: str
        The variant every other variant is measured against.
    variants: tuple[AblationVariantResult, ...]
        All measured variants including the baseline (zero deltas), in
        mapping order.
    pbo: PboResult
        Probability of backtest overfitting across the whole family
        (P5-001): whether picking the best variant on past folds survives
        on future folds.
    keep: tuple[str, ...]
        Variants that measured IMPROVES — the only ones worth keeping.
    """

    symbol: str
    cv_spec: dict[str, object]
    baseline_name: str
    variants: tuple[AblationVariantResult, ...]
    pbo: PboResult
    keep: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Serialise the ablation report to a plain dictionary."""
        return {
            "symbol": self.symbol,
            "cv_spec": dict(self.cv_spec),
            "baseline_name": self.baseline_name,
            "variants": [v.as_dict() for v in self.variants],
            "pbo": self.pbo.as_dict(),
            "keep": list(self.keep),
        }


def default_ablation_factories() -> dict[str, ReasonerFactory]:
    """The deterministic ablation family shipped with the harness.

    ``rules_only`` is the production baseline (``RuleBasedSolver``);
    ``quant_only`` is the pure momentum scorer. The AI cells (``ai_only``,
    ``quant_plus_ai``) are operator-supplied factories — they require a live
    model endpoint and are not fabricated here.
    """

    from backend.application.backtest.report import ReplayStep
    from backend.application.decision.quant_momentum_scorer import QuantMomentumScorer
    from backend.application.decision.rule_based_solver import RuleBasedSolver
    from backend.application.interfaces.ai_reasoner import AIReasoner

    def _rules(train_steps: Sequence[ReplayStep], train_prices: Sequence[float]) -> AIReasoner:
        return RuleBasedSolver()

    def _quant(train_steps: Sequence[ReplayStep], train_prices: Sequence[float]) -> AIReasoner:
        return QuantMomentumScorer()

    return {"rules_only": _rules, "quant_only": _quant}


class ReasonerAblation:
    """Run a reasoner family on identical folds and measure contributions.

    Parameters
    ----------
    costs: EvaluationCosts | None
        Shared cost ruler (defaults to ``EvaluationCosts.realistic()``).
    cv: WalkForwardCV | None
        Walk-forward splitter shared by every variant.
    starting_equity: float
        Fresh equity per fold per variant.
    n_trials: int
        Multiple-testing deflation for the Deflated Sharpe (P5-001).
    paired_beat_threshold: float
        Minimum pairwise beat rate required for an IMPROVES verdict.
    """

    def __init__(
        self,
        *,
        costs: EvaluationCosts | None = None,
        cv: WalkForwardCV | None = None,
        starting_equity: float = 100_000.0,
        n_trials: int = 1,
        paired_beat_threshold: float = 0.5,
    ) -> None:
        self._costs = costs or EvaluationCosts.realistic()
        self._cv = cv
        self._starting_equity = starting_equity
        self._n_trials = n_trials
        if not 0.0 < paired_beat_threshold <= 1.0:
            raise ValueError("paired_beat_threshold must be in (0, 1]")
        self._paired_beat_threshold = paired_beat_threshold

    def run(
        self,
        events: Sequence[ObservationEvent],
        *,
        variants: Mapping[str, ReasonerFactory],
        baseline_name: str,
    ) -> AblationReport:
        """Measure every variant's contribution over ``baseline_name``."""
        factories = dict(variants)
        if baseline_name not in factories:
            raise ValueError(f"baseline variant {baseline_name!r} not in the variant family")
        if len(factories) < 2:
            raise ValueError("ablation requires a baseline plus at least one variant")

        evaluator = DecisionPipelineEvaluator(
            costs=self._costs,
            cv=self._cv,
            starting_equity=self._starting_equity,
            n_trials=self._n_trials,
        )
        family = evaluator.evaluate_variants(events, factories)

        baseline = next(v for v in family.variants if v.name == baseline_name)
        results: list[AblationVariantResult] = []
        for variant in family.variants:
            if variant.name == baseline_name:
                results.append(
                    AblationVariantResult(
                        name=variant.name,
                        report=variant.report,
                        fold_returns=variant.fold_returns,
                        delta_mean_excess_pct=0.0,
                        delta_positive_fold_rate=0.0,
                        delta_deflated_sharpe=0.0,
                        paired_beat_rate=0.5,
                        verdict=ContributionVerdict.INCONCLUSIVE,
                        reasons=("baseline reference (not measured against itself)",),
                    )
                )
                continue
            results.append(
                self._contribution(variant.name, variant.report, variant.fold_returns, baseline)
            )

        return AblationReport(
            symbol=family.symbol,
            cv_spec=family.variants[0].report.cv_spec,
            baseline_name=baseline_name,
            variants=tuple(results),
            pbo=family.pbo,
            keep=tuple(v.name for v in results if v.verdict is ContributionVerdict.IMPROVES),
        )

    def _contribution(
        self,
        name: str,
        report: OutOfSampleReport,
        fold_returns: Sequence[float],
        baseline: VariantEvidence,
    ) -> AblationVariantResult:
        """Verdict one variant's contribution over the baseline evidence."""
        baseline_pooled = baseline.report.pooled
        pooled = report.pooled
        delta_mean_excess = pooled.mean_excess_return_pct - baseline_pooled.mean_excess_return_pct
        delta_fold_rate = pooled.positive_fold_rate - baseline_pooled.positive_fold_rate
        delta_dsr = _dsr_delta(pooled.deflated_sharpe, baseline_pooled.deflated_sharpe)
        beats = sum(
            1.0
            for mine, theirs in zip(fold_returns, baseline.fold_returns, strict=True)
            if mine > theirs
        )
        paired_beat_rate = beats / len(fold_returns) if fold_returns else 0.0

        reasons: list[str] = []
        if delta_dsr is None:
            reasons.append(
                "deflated Sharpe unavailable (too few folds for DSR): "
                "an improvement claim is refused without it"
            )
        else:
            reasons.append(
                f"delta deflated Sharpe {delta_dsr:+.4f} "
                f"(variant {pooled.deflated_sharpe:.4f} vs baseline "
                f"{baseline_pooled.deflated_sharpe:.4f})"
            )
        reasons.append(f"delta mean excess {delta_mean_excess:+.4f}%")
        reasons.append(f"delta positive-fold rate {delta_fold_rate:+.4f}")
        reasons.append(f"paired beat rate {paired_beat_rate:.3f}")

        improves = (
            delta_mean_excess > 0.0
            and delta_fold_rate >= 0.0
            and paired_beat_rate >= self._paired_beat_threshold
            and delta_dsr is not None
            and delta_dsr > 0.0
        )
        degrades = delta_mean_excess < 0.0 and paired_beat_rate < self._paired_beat_threshold
        if improves:
            verdict = ContributionVerdict.IMPROVES
        elif degrades:
            verdict = ContributionVerdict.DEGRADES
        else:
            verdict = ContributionVerdict.INCONCLUSIVE
            reasons.append("evidence is mixed: no improvement claim, no kill")

        return AblationVariantResult(
            name=name,
            report=report,
            fold_returns=tuple(fold_returns),
            delta_mean_excess_pct=round(delta_mean_excess, 6),
            delta_positive_fold_rate=round(delta_fold_rate, 6),
            delta_deflated_sharpe=round(delta_dsr, 6) if delta_dsr is not None else None,
            paired_beat_rate=round(paired_beat_rate, 6),
            verdict=verdict,
            reasons=tuple(reasons),
        )


def _dsr_delta(variant: float | None, baseline: float | None) -> float | None:
    """Variant DSR minus baseline DSR; None when either is inestimable."""
    if variant is None or baseline is None:
        return None
    return variant - baseline
