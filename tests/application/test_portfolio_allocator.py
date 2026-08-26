"""Tests for correlation-aware portfolio allocation (T2-14-1).

The allocation must discount redundancy (a correlated pair's combined
claim shrinks, an independent strategy's claim grows), preserve score
ratios inside a correlated pair, and refuse to allocate when the
correlation surface is incomplete.
"""

from __future__ import annotations

import pytest
from backend.application.research.portfolio_allocator import allocate_correlation_damped
from backend.domain.research.portfolio_allocator import AllocatedWeight, PortfolioAllocation
from backend.domain.research.portfolio_correlations import PortfolioCorrelationMatrix


def matrix(
    ids: list[str], correlations: list[tuple[str, str, float]]
) -> PortfolioCorrelationMatrix:
    n = len(ids)
    rows = [[0.0] * n for _ in range(n)]
    for index, _strategy_id in enumerate(ids):
        rows[index][index] = 1.0
    for left, right, rho in correlations:
        i, j = ids.index(left), ids.index(right)
        rows[i][j] = rho
        rows[j][i] = rho
    return PortfolioCorrelationMatrix(
        ids=tuple(ids), matrix=tuple(tuple(row) for row in rows), pairs=()
    )


def by_id(allocation: PortfolioAllocation) -> dict[str, AllocatedWeight]:
    return {weight.strategy_id: weight for weight in allocation.weights}


class TestCorrelationDamping:
    def test_uncorrelated_strategies_keep_score_weights(self) -> None:
        m = matrix(["a", "b", "c"], [("a", "b", 0.0), ("a", "c", 0.0), ("b", "c", 0.0)])
        allocation = allocate_correlation_damped({"a": 2.0, "b": 1.0, "c": 1.0}, m)
        weights = by_id(allocation)
        assert weights["a"].weight == pytest.approx(0.5)
        assert weights["b"].weight == pytest.approx(0.25)
        assert weights["c"].weight == pytest.approx(0.25)
        assert all(weight.dampening == pytest.approx(1.0) for weight in allocation.weights)
        assert sum(weight.weight for weight in allocation.weights) == pytest.approx(1.0)

    def test_correlated_pair_is_discounted_together(self) -> None:
        m = matrix(["a", "b", "c"], [("a", "b", 1.0), ("a", "c", 0.0), ("b", "c", 0.0)])
        allocation = allocate_correlation_damped({"a": 2.0, "b": 1.0, "c": 1.0}, m)
        weights = by_id(allocation)
        # redundant pair loses weight, independent c gains
        assert weights["a"].weight + weights["b"].weight == pytest.approx(2.0 / 3.0)
        assert weights["a"].weight == pytest.approx(4.0 / 9.0)
        assert weights["b"].weight == pytest.approx(2.0 / 9.0)
        assert weights["c"].weight == pytest.approx(1.0 / 3.0)
        # symmetric damping preserves the 2:1 score ratio inside the pair
        assert weights["a"].dampening == pytest.approx(weights["b"].dampening)
        # c carries no correlation load; a and b carry each other's weight
        assert weights["c"].correlation_load == pytest.approx(0.0)
        assert weights["a"].correlation_load == pytest.approx(weights["b"].weight)

    def test_partial_correlation_discounts_less(self) -> None:
        full = matrix(["a", "b", "c"], [("a", "b", 1.0), ("a", "c", 0.0), ("b", "c", 0.0)])
        half = matrix(["a", "b", "c"], [("a", "b", 0.5), ("a", "c", 0.0), ("b", "c", 0.0)])
        full_w = by_id(allocate_correlation_damped({"a": 1.0, "b": 1.0, "c": 1.0}, full))
        half_w = by_id(allocate_correlation_damped({"a": 1.0, "b": 1.0, "c": 1.0}, half))
        assert half_w["a"].weight > full_w["a"].weight
        assert half_w["a"].dampening > full_w["a"].dampening

    def test_sensitivity_strengthens_the_discount(self) -> None:
        m = matrix(["a", "b"], [("a", "b", 1.0)])
        mild = by_id(
            allocate_correlation_damped({"a": 1.0, "b": 1.0}, m, correlation_sensitivity=0.5)
        )
        strong = by_id(
            allocate_correlation_damped({"a": 1.0, "b": 1.0}, m, correlation_sensitivity=4.0)
        )
        assert strong["a"].dampening < mild["a"].dampening

    def test_negative_correlation_never_counts_against(self) -> None:
        m = matrix(["a", "b"], [("a", "b", -1.0)])
        allocation = allocate_correlation_damped({"a": 1.0, "b": 1.0}, m)
        weights = by_id(allocation)
        assert weights["a"].dampening == pytest.approx(1.0)
        assert weights["a"].correlation_load == pytest.approx(-0.5)


class TestEdgeCases:
    def test_single_strategy_gets_all(self) -> None:
        m = matrix(["a"], [])
        allocation = allocate_correlation_damped({"a": 3.0}, m)
        assert len(allocation.weights) == 1
        assert allocation.weights[0].weight == pytest.approx(1.0)
        assert allocation.weights[0].dampening == pytest.approx(1.0)

    def test_zero_score_strategy_is_excluded_with_record(self) -> None:
        m = matrix(["a", "b"], [("a", "b", 1.0)])
        allocation = allocate_correlation_damped({"a": 1.0, "b": 0.0}, m)
        weights = by_id(allocation)
        assert weights["b"].weight == 0.0
        assert weights["a"].weight == pytest.approx(1.0)

    def test_scored_strategy_missing_from_matrix_refused(self) -> None:
        m = matrix(["a", "b"], [("a", "b", 0.0)])
        with pytest.raises(ValueError, match="missing from the correlation matrix"):
            allocate_correlation_damped({"a": 1.0, "b": 1.0, "c": 1.0}, m)

    def test_no_positive_scores_refused(self) -> None:
        m = matrix(["a", "b"], [("a", "b", 0.0)])
        with pytest.raises(ValueError, match="no positive scores"):
            allocate_correlation_damped({"a": 0.0, "b": 0.0}, m)

    def test_bad_scores_refused(self) -> None:
        m = matrix(["a"], [])
        with pytest.raises(ValueError):
            allocate_correlation_damped({"a": -1.0}, m)
        with pytest.raises(ValueError):
            allocate_correlation_damped({"a": float("nan")}, m)
        with pytest.raises(ValueError):
            allocate_correlation_damped({}, m)

    def test_negative_sensitivity_refused(self) -> None:
        m = matrix(["a"], [])
        with pytest.raises(ValueError):
            allocate_correlation_damped({"a": 1.0}, m, correlation_sensitivity=-0.1)

    def test_serialisation_roundtrip(self) -> None:
        m = matrix(["a", "b"], [("a", "b", 1.0)])
        payload = allocate_correlation_damped({"a": 1.0, "b": 1.0}, m).as_dict()
        assert payload["correlation_sensitivity"] == 1.0
        assert [w["strategy_id"] for w in payload["weights"]] == ["a", "b"]
        assert set(payload["weights"][0]) == {
            "strategy_id",
            "score",
            "weight",
            "dampening",
            "correlation_load",
        }
