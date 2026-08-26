# backend/application/research/feature_attribution.py
"""Feature attribution and ablation runner (task P1-004).

Runs the ablation experiment: score a labelled sample set with the full
feature vector, then re-score with each feature in turn removed, and measure
the difference. Requires a :class:`FeatureScorer` — any callable model that
maps a feature vector to a label prediction.

The runner is deliberately model-agnostic: the scorer's internals do not
matter, only that it accepts a feature mapping and returns a label in
``{-1, 0, +1}``. This keeps attribution honest for any future model (rule,
linear, ML) without coupling the research factory to a modelling framework.

Cost accounting composes with the baseline suite (P1-003): a prediction flip
is a change of position, and every change pays the same half-spread + fee the
baselines pay. The runner reports the estimated cost of each feature's flips
so a feature's lift is always weighed against its execution cost.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Protocol

from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.domain.research.attribution import (
    AttributionReport,
    FeatureAttribution,
    Metrics,
)
from backend.domain.research.label import LabeledSample

if TYPE_CHECKING:
    from backend.application.research.experiment_registry import ExperimentRegistry
    from backend.domain.research.experiment import ExperimentRecord

logger = logging.getLogger(__name__)


class FeatureScorer(Protocol):
    """Anything that maps a feature vector to a label in {-1, 0, +1}."""

    name: str

    def predict(self, features: dict[str, Any]) -> float:
        """Return the predicted label for ``features``."""
        ...


class ThresholdScorer:
    """Deterministic reference scorer: sign of a single feature.

    A researcher's first model. Predicts ``+1`` when the feature exceeds the
    threshold, ``-1`` below it, and stands aside (``0``) when the feature is
    missing or not numeric. Never a claim of skill; a calibration check for
    the framework and a baseline for feature tests.
    """

    name = "threshold"

    def __init__(self, feature: str, threshold: float = 0.0) -> None:
        self._feature = feature
        self._threshold = threshold

    def predict(self, features: dict[str, Any]) -> float:
        value = features.get(self._feature)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return 0.0
        return 1.0 if float(value) > self._threshold else -1.0


def compute_metrics(y_true: Sequence[float], y_pred: Sequence[float]) -> Metrics:
    """Compute :class:`Metrics` over two aligned label sequences.

    Accuracy counts agreement across the whole set. Precision/recall/F1 are
    micro-averaged over the active classes (+1/-1) only; true/false positives,
    false negatives and true negatives are counted across both active classes
    so the metrics have a single, claimed value.
    """
    if len(y_true) != len(y_pred):
        raise ValueError("y_true and y_pred must be aligned")
    if not y_true:
        raise ValueError("cannot compute metrics over an empty set")

    agree = 0
    tp = 0
    fp = 0
    fn = 0
    tn = 0
    for truth, pred in zip(y_true, y_pred, strict=True):
        if truth == pred:
            agree += 1
        if pred in (1.0, -1.0):
            if truth == pred:
                tp += 1
            else:
                fp += 1
        if truth in (1.0, -1.0) and truth != pred:
            fn += 1
        if truth in (1.0, -1.0) and pred == truth:
            tn += 1

    accuracy = agree / len(y_true)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * (precision * recall) / (precision + recall) if (precision + recall) else 0.0
    return Metrics(
        accuracy=round(accuracy, 6),
        precision=round(precision, 6),
        recall=round(recall, 6),
        f1=round(f1, 6),
        n=len(y_true),
    )


def _regime_of(sample: LabeledSample, regime_key: str) -> str:
    value = sample.metadata.get(regime_key)
    if value is None:
        return "all"
    return str(value)


class AblationRunner:
    """Full-feature vs. leave-one-out scoring over labelled samples.

    Parameters
    ----------
    costs: EvaluationCosts | None
        Cost model for the flip-cost estimate; defaults to the realistic
        baseline model so attribution is honest by default.
    round_trip_multiplier: float
        A flip closes and (re-)opens a position, so it pays the round trip
        (2x the per-side cost). Kept configurable so the assumption is
        explicit rather than buried.
    """

    def __init__(
        self,
        costs: EvaluationCosts | None = None,
        round_trip_multiplier: float = 2.0,
    ) -> None:
        if round_trip_multiplier <= 0.0:
            raise ValueError("round_trip_multiplier must be positive")
        self._costs = costs or EvaluationCosts.realistic()
        self._round_trip = round_trip_multiplier

    def run(
        self,
        *,
        samples: Sequence[LabeledSample],
        scorer: FeatureScorer,
        feature_names: Sequence[str],
        regime_key: str = "regime",
    ) -> AttributionReport:
        """Ablate every named feature and measure its incremental lift.

        The full-feature predictions are computed once; each feature is then
        removed (its key deleted from the input vector) and the sample set is
        re-scored. Attribution is bucketed by the samples' ``regime_key``
        metadata (default "all" when untagged).
        """
        if not samples:
            raise ValueError("no samples to attribute")
        if not feature_names:
            raise ValueError("feature_names must not be empty")

        regimes = sorted({_regime_of(s, regime_key) for s in samples})
        full_pred = [scorer.predict(_features(s)) for s in samples]

        full_by_regime: dict[str, Metrics] = {}
        for regime in regimes:
            full_by_regime[regime] = compute_metrics(
                *self._slice(samples, full_pred, regime_key, regime)
            )

        rows: list[FeatureAttribution] = []
        for feature in feature_names:
            ablated_pred = [scorer.predict(_features(s, drop=feature)) for s in samples]
            for regime in regimes:
                truth_full, pred_full = self._slice(samples, full_pred, regime_key, regime)
                _, pred_ablated = self._slice(samples, ablated_pred, regime_key, regime)
                full_metrics = compute_metrics(truth_full, pred_full)
                ablated_metrics = compute_metrics(truth_full, pred_ablated)
                flips = sum(1 for a, b in zip(pred_full, pred_ablated, strict=True) if a != b)
                cost_pct = self._estimate_cost(flips)
                rows.append(
                    FeatureAttribution(
                        feature=feature,
                        regime=regime,
                        full_metrics=full_metrics,
                        ablated_metrics=ablated_metrics,
                        delta_accuracy=round(full_metrics.accuracy - ablated_metrics.accuracy, 6),
                        delta_f1=round(full_metrics.f1 - ablated_metrics.f1, 6),
                        flip_count=flips,
                        cost_pct=round(cost_pct, 6),
                    )
                )

        cost_model: dict[str, object] = {
            "half_spread_pct": self._costs.half_spread_pct,
            "taker_fee_pct": self._costs.taker_fee_pct,
            "round_trip_multiplier": self._round_trip,
        }
        return AttributionReport(
            feature_names=tuple(feature_names),
            regimes=tuple(regimes),
            full_by_regime=full_by_regime,
            attribution=tuple(rows),
            scorer_name=scorer.name,
            cost_model=cost_model,
        )

    # -- internals -----------------------------------------------------------

    def _slice(
        self,
        samples: Sequence[LabeledSample],
        predictions: Sequence[float],
        regime_key: str,
        regime: str,
    ) -> tuple[list[float], list[float]]:
        """Return (labels, predictions) for the samples in ``regime``."""
        truth: list[float] = []
        pred: list[float] = []
        for sample, prediction in zip(samples, predictions, strict=True):
            if _regime_of(sample, regime_key) == regime:
                truth.append(float(sample.label))
                pred.append(float(prediction))
        return truth, pred

    def _estimate_cost(self, flips: int) -> float:
        """Estimated cost of ``flips`` position changes, as % of notional."""
        per_side = self._costs.half_spread_pct + self._costs.taker_fee_pct
        return flips * per_side * self._round_trip * 100.0


def _features(sample: LabeledSample, drop: str | None = None) -> dict[str, Any]:
    """Feature vector for a sample, optionally with one feature removed."""
    features = dict(sample.features)
    if drop is not None:
        features.pop(drop, None)
    return features


def record_ablation(
    registry: ExperimentRegistry,
    experiment_id: str,
    report: AttributionReport,
) -> ExperimentRecord:
    """Persist one ablation report as an experiment's full result payload.

    The whole report — per-feature x per-regime attribution with
    full/ablated metrics, flip counts, flip cost, cost model, and scorer
    name — is stored verbatim as the experiment's metrics (T2-17-1).
    Nothing is summarised away: a stored report is a full reproduction
    record, and the store's payload column keeps an identical snapshot of
    the record. The experiment must exist and be RUNNING; the registry
    raises otherwise.
    """
    return registry.complete(experiment_id, metrics=report.as_dict())
