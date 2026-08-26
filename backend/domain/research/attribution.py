# backend/domain/research/attribution.py
"""Feature attribution and ablation contracts (task P1-004).

Attribution answers a research question the whole factory depends on: *which
features actually matter, in which regime, and at what cost?* The answer is
built from the same label-aware data the rest of the factory uses:

- **Ablation**: each feature can be switched off (removed from the scorer's
  input) and the resulting predictive metrics compared with the full set.
  The difference is the feature's measured, incremental contribution.
- **By regime**: samples are bucketed by a point-in-time regime tag attached
  at labeling time (regime classification is itself timestamp-correct; the
  researcher attaches it to ``LabeledSample.metadata``). Attribution is
  reported per regime, never globally.
- **By cost**: switching a feature changes how often the model's predictions
  flip. Every flip is a trade — and every trade pays the same cost model the
  baselines (P1-003) pay. Each attribution therefore reports the estimated
  cost of the feature's induced turnover, so a feature is only 'good' when
  its predictive lift beats its execution cost.

The report is a plain immutable value object that the experiment registry
(P1-005) can persist and the robustness harness (P1-008) can perturb.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class Metrics:
    """Standard predictive metrics over one labelled sample set.

    Accuracy is exact agreement across the full label set. Precision, recall
    and F1 are correct-only averaged over active classes (+1/-1): a sample
    whose label and prediction are both 0 counts only toward accuracy, never
    toward the F1-style averages, mirroring how directional research treats
    abstention.
    """

    accuracy: float
    precision: float
    recall: float
    f1: float
    n: int

    def as_dict(self) -> dict[str, object]:
        return {
            "accuracy": self.accuracy,
            "precision": self.precision,
            "recall": self.recall,
            "f1": self.f1,
            "n": self.n,
        }


@dataclass(frozen=True, slots=True)
class FeatureAttribution:
    """Measured contribution of one feature within one regime.

    Attributes
    ----------
    feature: str
        Feature key that was ablated.
    regime: str
        Regime bucket this attribution belongs to ("all" when no regime tag).
    full_metrics: Metrics
        Metrics with the full feature set, over this regime's samples.
    ablated_metrics: Metrics
        Metrics with the feature removed, over the same samples.
    delta_accuracy: float
        ``full.accuracy - ablated.accuracy``; positive means the feature adds
        predictive power, negative means removing it *improves* accuracy.
    delta_f1: float
        ``full.f1 - ablated.f1`` (same sign convention).
    flip_count: int
        Number of samples whose prediction changed when the feature was
        removed. Each flip is a change of position, hence a trade.
    cost_pct: float
        Estimated execution cost of the feature's induced flips, as a
        percentage of notional exposure (flips * round-trip cost). A positive
        value is the ongoing cost of using this feature.
    """

    feature: str
    regime: str
    full_metrics: Metrics
    ablated_metrics: Metrics
    delta_accuracy: float
    delta_f1: float
    flip_count: int
    cost_pct: float

    @property
    def lift_is_worth_cost(self) -> bool:
        """Whether the feature's F1 lift exceeds its induced trading cost."""
        return self.delta_f1 > 0.0 and self.delta_f1 > self.cost_pct / 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "regime": self.regime,
            "full_metrics": self.full_metrics.as_dict(),
            "ablated_metrics": self.ablated_metrics.as_dict(),
            "delta_accuracy": self.delta_accuracy,
            "delta_f1": self.delta_f1,
            "flip_count": self.flip_count,
            "cost_pct": self.cost_pct,
            "lift_is_worth_cost": self.lift_is_worth_cost,
        }


@dataclass(frozen=True, slots=True)
class AttributionReport:
    """Full result of one ablation run across regimes.

    Parameters
    ----------
    feature_names: tuple[str, ...]
        The features the runner was asked to ablate, in order.
    regimes: tuple[str, ...]
        Regime buckets found in the samples (or ("all",) when untagged).
    full_by_regime: dict[str, Metrics]
        Metrics with the full feature set, keyed by regime.
    attribution: tuple[FeatureAttribution, ...]
        One entry per feature and regime combination.
    scorer_name: str
        Name of the scorer used, for reproducibility in the registry.
    cost_model: dict[str, object]
        The exact cost assumptions applied (spread/fees), reproduced on every
        report so a number can always be audited against its assumptions.
    """

    feature_names: tuple[str, ...]
    regimes: tuple[str, ...]
    full_by_regime: dict[str, Metrics]
    attribution: tuple[FeatureAttribution, ...]
    scorer_name: str
    cost_model: dict[str, object] = field(default_factory=dict)

    def for_feature(self, feature: str) -> tuple[FeatureAttribution, ...]:
        """Return all attributions of a single feature across regimes."""
        return tuple(a for a in self.attribution if a.feature == feature)

    def for_regime(self, regime: str) -> tuple[FeatureAttribution, ...]:
        """Return all attributions within a single regime."""
        return tuple(a for a in self.attribution if a.regime == regime)

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "regimes": list(self.regimes),
            "full_by_regime": {k: v.as_dict() for k, v in self.full_by_regime.items()},
            "attribution": [a.as_dict() for a in self.attribution],
            "scorer_name": self.scorer_name,
            "cost_model": self.cost_model,
        }
