# backend/application/research/robustness.py
"""Robustness and multiple-testing harness (task P1-008).

Every number this module produces is recomputed on the **shared** costed
evaluator (:class:`BaselineEvaluator`, P1-003) with the **same** price series
and the **same** cost model — the perturbation and the champion are scored by
the exact same ruler, which is what makes the comparison meaningful.

The harness is deliberately separated from strategy choice: it perturbs a
"trade idea" by scoring a list of variants (the same idea with nearby
parameters), stresses expenses by re-running under escalating cost/slippage
assumptions, and reports selection bias by measuring how much of the best-of-N
headline is expected under a null. It never improves or tunes anything itself —
research honesty, not feature engineering.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Protocol

from backend.application.research.baseline_evaluation import (
    BaselineEvaluator,
    BaselineStrategy,
    EvaluationCosts,
)
from backend.domain.research.pbo import expected_max_of_normal_normals
from backend.domain.research.robustness import (
    ExpenseStressPoint,
    ExpenseStressReport,
    PerturbationOutcome,
    PerturbationReport,
    SelectionBiasReport,
)

_MIN_POSITIVE_FRACTION = 0.6


def robustness_payload(
    perturbation: PerturbationReport,
    stress: ExpenseStressReport,
    selection: SelectionBiasReport,
) -> dict[str, object]:
    """Bundle the three robustness reports into one verbatim evidence block.

    T3-23-1 seam: the evidence engine embeds this payload into the
    passport's evidence verbatim, so the passport carries the full
    robustness surface (perturbation outcomes, expense/slippage stress
    axis, selection-bias correction) — reproducible, never summarised
    away.
    """
    return {
        "perturbation": perturbation.as_dict(),
        "expense_stress": stress.as_dict(),
        "selection_bias": selection.as_dict(),
    }


class LabelledVariant(Protocol):
    """A perturbed strategy variant with a display label."""

    label: str

    def strategy(self) -> BaselineStrategy:
        """Return the strategy for this variant."""
        ...


class RobustnessRunner:
    """Score variants, stress expenses, and report selection bias.

    Parameters
    ----------
    costs: EvaluationCosts | None
        The *baseline* cost model (realistic by default). Stress ladders are
        relative multipliers of this model, so every number stays auditable
        against the same starting assumptions the factory uses everywhere.
    """

    def __init__(self, costs: EvaluationCosts | None = None) -> None:
        self._costs = costs or EvaluationCosts.realistic()

    def perturb(
        self,
        *,
        variants: Sequence[LabelledVariant],
        prices: Sequence[float],
        starting_equity: float = 100_000.0,
    ) -> PerturbationReport:
        """Score every variant on one shared world and report robustness.

        Costs and the price series are identical across variants; the champion
        is simply the best excess return among them. A robust idea keeps a
        positive excess across most nearby parameter choices.
        """
        if not variants:
            raise ValueError("at least one variant is required")
        evaluator = BaselineEvaluator(self._costs)
        outcomes: list[PerturbationOutcome] = []
        for variant in variants:
            result = evaluator.evaluate(
                strategy=variant.strategy(),
                prices=prices,
                starting_equity=starting_equity,
            )
            outcomes.append(
                PerturbationOutcome(
                    label=variant.label,
                    result=result,
                    excess_return_pct=result.excess_return_pct,
                )
            )
        ordered = sorted(outcomes, key=lambda o: o.excess_return_pct, reverse=True)
        champion = ordered[0]
        positive = sum(1 for o in outcomes if o.excess_return_pct > 0.0)
        fraction = positive / len(outcomes)
        return PerturbationReport(
            champion_label=champion.label,
            champion_excess_pct=round(champion.excess_return_pct, 6),
            variant_count=len(outcomes),
            outcomes=tuple(ordered),
            positive_variants=positive,
            positive_fraction=round(fraction, 6),
            robust=fraction >= _MIN_POSITIVE_FRACTION,
        )

    def expense_stress(
        self,
        *,
        strategy: BaselineStrategy,
        prices: Sequence[float],
        cost_multipliers: Sequence[float] = (1.0, 2.0, 5.0, 10.0),
        slippage_multipliers: Sequence[float] = (1.0, 2.0, 5.0, 10.0),
        starting_equity: float = 100_000.0,
    ) -> ExpenseStressReport:
        """Score ``strategy`` under escalating cost and slippage assumptions.

        ``cost_axis`` scales half-spread + taker fee together; ``slippage_axis``
        scales only half-spread. Each point re-runs the *same* strategy on the
        *same* prices under that cost model, so the shape of the decay is the
        strategy's cost-sensitivity, nothing else.
        """
        if not cost_multipliers or not slippage_multipliers:
            raise ValueError("at least one multiplier per axis is required")
        cost_axis = tuple(
            self._point(strategy, prices, half_m, taker_m, starting_equity)
            for half_m, taker_m in _paired(cost_multipliers, cost_multipliers)
        )
        slippage_axis = tuple(
            self._point(strategy, prices, half_m, 1.0, starting_equity)
            for half_m in slippage_multipliers
        )
        return ExpenseStressReport(
            strategy_name=str(strategy.name),
            cost_axis=cost_axis,
            slippage_axis=slippage_axis,
            survives_2x_cost=_positive_at(cost_axis, 2.0),
            survives_2x_slippage=_positive_at(slippage_axis, 2.0),
        )

    def _point(
        self,
        strategy: BaselineStrategy,
        prices: Sequence[float],
        half_spread_mul: float,
        taker_fee_mul: float,
        starting_equity: float,
    ) -> ExpenseStressPoint:
        costs = EvaluationCosts(
            half_spread_pct=self._costs.half_spread_pct * half_spread_mul,
            taker_fee_pct=self._costs.taker_fee_pct * taker_fee_mul,
        )
        result = BaselineEvaluator(costs).evaluate(
            strategy=strategy,
            prices=prices,
            starting_equity=starting_equity,
        )
        return ExpenseStressPoint(
            multiplier=round(half_spread_mul, 4),
            excess_return_pct=round(result.excess_return_pct, 6),
            total_return_pct=round(result.total_return_pct, 6),
            transaction_cost_pct=round(result.transaction_cost_pct, 6),
        )

    def selection_bias(
        self,
        *,
        excess_returns: Sequence[float],
    ) -> SelectionBiasReport:
        """Measure the multiple-testing inflation of a best-of-N headline.

        Computes, from the excess returns of every attempt that contributed to
        the champion, the expected best-of-N excess under a normal null with
        the observed mean and standard deviation, and reports how much of the
        champion is selection rather than signal.
        """
        n = len(excess_returns)
        if n < 2:
            raise ValueError("at least two excess-return samples are required")
        best = max(excess_returns)
        mean = sum(excess_returns) / n
        std = _std(excess_returns)
        expected_max_std = expected_max_of_normal_normals(n)
        expected_best_null = mean + std * expected_max_std
        inflation = expected_best_null - mean
        adjusted = best - inflation
        return SelectionBiasReport(
            n_experiments=n,
            champion_excess_pct=round(best, 6),
            mean_excess_pct=round(mean, 6),
            std_excess_pct=round(std, 6),
            expected_best_null_pct=round(expected_best_null, 6),
            selection_inflation_pct=round(inflation, 6),
            adjusted_excess_pct=round(adjusted, 6),
            survives=adjusted > 0.0,
        )


def _paired(first: Sequence[float], second: Sequence[float]) -> list[tuple[float, float]]:
    return list(zip(first, second, strict=True))


def _positive_at(points: Sequence[ExpenseStressPoint], multiplier: float) -> bool:
    for point in points:
        if abs(point.multiplier - multiplier) < 1e-9:
            return point.excess_return_pct > 0.0
    return False


def _std(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    total = sum((v - mean) ** 2 for v in values)
    return math.sqrt(total / (len(values) - 1))
