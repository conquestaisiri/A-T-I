# backend/application/research/regime_evaluation.py
"""Regime-conditioned strategy evaluation (task P1-007).

The evaluator answers: *how did this strategy perform inside each market
regime?* Two non-negotiables from the contract:

- **Timestamp-correct regimes.** A classifier's label for bar ``i`` may only
  depend on prices up to and including ``i``. This module ships a causal,
  deterministic classifier (:class:`VolatilityRegimeClassifier`) and accepts
  any other :class:`RegimeClassifier` conforming to the same property; the
  property itself is tested, so no future classifier can smuggle look-ahead
  into a regime report.
- **Reconciliation.** Attribution never re-runs the strategy: it reuses the
  shared costed simulation of :class:`BaselineEvaluator` (P1-003) and buckets
  that equity curve's per-bar deltas by regime. Per-bar deltas already include
  flip costs, so each regime's return is net of the exact cost model — and the
  per-regime returns always sum to the whole-run total return.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from backend.application.research.baseline_evaluation import (
    BaselineEvaluator,
    BaselineStrategy,
    EvaluationCosts,
)
from backend.domain.research.evaluation import BaselineResult
from backend.domain.research.regime_evaluation import (
    RegimeEvaluatedResult,
    RegimePerformance,
)

DEFAULT_WARMUP_TAG = "warmup"


class RegimeClassifier(Protocol):
    """Causal price-series regime labeler.

    A conforming classifier maps a price series to one label per bar. The
    **causality contract**: ``labels(prices)[i]`` must depend only on
    ``prices[0..i]``. Implementations that peek at the future (e.g. global
    quantiles of the whole series) violate the contract and must not be used.
    """

    name: str

    def labels(self, prices: Sequence[float]) -> Sequence[str]:
        """Return one regime label per price bar (causal)."""
        ...


class VolatilityRegimeClassifier:
    """Deterministic rolling-volatility regime classifier.

    Labels each bar ``i`` "high_vol" or "low_vol" by comparing the realised
    per-bar volatility of the trailing ``window`` returns ending at ``i`` with
    an absolute ``vol_threshold``. Bars before ``window`` returns exist are
    tagged ``warmup``.

    The threshold is absolute (not a whole-series quantile), which is what
    makes the classifier causal: each label uses only the trailing window
    ending at that bar, never future data.
    """

    name = "volatility_regime"

    def __init__(
        self,
        window: int = 20,
        vol_threshold: float = 0.005,
        warmup_tag: str = DEFAULT_WARMUP_TAG,
    ) -> None:
        if window < 2:
            raise ValueError("regime window must be at least 2 bars")
        if vol_threshold <= 0.0:
            raise ValueError("vol_threshold must be positive")
        if not warmup_tag or warmup_tag in ("high_vol", "low_vol"):
            raise ValueError("warmup_tag must be non-empty and distinct from regime tags")
        self._window = window
        self._vol_threshold = vol_threshold
        self._warmup = warmup_tag

    def labels(self, prices: Sequence[float]) -> Sequence[str]:
        if len(prices) < 2:
            return tuple(self._warmup for _ in prices)
        if any(p <= 0 for p in prices):
            raise ValueError("prices must be strictly positive")
        if len(set(prices)) <= 1:
            # A flat series has no finite returns; regime is not observable.
            return tuple(self._warmup for _ in prices)

        labels: list[str] = []
        for i in range(len(prices)):
            if i < self._window:
                labels.append(self._warmup)
                continue
            window = prices[i - self._window : i + 1]
            returns = [window[j] / window[j - 1] - 1.0 for j in range(1, len(window))]
            mean = sum(returns) / len(returns)
            total = sum((r - mean) ** 2 for r in returns)
            vol = math.sqrt(total / max(len(returns) - 1, 1))
            labels.append("high_vol" if vol > self._vol_threshold else "low_vol")
        return tuple(labels)

    def params(self) -> dict[str, object]:
        return {
            "window": self._window,
            "vol_threshold": self._vol_threshold,
            "warmup_tag": self._warmup,
        }


@dataclass(frozen=True, slots=True)
class _BarDelta:
    """Hand-carried per-bar equity delta and price return."""

    equity_delta_pct: float
    market_return_pct: float
    exposed: bool


class RegimeEvaluator:
    """Run a strategy and report net performance broken down by regime.

    The whole-run number comes from the exact same costed simulation every
    baseline uses; the regime breakdown buckets that single equity curve, never
    re-simulating with different costs or prices. Deliberately no separate
    "regime-only backtest": running sub-series independently would refund
    costs and change entries, which would be a different, non-comparable trade
    — the very thing this factory refuses to do.
    """

    def __init__(
        self,
        *,
        costs: EvaluationCosts | None = None,
        classifier: RegimeClassifier | None = None,
    ) -> None:
        self._costs = costs or EvaluationCosts.realistic()
        self._classifier = classifier or VolatilityRegimeClassifier()

    def evaluate(
        self,
        *,
        strategy: BaselineStrategy,
        prices: Sequence[float],
        starting_equity: float = 100_000.0,
    ) -> RegimeEvaluatedResult:
        """Backtest ``strategy`` on ``prices`` and return per-regime metrics."""
        evaluator = BaselineEvaluator(self._costs)
        overall = evaluator.evaluate(
            strategy=strategy,
            prices=prices,
            starting_equity=starting_equity,
        )
        targets = strategy.targets(prices)
        labels = self._classifier.labels(list(prices))
        if len(labels) != len(prices):
            raise ValueError("regime classifier must return one label per price bar")

        deltas = self._bar_deltas(overall, prices, targets)
        blocks = self._bucket(labels, deltas)
        return RegimeEvaluatedResult(
            name=overall.name,
            description=overall.description,
            overall=overall,
            performance=tuple(blocks),
            classifier_name=self._classifier.name,
            classifier_params=_params_of(self._classifier),
            cost_model={
                "half_spread_pct": self._costs.half_spread_pct,
                "taker_fee_pct": self._costs.taker_fee_pct,
            },
        )

    def _bar_deltas(
        self, overall: BaselineResult, prices: Sequence[float], targets: Sequence[float]
    ) -> list[_BarDelta]:
        """Per-bar equity deltas (net of costs) from the shared equity curve.

        Exposure at bar ``i`` is ``targets[i] != 0`` — the same position the
        shared simulation holds — so per-regime exposure is exact rather than a
        whole-series proxy.
        """
        curve = overall.equity_curve
        deltas: list[_BarDelta] = []
        for i in range(len(curve)):
            if i == 0:
                equity_delta = (curve[0] - overall.starting_equity) / overall.starting_equity
                market_delta = 0.0
            else:
                equity_delta = (curve[i] - curve[i - 1]) / overall.starting_equity
                market_delta = prices[i] / prices[i - 1] - 1.0
            deltas.append(
                _BarDelta(
                    equity_delta_pct=equity_delta * 100.0,
                    market_return_pct=market_delta * 100.0,
                    exposed=targets[i] != 0.0,
                )
            )
        return deltas

    def _bucket(self, labels: Sequence[str], deltas: list[_BarDelta]) -> list[RegimePerformance]:
        by_regime: dict[str, list[tuple[int, _BarDelta]]] = {}
        for i, label in enumerate(labels):
            by_regime.setdefault(label, []).append((i, deltas[i]))

        order = sorted(by_regime, key=lambda tag: (tag == DEFAULT_WARMUP_TAG, tag))
        # Keep label tags stable/readable: natural order, warmup last.
        blocks: list[RegimePerformance] = []
        total_bars = len(deltas)
        for tag in order:
            entries = by_regime[tag]
            bars = len(entries)
            sample_pct = (bars / total_bars) * 100.0 if total_bars else 0.0
            exposure = sum(1 for entry in entries if entry[1].exposed)
            per_bar_returns = [entry[1].equity_delta_pct for entry in entries]
            return_pct = sum(per_bar_returns)
            market_pct = sum(entry[1].market_return_pct for entry in entries)
            excess = return_pct - market_pct
            vol = _std_dev(per_bar_returns)
            sharpe = _mean(per_bar_returns) / vol if vol > 0.0 else 0.0
            positive = _mean([1.0 if r > 0.0 else 0.0 for r in per_bar_returns])
            blocks.append(
                RegimePerformance(
                    regime=tag,
                    bars=bars,
                    exposure_bars=exposure,
                    return_pct=round(return_pct, 6),
                    market_return_pct=round(market_pct, 6),
                    excess_pct=round(excess, 6),
                    per_bar_volatility_pct=round(vol, 6),
                    sharpe_per_bar=round(sharpe, 6),
                    positive_bar_rate=round(positive, 6),
                    share_of_bars_pct=round(sample_pct, 6),
                )
            )
        return blocks


def _params_of(classifier: RegimeClassifier) -> dict[str, object]:
    params: Callable[[], dict[str, Any]] | None = getattr(classifier, "params", None)
    if params is not None:
        return dict(params())
    return {}


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std_dev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    total = sum((v - mean) ** 2 for v in values)
    return math.sqrt(total / (len(values) - 1))
