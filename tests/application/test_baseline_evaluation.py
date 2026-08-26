"""Tests for the baseline strategy evaluation suite (P1-003).

The suite must guarantee:

1. Simple baselines exist and are causal (a target never uses the bar it
   trades).
2. Every transition pays realistic costs — buy-and-hold is *not* free.
3. Results are comparable: the same cost model and the same price series for
   every strategy, and every result carries the costed reference return.
4. The harness is deterministic.
"""

from __future__ import annotations

import pytest
from backend.application.research.baseline_evaluation import (
    AlwaysFlatBaseline,
    BaselineEvaluator,
    BuyAndHoldBaseline,
    EvaluationCosts,
    MomentumBaseline,
    MovingAverageCrossoverBaseline,
    compare_strategies,
)


def rising_prices(n: int = 100, step: float = 1.0, start: float = 100.0) -> list[float]:
    return [start + i * step for i in range(n)]


def falling_prices(n: int = 100, step: float = 1.0, start: float = 200.0) -> list[float]:
    return [start - i * step for i in range(n)]


@pytest.fixture
def evaluator() -> BaselineEvaluator:
    return BaselineEvaluator(EvaluationCosts.free())


class TestBaselines:
    def test_always_flat_is_flat(self, evaluator):
        result = evaluator.evaluate(strategy=AlwaysFlatBaseline(), prices=rising_prices())
        assert result.num_trades == 0
        assert result.final_equity == result.starting_equity
        assert result.total_return_pct == 0.0
        assert result.sample_exposure_pct == 0.0

    def test_buy_and_hold_direction(self, evaluator):
        up = evaluator.evaluate(strategy=BuyAndHoldBaseline(), prices=rising_prices())
        assert up.total_return_pct > 0
        down = evaluator.evaluate(strategy=BuyAndHoldBaseline(), prices=falling_prices())
        assert down.total_return_pct < 0

    def test_always_flat_reference_excess(self, evaluator):
        up = evaluator.evaluate(strategy=AlwaysFlatBaseline(), prices=rising_prices())
        # Flat underperforms every rising buy-and-hold.
        assert up.excess_return_pct < 0
        # And the reference is the market move itself.
        assert up.buy_and_hold_return_pct > 0

    def test_crossover_is_causal(self, evaluator):
        baseline = MovingAverageCrossoverBaseline(fast=3, slow=10)
        n = 50
        targets = baseline.targets(rising_prices(n))
        # Before the slow window is full the strategy is flat.
        assert targets[0] == 0.0
        assert targets[8] == 0.0
        assert targets[9] == 1.0
        # Trading the same series that informed the target cannot leak:
        # recompute with a different tail and the prefix must not change.
        prefix = list(targets[:25])
        tail = rising_prices(n)[:25] + [500.0] * 25
        recomputed = list(baseline.targets(tail))
        assert recomputed[:25] == prefix

    def test_ma_crossover_follows_uptrend(self):
        costs = EvaluationCosts.free()
        evaluator = BaselineEvaluator(costs)
        prices = rising_prices(200, step=0.5)
        result = evaluator.evaluate(
            strategy=MovingAverageCrossoverBaseline(fast=5, slow=20), prices=prices
        )
        assert result.total_return_pct > 0
        assert result.num_trades >= 1

    def test_momentum_downside_is_short(self):
        costs = EvaluationCosts.free()
        evaluator = BaselineEvaluator(costs)
        prices = falling_prices(200, step=0.5)
        result = evaluator.evaluate(strategy=MomentumBaseline(lookback=10), prices=prices)
        # A persistent downtrend and a short-capable momentum should profit.
        assert result.total_return_pct > 0

    def test_baseline_window_validation(self):
        with pytest.raises(ValueError):
            MovingAverageCrossoverBaseline(fast=0, slow=10)
        with pytest.raises(ValueError):
            MovingAverageCrossoverBaseline(fast=10, slow=5)
        with pytest.raises(ValueError):
            MomentumBaseline(lookback=0)


class TestCosts:
    def test_costs_reduce_round_trip(self):
        free = BaselineEvaluator(EvaluationCosts.free())
        costed = BaselineEvaluator(EvaluationCosts.realistic())
        flat = AlwaysFlatBaseline()
        free_result = free.evaluate(strategy=flat, prices=rising_prices())
        costed_result = costed.evaluate(strategy=flat, prices=rising_prices())
        assert costed_result.transaction_cost_pct == 0.0
        # A flat strategy pays zero regardless of the cost model.
        assert free_result.final_equity == costed_result.final_equity

    def test_trading_pays_costs(self):
        free = BaselineEvaluator(EvaluationCosts.free())
        costed = BaselineEvaluator(EvaluationCosts.realistic())
        prices = rising_prices(200, step=0.5)
        free_result = free.evaluate(strategy=MomentumBaseline(10), prices=prices)
        costed_result = costed.evaluate(strategy=MomentumBaseline(10), prices=prices)
        assert costed_result.num_trades == free_result.num_trades
        # Costs always reduce net return for a strategy that traded.
        assert costed_result.total_return_pct < free_result.total_return_pct
        assert costed_result.transaction_cost_pct > 0
        assert costed_result.num_trades > 0

    def test_whiplash_bleeds_costs(self):
        # Alternating up/down prices force repeated position flips.
        costs = EvaluationCosts.realistic()
        evaluator = BaselineEvaluator(costs)
        whiplash = []
        for i in range(100):
            whiplash.append(101.0 if i % 2 == 0 else 99.0)
        result = evaluator.evaluate(strategy=MomentumBaseline(3), prices=whiplash)
        assert result.num_trades >= 10
        assert result.transaction_cost_pct > 0


class TestComparison:
    def test_always_includes_references(self):
        prices = rising_prices(150, step=0.5)
        results = compare_strategies(prices=prices, strategies=[MomentumBaseline(10)])
        names = {r.name for r in results}
        assert "momentum" in names
        assert "buy_and_hold" in names
        # Sorted by excess return, highest first.
        excess = [r.excess_return_pct for r in results]
        assert excess == sorted(excess, reverse=True)

    def test_results_share_price_and_cost_world(self):
        prices = rising_prices(150, step=0.5)
        results = compare_strategies(
            prices=prices,
            strategies=[MomentumBaseline(10), MovingAverageCrossoverBaseline(5, 20)],
        )
        references = {r.buy_and_hold_return_pct for r in results}
        # Every result compares against the identical costed reference.
        assert len(references) == 1

    def test_deterministic(self):
        prices = [100 + (i % 7) * 2 for i in range(120)]
        a = compare_strategies(prices=prices, strategies=[MomentumBaseline(5)])
        b = compare_strategies(prices=prices, strategies=[MomentumBaseline(5)])
        assert [r.as_dict()["equity_curve"] for r in a] == [r.as_dict()["equity_curve"] for r in b]


class TestValidation:
    def test_rejects_flat_or_negative_prices(self, evaluator):
        with pytest.raises(ValueError):
            evaluator.evaluate(strategy=BuyAndHoldBaseline(), prices=[100.0, 0.0])
        with pytest.raises(ValueError):
            evaluator.evaluate(strategy=BuyAndHoldBaseline(), prices=[100.0, -1.0])

    def test_rejects_bad_starting_equity(self, evaluator):
        with pytest.raises(ValueError):
            evaluator.evaluate(
                strategy=BuyAndHoldBaseline(), prices=rising_prices(), starting_equity=0
            )

    def test_rejects_bad_target(self):
        class BadTargets:
            name = "bad"

            def targets(self, prices):
                return [7.0 for _ in prices]

            def describe(self):
                return "bad"

        with pytest.raises(ValueError):
            BaselineEvaluator(EvaluationCosts.free()).evaluate(
                strategy=BadTargets(), prices=rising_prices()
            )
