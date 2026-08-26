"""Tests for robustness and multiple-testing controls (P1-008).

The harness must guarantee:

1. Parameter perturbation scores every variant on the same shared price/cost
   world, picks the champion from the same ruler, and reports how many nearby
   choices kept a positive excess.
2. Cost and slippage stress re-run the *same* strategy under escalating
   assumptions; nothing else changes between points.
3. Selection bias is reported honestly: the best-of-N headline is compared
   with the expected best-of-N under a null, so the inflated part is stated.
"""

from __future__ import annotations

import pytest
from backend.application.research.baseline_evaluation import (
    AlwaysFlatBaseline,
    EvaluationCosts,
    MomentumBaseline,
)
from backend.application.research.robustness import RobustnessRunner


def rising_prices(n: int = 120, step: float = 0.5, start: float = 100.0) -> list[float]:
    return [start + i * step for i in range(n)]


class MomentumVariant:
    def __init__(self, label: str, lookback: int) -> None:
        self.label = label
        self._lookback = lookback

    def strategy(self):
        return MomentumBaseline(lookback=self._lookback)


def momentum_variants() -> list[MomentumVariant]:
    return [MomentumVariant(f"mom-{lb}", lb) for lb in (5, 7, 10, 12, 15, 20)]


class TestPerturbation:
    def test_champion_from_shared_world(self):
        runner = RobustnessRunner(EvaluationCosts.realistic())
        report = runner.perturb(variants=momentum_variants(), prices=rising_prices())
        assert report.variant_count == 6
        assert report.champion_label in {v.label for v in momentum_variants()}
        assert report.champion_excess_pct == max(o.excess_return_pct for o in report.outcomes)
        # Every outcome shares the same prices and cost model.
        assert len({o.result.buy_and_hold_return_pct for o in report.outcomes}) == 1
        assert len({o.result.starting_equity for o in report.outcomes}) == 1

    def test_positive_variants_counted(self):
        runner = RobustnessRunner(EvaluationCosts.free())
        report = runner.perturb(variants=momentum_variants(), prices=rising_prices())
        assert report.positive_variants == sum(
            1 for o in report.outcomes if o.excess_return_pct > 0
        )
        assert report.positive_fraction == round(report.positive_variants / report.variant_count, 6)

    def test_robust_flag_reflects_majority(self):
        runner = RobustnessRunner(EvaluationCosts.realistic())
        report = runner.perturb(variants=momentum_variants(), prices=rising_prices())
        assert report.robust == (report.positive_fraction >= 0.6)

    def test_requires_variants(self):
        runner = RobustnessRunner()
        with pytest.raises(ValueError):
            runner.perturb(variants=[], prices=rising_prices())

    def test_deterministic(self):
        runner = RobustnessRunner(EvaluationCosts.realistic())
        a = runner.perturb(variants=momentum_variants(), prices=rising_prices())
        b = runner.perturb(variants=momentum_variants(), prices=rising_prices())
        assert a.as_dict() == b.as_dict()


class TestExpenseStress:
    def test_cost_axis_scales_both_directions(self):
        runner = RobustnessRunner(EvaluationCosts.realistic())
        report = runner.expense_stress(
            strategy=MomentumBaseline(10), prices=rising_prices(200, step=0.5)
        )
        assert [p.multiplier for p in report.cost_axis] == [1.0, 2.0, 5.0, 10.0]
        # Higher costs must never *increase* the excess that already exists.
        for i in range(1, len(report.cost_axis)):
            assert report.cost_axis[i].transaction_cost_pct >= (
                report.cost_axis[i - 1].transaction_cost_pct - 1e-9
            )

    def test_slippage_axis_scales_only_half_spread(self):
        runner = RobustnessRunner(EvaluationCosts.realistic())
        report = runner.expense_stress(
            strategy=MomentumBaseline(10), prices=rising_prices(200, step=0.5)
        )
        assert [p.multiplier for p in report.slippage_axis] == [1.0, 2.0, 5.0, 10.0]
        cost_at_2x = next(p for p in report.cost_axis if p.multiplier == 2.0)
        slip_at_2x = next(p for p in report.slippage_axis if p.multiplier == 2.0)
        # Slippage-only doubles the half-spread; the cost axis doubles both
        # spread and fee, so cost-axis costs must dominate slippage-axis costs.
        assert cost_at_2x.transaction_cost_pct >= slip_at_2x.transaction_cost_pct - 1e-9

    def test_survives_2x_flags(self):
        runner = RobustnessRunner(EvaluationCosts.realistic())
        report = runner.expense_stress(
            strategy=MomentumBaseline(10), prices=rising_prices(200, step=0.5)
        )
        assert report.survives_2x_cost == any(
            p.multiplier == 2.0 and p.excess_return_pct > 0 for p in report.cost_axis
        )
        assert report.survives_2x_slippage == any(
            p.multiplier == 2.0 and p.excess_return_pct > 0 for p in report.slippage_axis
        )

    def test_flat_strategy_never_survives_2x(self):
        runner = RobustnessRunner(EvaluationCosts.realistic())
        report = runner.expense_stress(strategy=AlwaysFlatBaseline(), prices=rising_prices())
        # Zero edge everywhere: the 2x flags must be False.
        assert all(p.excess_return_pct <= 0 for p in report.cost_axis)
        assert not report.survives_2x_cost

    def test_requires_multipliers(self):
        runner = RobustnessRunner()
        with pytest.raises(ValueError):
            runner.expense_stress(
                strategy=MomentumBaseline(10),
                prices=rising_prices(),
                cost_multipliers=[],
            )


class TestSelectionBias:
    def test_best_of_n_corrected(self):
        runner = RobustnessRunner()
        excess = [1.0, 0.8, 1.2, 0.9, 1.1]
        report = runner.selection_bias(excess_returns=excess)
        assert report.n_experiments == 5
        assert report.champion_excess_pct == 1.2
        # Selection inflates the headline: expected best-of-N under the null
        # exceeds the mean, and the adjusted excess is below the champion.
        assert report.expected_best_null_pct > report.mean_excess_pct
        assert report.selection_inflation_pct > 0
        assert report.adjusted_excess_pct < report.champion_excess_pct
        assert report.survives == (report.adjusted_excess_pct > 0)

    def test_more_tries_more_inflation(self):
        runner = RobustnessRunner()
        few = runner.selection_bias(excess_returns=[2.0] * 5 + [3.0])
        many = runner.selection_bias(excess_returns=[2.0] * 5 + [3.0] * 5 + [9.0])
        assert many.n_experiments > few.n_experiments
        # A larger N with the same dispersion leans on a larger correction.
        assert many.selection_inflation_pct >= few.selection_inflation_pct - 1e-9

    def test_all_negative_never_survives(self):
        runner = RobustnessRunner()
        report = runner.selection_bias(excess_returns=[-1.0, -0.5, -0.8])
        assert report.champion_excess_pct < 0
        assert report.survives is False

    def test_requires_two_samples(self):
        runner = RobustnessRunner()
        with pytest.raises(ValueError):
            runner.selection_bias(excess_returns=[1.0])

    def test_as_dict_round_trip(self):
        runner = RobustnessRunner()
        report = runner.selection_bias(excess_returns=[0.5, 0.7, 0.9, 1.1, 0.4])
        payload = report.as_dict()
        assert payload["n_experiments"] == 5
        assert payload["survives"] == report.survives
