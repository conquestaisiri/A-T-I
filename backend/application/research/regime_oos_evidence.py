# backend/application/research/regime_oos_evidence.py
"""Regime-conditioned OOS evidence builder (task T2-11-1).

Turns a ``DecisionPipelineEvaluator`` report plus the exact price series it
ran on into regime-attributed fold evidence: each fold is allocated to the
regime dominating its test window (labels from ``regime_evaluation``'s causal
classifier contract), assessed folds are aggregated per regime, and a regime
robustness score answers "did the candidate make positive net excess in most
regimes it was actually tested in?".

Why this module exists as the first Tier-2 piece
-----------------------------------------------
The evidence pipeline currently proves *how* a candidate performed
out-of-sample; T2-11-1 adds *where* — inside which market regimes — so a
candidate passport carries regime-performance evidence that the edge-decay
system (T2-15) and rollback triggers can use later. The verdict gates are
deliberately not touched: regime evidence is advisory on the passport, never
a silent promoter or killer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from backend.application.research.decision_pipeline_evaluator import (
    OutOfSampleReport,
)
from backend.application.research.regime_evaluation import (
    DEFAULT_WARMUP_TAG,
    RegimeClassifier,
    VolatilityRegimeClassifier,
)
from backend.domain.research.regime_oos import (
    RegimeFoldAllocation,
    RegimeOosReport,
    RegimeOosSummary,
)

_MIN_DEFAULT_DOMINANT_FOLDS = 2


class RegimeOosEvidenceBuilder:
    """Attach regime-conditioned evidence to an out-of-sample report.

    Parameters
    ----------
    classifier: RegimeClassifier | None
        Causal regime labeler (defaults to ``VolatilityRegimeClassifier``).
    min_dominant_folds: int
        Minimum dominant folds a regime needs to count toward the robustness
        score and to report a ``robust_threshold_met`` block.
    """

    def __init__(
        self,
        *,
        classifier: RegimeClassifier | None = None,
        min_dominant_folds: int = _MIN_DEFAULT_DOMINANT_FOLDS,
    ) -> None:
        if min_dominant_folds < 1:
            raise ValueError("min_dominant_folds must be >= 1")
        self._classifier = classifier or VolatilityRegimeClassifier()
        self._min_dominant_folds = min_dominant_folds
        self._warmup = getattr(self._classifier, "warmup_tag", None) or DEFAULT_WARMUP_TAG

    def build(self, report: OutOfSampleReport, prices: Sequence[float]) -> RegimeOosReport:
        """Compute the regime-conditioned evidence for ``report``.

        ``prices`` must be the exact price series the report's fold windows
        refer to (one price per step, aligned to the evaluator's step
        indices). Fold test-window indices are then sliced from the full
        series, and the causal classifier labels the whole series once
        (labels for a bar depend only on prices up to that bar).
        """
        prices = [float(p) for p in prices]
        if not prices:
            raise ValueError("regime evidence requires a non-empty price series")
        folds = list(report.folds)
        if not folds:
            raise ValueError("regime evidence requires a report with at least one fold")
        for fold in folds:
            if fold.test_range[1] > len(prices):
                raise ValueError(
                    f"fold {fold.fold} test window ends at step {fold.test_range[1]} but "
                    f"only {len(prices)} prices were provided"
                )
        labels = self._labels(prices)
        if labels is None:
            allocations = [self._unassessed(fold, _FLAT_SERIES_NOTE) for fold in folds]
        else:
            allocations = [
                self._allocate(fold, labels[fold.test_range[0] : fold.test_range[1]])
                for fold in folds
            ]
        summaries, robust_score, robust_regimes = self._summarise(allocations)
        insufficient = sum(1 for s in summaries if not s.robust_threshold_met)
        classification_error = None if labels is not None else _FLAT_SERIES_NOTE

        return RegimeOosReport(
            symbol=report.symbol,
            classifier_name=str(self._classifier.name),
            classifier_params=_params_of(self._classifier),
            min_dominant_folds=self._min_dominant_folds,
            folds=tuple(allocations),
            regimes=summaries,
            robustness_score=robust_score,
            robust_regimes=robust_regimes,
            insufficient_regimes=insufficient,
            total_regimes=len(summaries),
            classification_error=classification_error,
        )

    def _labels(self, prices: Sequence[float]) -> list[str] | None:
        """Causal labels for the full series, or None when unclassifiable.

        A flat or single-point series has no observable regimes; the
        classification is reported as an error rather than fabricated, and
        every fold is left unassessed.
        """
        try:
            labels = list(self._classifier.labels(list(prices)))
        except ValueError:
            labels = None
        if labels is not None and len(labels) != len(prices):
            raise ValueError("regime classifier must return one label per price bar")
        if labels is not None and all(tag == self._warmup for tag in labels):
            # No bar ever left the classifier's warm-up: the series has no
            # observable regime (e.g. a flat price series). Reporting this as
            # an error is honest; fabricating a regime would not be.
            labels = None
        return labels

    def _allocate(
        self,
        fold: Any,
        window_labels: Sequence[str],
    ) -> RegimeFoldAllocation:
        """Attribute one fold to its dominant test-window regime."""
        if not window_labels:
            return RegimeFoldAllocation(
                fold=fold.fold,
                test_range=fold.test_range,
                bars=0,
                dominant_regime=self._warmup,
                assessed=False,
                note="empty test window",
            )
        counts = Counter(window_labels)
        dominant = max(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0]
        shares = {tag: (count / len(window_labels)) * 100.0 for tag, count in counts.items()}
        excess, note, assessed = self._fold_outcome(fold)
        if assessed and dominant == self._warmup:
            assessed = False
            note = "test window dominated by regime warm-up; not attributed"
        return RegimeFoldAllocation(
            fold=fold.fold,
            test_range=fold.test_range,
            bars=len(window_labels),
            dominant_regime=dominant,
            regime_shares=shares,
            excess_return_pct=excess,
            assessed=assessed,
            note=note,
        )

    def _fold_outcome(self, fold: Any) -> tuple[float, str, bool]:
        """The fold's net excess (or None) and its assessment note."""
        reference = next((b for b in fold.baselines if b.name == "buy_and_hold"), None)
        if reference is None:
            return 0.0, "no buy-and-hold reference on this fold", False
        excess = fold.report.returns_pct - reference.total_return_pct
        return round(float(excess), 6), "", True

    @staticmethod
    def _unassessed(fold: Any, note: str) -> RegimeFoldAllocation:
        """An allocation left out of the regime evidence (classification down)."""
        return RegimeFoldAllocation(
            fold=fold.fold,
            test_range=fold.test_range,
            bars=fold.test_range[1] - fold.test_range[0],
            dominant_regime=DEFAULT_WARMUP_TAG,
            assessed=False,
            note=note,
        )

    def _summarise(
        self, allocations: Sequence[RegimeFoldAllocation]
    ) -> tuple[tuple[RegimeOosSummary, ...], float | None, int]:
        """Per-regime aggregates over assessed folds + the robustness score."""
        assessed = [a for a in allocations if a.assessed]
        by_regime: dict[str, list[RegimeFoldAllocation]] = {}
        for allocation in assessed:
            by_regime.setdefault(allocation.dominant_regime, []).append(allocation)

        summaries: list[RegimeOosSummary] = []
        for regime in sorted(by_regime):
            owned = by_regime[regime]
            wins = sum(1 for a in owned if a.excess_return_pct > 0.0)
            mean_excess = sum(a.excess_return_pct for a in owned) / len(owned)
            rate = wins / len(owned)
            summaries.append(
                RegimeOosSummary(
                    regime=regime,
                    dominant_folds=len(owned),
                    bars=sum(a.bars for a in owned),
                    fold_share_pct=(len(owned) / len(assessed)) * 100.0,
                    mean_excess_pct=round(mean_excess, 6),
                    positive_fold_rate=round(rate, 6),
                    beats_buy_and_hold_rate=round(rate, 6),
                    robust_threshold_met=len(owned) >= self._min_dominant_folds,
                )
            )

        qualifying = [s for s in summaries if s.robust_threshold_met]
        if not qualifying:
            return tuple(summaries), None, 0
        robust = sum(1 for s in qualifying if s.mean_excess_pct > 0.0)
        return tuple(summaries), robust / len(qualifying), robust


_FLAT_SERIES_NOTE = "regime classification unavailable (e.g. flat price series); folds unassessed"


def _params_of(classifier: RegimeClassifier) -> dict[str, Any]:
    params = getattr(classifier, "params", None)
    if params is not None:
        return dict(params())
    return {}
