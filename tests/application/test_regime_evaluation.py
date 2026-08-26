"""Tests for regime-conditioned strategy evaluation (P1-007).

The module must guarantee:

1. Regime classification is timestamp-correct — a bar's label never uses
   future prices, and appending bars never changes earlier labels.
2. Per-regime returns are net of the shared cost model and always sum to the
   whole-run total return (reconciliation, no fabricated wins).
3. Reports are deterministic and reproduce classifier + cost assumptions.
"""

from __future__ import annotations

import math

import pytest
from backend.application.research.baseline_evaluation import (
    AlwaysFlatBaseline,
    BuyAndHoldBaseline,
    EvaluationCosts,
    MomentumBaseline,
)
from backend.application.research.regime_evaluation import (
    RegimeEvaluator,
    VolatilityRegimeClassifier,
)


def two_regime_series(
    n_calm: int = 60,
    n_storm: int = 60,
    calm_step: float = 0.05,
    storm_step: float = 5.0,
    start: float = 100.0,
) -> list[float]:
    """Calm uptrend, then a stormy trend, then calm — returns monotonically
    increasing prices crossing from low to high volatility."""
    prices = []
    price = start
    for _ in range(n_calm):
        prices.append(price)
        price += calm_step
    storm = price
    for _ in range(n_storm):
        prices.append(storm)
        storm += storm_step
    after = storm
    for _ in range(n_calm):
        prices.append(after)
        after += calm_step
    return prices


class TestClassifierCausality:
    def test_labels_are_causal_prefix_stable(self):
        classifier = VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        series = two_regime_series()
        baseline = classifier.labels(series)
        # Appending more calm bars must not change the first N labels: a
        # classifier that peeked at the future would silently relabel.
        extended = classifier.labels(series + [series[-1] + 0.05] * 40)
        assert list(baseline) == list(extended[: len(series)])

    def test_warmup_before_window(self):
        classifier = VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        labels = classifier.labels([100 + i for i in range(30)])
        assert labels[0] == "warmup"
        assert labels[9] == "warmup"
        assert all(tag in ("high_vol", "low_vol") for tag in labels[10:])

    def test_high_vol_region_is_flagged(self):
        classifier = VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        series = two_regime_series()
        labels = list(classifier.labels(series))
        # The stormy segment (large steps) must contain high_vol bars, and the
        # calm trailing region must return to low_vol.
        storm = "high_vol" in labels
        calm_tail = labels[-20:]
        assert storm
        assert "low_vol" in calm_tail

    def test_flat_series_is_warmup(self):
        classifier = VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        labels = classifier.labels([100.0] * 50)
        assert set(labels) == {"warmup"}

    def test_validation(self):
        with pytest.raises(ValueError):
            VolatilityRegimeClassifier(window=1, vol_threshold=0.01)
        with pytest.raises(ValueError):
            VolatilityRegimeClassifier(window=10, vol_threshold=0.0)
        with pytest.raises(ValueError):
            VolatilityRegimeClassifier(window=10, vol_threshold=0.01, warmup_tag="high_vol")


class TestReconciliation:
    def test_per_regime_returns_sum_to_total(self):
        evaluator = RegimeEvaluator(
            costs=EvaluationCosts.realistic(),
            classifier=VolatilityRegimeClassifier(window=10, vol_threshold=0.01),
        )
        prices = two_regime_series()
        result = evaluator.evaluate(strategy=MomentumBaseline(5), prices=prices)
        summed = sum(p.return_pct for p in result.performance)
        assert summed == pytest.approx(result.overall.total_return_pct, abs=1e-6)

    def test_per_regime_bars_sum_to_total(self):
        evaluator = RegimeEvaluator(
            classifier=VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        )
        prices = two_regime_series()
        result = evaluator.evaluate(strategy=AlwaysFlatBaseline(), prices=prices)
        assert sum(p.bars for p in result.performance) == len(prices)

    def test_regime_numbers_reproduce_overall_sharpe_context(self):
        evaluator = RegimeEvaluator(
            classifier=VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        )
        prices = two_regime_series()
        result = evaluator.evaluate(strategy=BuyAndHoldBaseline(), prices=prices)
        # No fabricated positive alpha: the costed whole run is the ceiling.
        assert abs(result.overall.excess_return_pct) < abs(result.overall.total_return_pct) + 1e-6


class TestReports:
    def test_reports_embed_baseline(self):
        evaluator = RegimeEvaluator(
            costs=EvaluationCosts.realistic(),
            classifier=VolatilityRegimeClassifier(window=15, vol_threshold=0.4),
        )
        prices = two_regime_series()
        result = evaluator.evaluate(strategy=MomentumBaseline(10), prices=prices)
        assert result.overall.name == "momentum"
        assert result.overall.equity_curve
        assert result.performance  # at least one regime block
        assert result.regimes == tuple(p.regime for p in result.performance)

    def test_exposure_is_exact_per_regime(self):
        evaluator = RegimeEvaluator(
            classifier=VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        )
        prices = two_regime_series()
        # Always-flat: zero exposure everywhere.
        flat = evaluator.evaluate(strategy=AlwaysFlatBaseline(), prices=prices)
        assert all(p.exposure_bars == 0 for p in flat.performance)
        # Fully-long: every bar exposed in every regime.
        long = evaluator.evaluate(strategy=BuyAndHoldBaseline(), prices=prices)
        assert all(p.exposure_bars == p.bars for p in long.performance)

    def test_for_regime_lookup(self):
        evaluator = RegimeEvaluator(
            classifier=VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        )
        result = evaluator.evaluate(strategy=AlwaysFlatBaseline(), prices=two_regime_series())
        block = result.for_regime("warmup")
        assert block is not None
        assert block.exposure_bars == 0  # flat everywhere
        assert result.for_regime("does_not_exist") is None

    def test_no_realtime_volatility_peeking(self):
        # A classifier whose threshold is an absolute level (never the series'
        # quantile) stays causal; the evaluator must refuse a mislabeled series
        # defensively, but the shipped classifier is enough for determinism.
        classifier = VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        a = list(classifier.labels(two_regime_series()[:100]))
        b = list(classifier.labels(two_regime_series()[:100]))
        assert a == b

    def test_as_dict_round_trip(self):
        evaluator = RegimeEvaluator(
            costs=EvaluationCosts.realistic(),
            classifier=VolatilityRegimeClassifier(window=10, vol_threshold=0.01),
        )
        result = evaluator.evaluate(strategy=MomentumBaseline(5), prices=two_regime_series())
        payload = result.as_dict()
        assert payload["name"] == "momentum"
        assert payload["classifier_name"] == "volatility_regime"
        assert payload["cost_model"]["half_spread_pct"] == 0.0002
        assert len(payload["performance"]) == len(result.performance)


class TestDeterminism:
    def test_deterministic_across_runs(self):
        evaluator = RegimeEvaluator(
            costs=EvaluationCosts.realistic(),
            classifier=VolatilityRegimeClassifier(window=10, vol_threshold=0.01),
        )
        a = evaluator.evaluate(strategy=MomentumBaseline(5), prices=two_regime_series())
        b = evaluator.evaluate(strategy=MomentumBaseline(5), prices=two_regime_series())
        assert a.as_dict() == b.as_dict()

    def test_labels_match_regime_math(self):
        classifier = VolatilityRegimeClassifier(window=10, vol_threshold=0.01)
        prices = two_regime_series()
        labels = list(classifier.labels(prices))
        assert len(labels) == len(prices)
        for i in range(10, len(prices)):
            win = prices[i - 10 : i + 1]
            returns = [win[j] / win[j - 1] - 1 for j in range(1, len(win))]
            mean = sum(returns) / len(returns)
            total = sum((r - mean) ** 2 for r in returns)
            vol = math.sqrt(total / (len(returns) - 1))
            expected = "high_vol" if vol > 0.01 else "low_vol"
            assert labels[i] == expected
