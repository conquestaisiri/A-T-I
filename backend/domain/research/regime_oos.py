# backend/domain/research/regime_oos.py
"""Regime-conditioned out-of-sample evidence contracts (task T2-11-1).

A candidate's out-of-sample report says *how* it did across folds; this
module says *where* it did it inside the market's regimes. Every fold of a
``DecisionPipelineEvaluator`` report is attributed to the regime that
dominated its test window (labels come from a timestamp-correct, causal
classifier), and the fold's net excess return is aggregated by regime.

Honesty invariants
------------------
- The regime labels obey ``regime_evaluation``'s causality contract: a bar's
  label depends only on prices up to that bar, never on the future.
- Fold returns are the evaluator's own realised numbers (per-fold total
  return and excess over the shared costed buy-and-hold baseline). Nothing is
  re-simulated, so the regime breakdown can never show a win the OOS run did
  not earn.
- A fold whose test window is dominated by the classifier's warm-up tag, or
  that has no buy-and-hold reference, is *not assessed*: it never enters the
  per-regime summaries or the robustness score.
- The regime robustness score is advisory evidence on the passport; it does
  not change the evidence verdict, which stays under
  ``verdict_for_evidence``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class RegimeFoldAllocation:
    """One OOS fold attributed to the regime dominating its test window.

    Attributes
    ----------
    fold: int
        Zero-based fold index (matches the evaluator's fold order).
    test_range: tuple[int, int]
        Half-open [start, end) bar range of the fold's test window.
    bars: int
        Length of the test window (``test_range[1] - test_range[0]``).
    dominant_regime: str
        The regime tag with the most test-window bars.
    regime_shares: Mapping[str, float]
        Per-regime share of the test-window bars, in percent, sorted by tag.
    excess_return_pct: float
        The fold's realised total return minus the shared buy-and-hold
        return over the same window (the evaluator's own numbers). 0.0 when
        ``assessed`` is False.
    assessed: bool
        False when the fold is dominated by the classifier's warm-up tag or
        has no buy-and-hold reference — such folds never feed the summaries.
    note: str
        Why the fold is not assessed (empty when assessed).
    """

    fold: int
    test_range: tuple[int, int]
    bars: int
    dominant_regime: str
    regime_shares: Mapping[str, float] = field(default_factory=dict)
    excess_return_pct: float = 0.0
    assessed: bool = True
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialise the allocation to a plain dictionary."""
        return {
            "fold": self.fold,
            "test_range": list(self.test_range),
            "bars": self.bars,
            "dominant_regime": self.dominant_regime,
            "regime_shares": {
                tag: round(share, 4) for tag, share in sorted(self.regime_shares.items())
            },
            "excess_return_pct": round(self.excess_return_pct, 6),
            "assessed": self.assessed,
            "note": self.note,
        }


@dataclass(frozen=True, slots=True)
class RegimeOosSummary:
    """Per-regime aggregation of the candidate's assessed OOS folds.

    Attributes
    ----------
    regime: str
        Regime tag this block covers (never the warm-up tag).
    dominant_folds: int
        Number of assessed folds dominated by this regime.
    bars: int
        Total test-window bars attributed to this regime.
    fold_share_pct: float
        Share of all assessed folds attributed to this regime, in [0, 100].
    mean_excess_pct: float
        Mean net excess return of this regime's dominant folds (percent).
    positive_fold_rate: float
        Fraction of this regime's dominant folds with positive excess, in
        [0, 1]. Same frame as the pooled evidence's positive-fold rate.
    beats_buy_and_hold_rate: float
        Fraction with positive excess (excess over the costed buy-and-hold
        baseline), in [0, 1].
    robust_threshold_met: bool
        Whether ``dominant_folds`` reached the configured minimum, so this
        regime counts toward the robustness score.
    """

    regime: str
    dominant_folds: int
    bars: int
    fold_share_pct: float
    mean_excess_pct: float
    positive_fold_rate: float
    beats_buy_and_hold_rate: float
    robust_threshold_met: bool

    def as_dict(self) -> dict[str, Any]:
        """Serialise the summary to a plain dictionary."""
        return {
            "regime": self.regime,
            "dominant_folds": self.dominant_folds,
            "bars": self.bars,
            "fold_share_pct": round(self.fold_share_pct, 4),
            "mean_excess_pct": round(self.mean_excess_pct, 6),
            "positive_fold_rate": round(self.positive_fold_rate, 6),
            "beats_buy_and_hold_rate": round(self.beats_buy_and_hold_rate, 6),
            "robust_threshold_met": self.robust_threshold_met,
        }


@dataclass(frozen=True, slots=True)
class RegimeOosReport:
    """The full regime-conditioned evidence of one candidate evaluation.

    Attributes
    ----------
    symbol: str
        Symbol evaluated.
    classifier_name: str
        Name of the causal regime classifier used (reproducibility).
    classifier_params: Mapping[str, Any]
        Exact classifier parameters, reproduced on every report.
    min_dominant_folds: int
        Minimum dominant folds a regime needs to count toward the score.
    folds: tuple[RegimeFoldAllocation, ...]
        Every fold's regime allocation, in fold order (audit trail).
    regimes: tuple[RegimeOosSummary, ...]
        Per-regime summaries over assessed folds, sorted by tag.
    robustness_score: float | None
        Fraction of regimes meeting the dominant-fold minimum whose mean
        net excess is positive, in [0, 1]; None when no regime qualifies.
    robust_regimes: int
        Number of qualifying regimes with positive mean excess.
    insufficient_regimes: int
        Number of regimes seen as dominant but below the minimum fold count.
    total_regimes: int
        Number of distinct (non-warm-up) dominant regimes found.
    classification_error: str | None
        Set when the classifier could not label the series (e.g. a flat
        price series); all folds are then unassessed and the score is None.
    """

    symbol: str
    classifier_name: str
    classifier_params: Mapping[str, Any] = field(default_factory=dict)
    min_dominant_folds: int = 2
    folds: tuple[RegimeFoldAllocation, ...] = ()
    regimes: tuple[RegimeOosSummary, ...] = ()
    robustness_score: float | None = None
    robust_regimes: int = 0
    insufficient_regimes: int = 0
    total_regimes: int = 0
    classification_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the report to a plain dictionary (round-trips)."""
        return {
            "symbol": self.symbol,
            "classifier_name": self.classifier_name,
            "classifier_params": dict(self.classifier_params),
            "min_dominant_folds": self.min_dominant_folds,
            "folds": [fold.as_dict() for fold in self.folds],
            "regimes": [summary.as_dict() for summary in self.regimes],
            "robustness_score": self.robustness_score,
            "robust_regimes": self.robust_regimes,
            "insufficient_regimes": self.insufficient_regimes,
            "total_regimes": self.total_regimes,
            "classification_error": self.classification_error,
        }
