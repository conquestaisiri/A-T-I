"""Unit tests for the strategy allocator (task P3-003).

The acceptance criteria are:
1. Strategies compete for risk budget.
2. Correlation and regime fit are included in the allocation.
3. The allocator cannot bypass the risk gate.
"""

from __future__ import annotations

import math
from typing import Any

import pytest
from backend.application.research.strategy_allocator import (
    AllocationConfig,
    allocate_strategies,
)
from backend.domain.research.allocation import StrategyProfile


def _profile(name: str, expected: float, vol: float, fit: float = 1.0) -> StrategyProfile:
    return StrategyProfile(
        name=name, expected_return_pct=expected, volatility_pct=vol, regime_fit=fit
    )


# --- strategies compete for the risk budget -------------------------


def test_single_strategy_takes_the_full_budget() -> None:
    result = allocate_strategies(strategies=(_profile("a", 5.0, 10.0),), risk_budget_pct=10.0)
    assert result.status == "allocated"
    assert result.weight_for("a") == pytest.approx(1.0)
    assert result.portfolio_volatility_pct == pytest.approx(10.0, abs=1e-6)


def test_equal_risk_parity_yields_equal_weights() -> None:
    result = allocate_strategies(
        strategies=(_profile("a", 5.0, 10.0), _profile("b", 5.0, 10.0)),
        risk_budget_pct=10.0,
    )
    assert result.weight_for("a") == pytest.approx(result.weight_for("b"), abs=1e-6)
    assert result.weight_for("a") == pytest.approx(math.sqrt(0.5), abs=1e-6)
    assert result.portfolio_volatility_pct == pytest.approx(10.0, abs=1e-6)


def test_higher_volatility_earns_less_budget() -> None:
    result = allocate_strategies(
        strategies=(_profile("risky", 10.0, 20.0), _profile("calm", 5.0, 10.0)),
        risk_budget_pct=10.0,
    )
    assert result.weight_for("calm") == pytest.approx(2 * result.weight_for("risky"), abs=1e-5)
    assert result.weight_for("calm") == pytest.approx(math.sqrt(0.5), abs=1e-6)
    assert result.weight_for("risky") == pytest.approx(math.sqrt(0.5) / 2, abs=1e-6)


def test_allocations_sorted_by_weight_descending() -> None:
    result = allocate_strategies(
        strategies=(_profile("risky", 10.0, 20.0), _profile("calm", 5.0, 10.0)),
        risk_budget_pct=10.0,
    )
    weights = [a.weight for a in result.allocations]
    assert weights == sorted(weights, reverse=True)


def test_weights_are_non_negative_and_positive_when_allocated() -> None:
    result = allocate_strategies(
        strategies=(_profile("a", 5.0, 10.0), _profile("b", -2.0, 30.0)),
        risk_budget_pct=10.0,
    )
    assert all(a.weight >= 0.0 for a in result.allocations)
    assert sum(a.weight for a in result.allocations) > 0.0


# --- correlation and regime fit are included ------------------------


def test_positive_correlation_holds_back_allocations() -> None:
    correlated = allocate_strategies(
        strategies=(_profile("a", 5.0, 10.0), _profile("b", 5.0, 10.0)),
        risk_budget_pct=10.0,
        correlations=((1.0, 1.0), (1.0, 1.0)),
    )
    uncorrelated = allocate_strategies(
        strategies=(_profile("a", 5.0, 10.0), _profile("b", 5.0, 10.0)),
        risk_budget_pct=10.0,
    )
    assert correlated.weight_for("a") == pytest.approx(0.5)
    assert uncorrelated.weight_for("a") == pytest.approx(math.sqrt(0.5), abs=1e-6)
    assert correlated.portfolio_volatility_pct == pytest.approx(10.0, abs=1e-6)
    assert uncorrelated.portfolio_volatility_pct == pytest.approx(10.0, abs=1e-6)


def test_regime_fit_reweights_the_competition() -> None:
    result = allocate_strategies(
        strategies=(
            _profile("good_fit", 5.0, 10.0, fit=1.0),
            _profile("poor_fit", 5.0, 10.0, fit=0.5),
        ),
        risk_budget_pct=10.0,
    )
    assert result.weight_for("good_fit") == pytest.approx(
        2 * result.weight_for("poor_fit"), abs=1e-5
    )


def test_min_regime_fit_eliminates_strategies() -> None:
    result = allocate_strategies(
        strategies=(
            _profile("good", 5.0, 10.0, fit=1.0),
            _profile("bad", 5.0, 10.0, fit=0.5),
        ),
        risk_budget_pct=10.0,
        config=AllocationConfig(min_regime_fit=0.8),
    )
    assert result.weight_for("good") == pytest.approx(1.0, abs=1e-6)
    assert result.weight_for("bad") == pytest.approx(0.0)


def test_portfolio_volatility_never_exceeds_budget() -> None:
    budgets = (2.0, 5.0, 12.0, 25.0)
    for budget in budgets:
        result = allocate_strategies(
            strategies=(
                _profile("a", 5.0, 8.0),
                _profile("b", 5.0, 15.0),
                _profile("c", 5.0, 22.0, fit=0.7),
            ),
            risk_budget_pct=budget,
            correlations=((1.0, 0.3, 0.1), (0.3, 1.0, -0.2), (0.1, -0.2, 1.0)),
        )
        assert result.portfolio_volatility_pct <= budget + 1e-6


def test_portfolio_expected_return_is_weighted_sum() -> None:
    profiles = (_profile("a", 5.0, 10.0), _profile("b", 10.0, 20.0))
    result = allocate_strategies(strategies=profiles, risk_budget_pct=10.0)
    expected = sum(
        a.weight * p.expected_return_pct for a, p in zip(result.allocations, profiles, strict=True)
    )
    assert result.portfolio_expected_return_pct == pytest.approx(expected, abs=1e-6)


# --- the risk gate cannot be bypassed -------------------------------


def test_risk_gate_veto_blocks_even_an_attractive_strategy() -> None:
    result = allocate_strategies(
        strategies=(_profile("gold_mine", 50.0, 1.0),),
        risk_budget_pct=10.0,
        risk_gate_allowed=False,
        blocked_reason="reconciliation mismatch",
    )
    assert result.status == "blocked"
    assert result.blocked is True
    assert result.allocations == ()
    assert result.weight_for("gold_mine") == 0.0
    assert result.portfolio_volatility_pct == 0.0
    assert result.blocked_reason == "reconciliation mismatch"


def test_risk_gate_veto_default_reason() -> None:
    result = allocate_strategies(
        strategies=(_profile("a", 5.0, 10.0),),
        risk_budget_pct=10.0,
        risk_gate_allowed=False,
    )
    assert result.blocked and result.blocked_reason == "risk gate veto"


def test_risk_gate_pass_allocates() -> None:
    result = allocate_strategies(
        strategies=(_profile("a", 5.0, 10.0),),
        risk_budget_pct=10.0,
        risk_gate_allowed=True,
    )
    assert result.status == "allocated"
    assert result.weight_for("a") == pytest.approx(1.0)


# --- validation -----------------------------------------------------


def test_input_validation() -> None:
    with pytest.raises(ValueError):
        allocate_strategies(strategies=(), risk_budget_pct=10.0)
    with pytest.raises(ValueError):
        allocate_strategies(strategies=(_profile("a", 5.0, 10.0),), risk_budget_pct=0.0)
    with pytest.raises(ValueError):
        allocate_strategies(
            strategies=(_profile("a", 5.0, 10.0), _profile("a", 5.0, 10.0)),
            risk_budget_pct=10.0,
        )
    with pytest.raises(ValueError):
        allocate_strategies(strategies=(_profile("a", 5.0, 0.0),), risk_budget_pct=10.0)
    with pytest.raises(ValueError):
        allocate_strategies(strategies=(_profile("a", 5.0, 10.0, fit=1.5),), risk_budget_pct=10.0)
    with pytest.raises(ValueError):
        allocate_strategies(strategies=(_profile("a", 5.0, 10.0, fit=-0.1),), risk_budget_pct=10.0)


def test_correlation_matrix_validation() -> None:
    profiles = (_profile("a", 5.0, 10.0), _profile("b", 5.0, 10.0))
    with pytest.raises(ValueError):
        allocate_strategies(strategies=profiles, risk_budget_pct=10.0, correlations=((1.0,),))
    with pytest.raises(ValueError):
        allocate_strategies(
            strategies=profiles, risk_budget_pct=10.0, correlations=((1.0, 1.5), (1.5, 1.0))
        )
    with pytest.raises(ValueError):
        allocate_strategies(
            strategies=profiles, risk_budget_pct=10.0, correlations=((1.0, 0.3), (0.2, 1.0))
        )
    with pytest.raises(ValueError):
        allocate_strategies(
            strategies=profiles, risk_budget_pct=10.0, correlations=((0.0, 0.3), (0.3, 1.0))
        )


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        AllocationConfig(min_regime_fit=1.5)


# --- determinism and as_dict ----------------------------------------


def test_allocation_is_deterministic() -> None:
    strategies = (_profile("a", 5.0, 10.0), _profile("b", 8.0, 15.0))
    kwargs: dict[str, Any] = dict(
        strategies=strategies,
        risk_budget_pct=10.0,
        correlations=((1.0, 0.5), (0.5, 1.0)),
    )
    first = allocate_strategies(**kwargs).as_dict()
    second = allocate_strategies(**kwargs).as_dict()
    assert first == second


def test_result_as_dict_round_trip() -> None:
    result = allocate_strategies(strategies=(_profile("a", 5.0, 10.0),), risk_budget_pct=10.0)
    data = result.as_dict()
    assert set(data.keys()) == {
        "allocations",
        "status",
        "risk_budget_pct",
        "portfolio_expected_return_pct",
        "portfolio_volatility_pct",
        "blocked_reason",
    }
    assert data["status"] == "allocated"
    assert data["allocations"][0]["strategy_name"] == "a"


def test_blocked_result_as_dict() -> None:
    result = allocate_strategies(
        strategies=(_profile("a", 5.0, 10.0),),
        risk_budget_pct=10.0,
        risk_gate_allowed=False,
    )
    data = result.as_dict()
    assert data["status"] == "blocked"
    assert data["allocations"] == []


def test_weight_for_unknown_strategy_is_zero() -> None:
    result = allocate_strategies(strategies=(_profile("a", 5.0, 10.0),), risk_budget_pct=10.0)
    assert result.weight_for("missing") == 0.0
