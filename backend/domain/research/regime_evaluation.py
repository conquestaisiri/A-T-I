# backend/domain/research/regime_evaluation.py
"""Regime-conditioned strategy evaluation contracts (task P1-007).

A strategy's headline number is rarely the whole truth: a momentum rule can
earn it all in calm markets and give it back in panics, and the researcher
needs to know which. This module answers *how a strategy performed inside
each market regime*, without ever fabricating the regime labels.

Two principles hold the module together:

- **The classification is timestamp-correct.** Who classifies a bar decides
  what information that label may use. Here a classifier's label for bar ``i``
  may depend only on prices up to and including ``i`` — never on the future.
  This is enforced as a contract and tested as a property (labels are
  prefix-stable: appending bars must not change earlier labels).
- **The attribution reconciles.** Per-bar returns are taken net of the exact
  cost model the baselines (P1-003) pay and deltas are bucketed by regime, so
  the sum of per-regime returns always equals the whole-run total return. A
  per-regime report can never show a win that the full backtest did not earn.

The whole-run :class:`BaselineResult` from ``backend.domain.research.evaluation``
is embedded in the result so every regime number is auditable against the
familiar global number of the same run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.domain.research.evaluation import BaselineResult


@dataclass(frozen=True, slots=True)
class RegimePerformance:
    """Net-of-cost performance of one strategy inside a single regime.

    Every figure is computed from the equity curve of the shared costed
    simulation, bucketed bar by bar:

    - a bar belongs to the regime its timestamp-correct label assigns;
    - a bar's equity delta already includes any flip cost charged at that
      bar, so ``return_pct`` is the regime's true net contribution.

    Attributes
    ----------
    regime: str
        Regime tag this block covers (e.g. "low_vol" / "high_vol", or the
        classifier's warm-up tag).
    bars: int
        Number of price bars assigned to this regime.
    exposure_bars: int
        Bars spent in a non-flat position within this regime.
    return_pct: float
        Cumulative net return earned over this regime's bars, as a percentage
        of starting equity. The per-regime values sum to the whole-run total.
    market_return_pct: float
        Cost-free buy-and-hold over exactly this regime's bars (the regime's
        own market move) for an honest within-regime comparison.
    excess_pct: float
        ``return_pct - market_return_pct`` inside the regime.
    per_bar_volatility_pct: float
        Standard deviation of this regime's per-bar strategy returns (%).
    sharpe_per_bar: float
        Mean of per-bar returns / their standard deviation within the regime.
        0 when volatility is zero.
    positive_bar_rate: float
        Fraction of the regime's bars with a positive per-bar return, in
        [0, 1].
    share_of_bars_pct: float
        This regime's share of the total bars, in [0, 100].
    """

    regime: str
    bars: int
    exposure_bars: int
    return_pct: float
    market_return_pct: float
    excess_pct: float
    per_bar_volatility_pct: float
    sharpe_per_bar: float
    positive_bar_rate: float
    share_of_bars_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "bars": self.bars,
            "exposure_bars": self.exposure_bars,
            "return_pct": self.return_pct,
            "market_return_pct": self.market_return_pct,
            "excess_pct": self.excess_pct,
            "per_bar_volatility_pct": self.per_bar_volatility_pct,
            "sharpe_per_bar": self.sharpe_per_bar,
            "positive_bar_rate": self.positive_bar_rate,
            "share_of_bars_pct": self.share_of_bars_pct,
        }


@dataclass(frozen=True, slots=True)
class RegimeEvaluatedResult:
    """One strategy backtest with a per-regime breakdown.

    Attributes
    ----------
    name: str
        Strategy name (unique within a comparison).
    description: str
        Human-readable strategy description.
    overall: BaselineResult
        Whole-run result from the shared costed evaluator; the single source
        of truth those regime numbers must reconcile to.
    performance: tuple[RegimePerformance, ...]
        One entry per regime found in the series, in the classifier's label
        order. Per-regime ``return_pct`` values sum to ``overall``'s total.
    classifier_name: str
        Name of the classifier used, for reproducibility.
    classifier_params: dict[str, object]
        Exact classifier parameters, reproduced on every report.
    cost_model: dict[str, object]
        Exact cost assumptions of the underlying run.
    """

    name: str
    description: str
    overall: BaselineResult
    performance: tuple[RegimePerformance, ...]
    classifier_name: str
    classifier_params: dict[str, object] = field(default_factory=dict)
    cost_model: dict[str, object] = field(default_factory=dict)

    def for_regime(self, regime: str) -> RegimePerformance | None:
        """Return the performance block for a regime tag, or ``None``."""
        for block in self.performance:
            if block.regime == regime:
                return block
        return None

    @property
    def regimes(self) -> tuple[str, ...]:
        """Regime tags, in report order."""
        return tuple(block.regime for block in self.performance)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "overall": self.overall.as_dict(),
            "performance": [block.as_dict() for block in self.performance],
            "classifier_name": self.classifier_name,
            "classifier_params": dict(self.classifier_params),
            "cost_model": dict(self.cost_model),
        }
