"""Unit tests for the scenario engine (task P3-001).

The acceptance criteria are:
1. Multiple scenarios are generated.
2. Probabilities are calibrated (analytic and empirically verified).
3. Expected value includes costs (a trade only when net expectation is
   positive).
4. Abstention is supported (an explicit, tested decision).
"""

from __future__ import annotations

import pytest
from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.scenario_engine import (
    ScenarioEngine,
    build_scenario_set,
    round_trip_cost_pct,
)
from backend.domain.research.scenario import (
    ScenarioAction,
    ScenarioDecision,
    ScenarioSet,
)

# --- multiple scenarios ---------------------------------------------


def test_multiple_scenarios_are_generated() -> None:
    for n in (3, 5, 7, 9):
        scenario_set = build_scenario_set(vol_pct=1.0, horizon=10, n_points=n)
        assert len(scenario_set.scenarios) == n


def test_scenario_set_structure() -> None:
    scenario_set = build_scenario_set(vol_pct=1.0, horizon=10, n_points=5)
    assert isinstance(scenario_set, ScenarioSet)
    assert scenario_set.horizon == 10
    assert scenario_set.expected_move_pct == 0.0
    for scenario in scenario_set.scenarios:
        assert scenario.label
        assert 0.0 < scenario.probability <= 1.0
        assert scenario.return_pct > float("-inf")


# --- calibrated probabilities ---------------------------------------


def test_probabilities_sum_to_one() -> None:
    for n in (3, 5, 7, 9):
        scenario_set = build_scenario_set(vol_pct=2.0, horizon=10, n_points=n)
        assert scenario_set.total_probability == pytest.approx(1.0, abs=1e-8)


def test_returns_are_symmetric_under_zero_drift() -> None:
    scenario_set = build_scenario_set(vol_pct=2.0, horizon=10, n_points=5)
    returns = [s.return_pct for s in scenario_set.scenarios]
    assert returns[0] == pytest.approx(-returns[-1], abs=1e-8)
    assert returns[1] == pytest.approx(-returns[-2], abs=1e-8)
    assert returns[2] == pytest.approx(0.0, abs=1e-8)


def test_extreme_bucket_has_larger_move_than_central() -> None:
    scenario_set = build_scenario_set(vol_pct=2.0, horizon=10, n_points=5)
    returns = [s.return_pct for s in scenario_set.scenarios]
    assert abs(returns[-1]) > abs(returns[-2]) > abs(returns[-3])


def test_drift_shifts_returns_but_not_probabilities() -> None:
    zero = build_scenario_set(vol_pct=1.0, horizon=5, n_points=5)
    drift = build_scenario_set(vol_pct=1.0, horizon=5, expected_move_pct=0.5, n_points=5)
    for z, d in zip(zero.scenarios, drift.scenarios, strict=True):
        assert d.probability == pytest.approx(z.probability)
        assert d.return_pct == pytest.approx(z.return_pct + 0.5)


def test_calibration_empirically_matches_probabilities() -> None:
    engine = ScenarioEngine()
    report = engine.calibration_report(vol_pct=2.0, horizon=10, n_points=5, draws=200_000, seed=7)
    assert report["max_abs_error"] < 0.01
    assert report["brier_score"] < 1e-4
    assert report["draws"] == 200_000
    assert len(report["buckets"]) == 5


def test_calibration_report_is_deterministic_with_seed() -> None:
    engine = ScenarioEngine()
    a = engine.calibration_report(vol_pct=1.0, horizon=5, n_points=7, seed=3)
    b = engine.calibration_report(vol_pct=1.0, horizon=5, n_points=7, seed=3)
    assert a == b


# --- input validation -----------------------------------------------


def test_invalid_inputs_are_rejected() -> None:
    with pytest.raises(ValueError):
        build_scenario_set(vol_pct=-1.0, horizon=10)
    with pytest.raises(ValueError):
        build_scenario_set(vol_pct=float("nan"), horizon=10)
    with pytest.raises(ValueError):
        build_scenario_set(vol_pct=1.0, horizon=0)
    with pytest.raises(ValueError):
        build_scenario_set(vol_pct=1.0, horizon=2, n_points=2)


def test_zero_vol_returns_drift_only() -> None:
    scenario_set = build_scenario_set(vol_pct=0.0, horizon=10, expected_move_pct=0.3, n_points=5)
    assert scenario_set.total_probability == pytest.approx(1.0, abs=1e-8)
    for scenario in scenario_set.scenarios:
        assert scenario.return_pct == pytest.approx(0.3)


# --- expected value includes costs ----------------------------------


def test_round_trip_cost_matches_realistic_cost_model() -> None:
    costs = EvaluationCosts.realistic()
    assert round_trip_cost_pct(costs) == pytest.approx(2.0 * (0.0002 + 0.0004) * 100.0)


def test_zero_drift_long_abstains_because_of_costs() -> None:
    engine = ScenarioEngine()
    evaluation = engine.evaluate(action=ScenarioAction.LONG, vol_pct=2.0, horizon=10)
    assert evaluation.gross_expected_value_pct == pytest.approx(0.0, abs=1e-7)
    assert evaluation.expected_value_pct == pytest.approx(-round_trip_cost_pct(engine.costs))
    assert evaluation.decision is ScenarioDecision.ABSTAIN


def test_positive_drift_long_trades_when_expected_value_clears_costs() -> None:
    costs = EvaluationCosts.realistic()
    engine = ScenarioEngine(costs)
    threshold = round_trip_cost_pct(costs) + 0.01
    evaluation = engine.evaluate(
        action=ScenarioAction.LONG, vol_pct=0.5, horizon=10, expected_move_pct=threshold
    )
    assert evaluation.decision is ScenarioDecision.TRADE_LONG
    assert evaluation.expected_value_pct == pytest.approx(threshold - round_trip_cost_pct(costs))


def test_negative_drift_short_trades_when_expected_value_clears_costs() -> None:
    engine = ScenarioEngine()
    costs = engine.costs
    evaluation = engine.evaluate(
        action=ScenarioAction.SHORT, vol_pct=0.5, horizon=10, expected_move_pct=-1.0
    )
    assert evaluation.decision is ScenarioDecision.TRADE_SHORT
    assert evaluation.expected_value_pct == pytest.approx(
        1.0 - round_trip_cost_pct(costs), abs=1e-6
    )


def test_expected_value_is_probability_weighted_sum() -> None:
    engine = ScenarioEngine()
    evaluation = engine.evaluate(action=ScenarioAction.LONG, vol_pct=2.0, horizon=10)
    expectant = sum(s.return_pct * s.probability for s in evaluation.scenarios.scenarios)
    assert evaluation.gross_expected_value_pct == pytest.approx(expectant, abs=1e-6)


# --- abstention -----------------------------------------------------


def test_flat_action_always_abstains_with_zero_cost() -> None:
    engine = ScenarioEngine()
    evaluation = engine.evaluate(action=ScenarioAction.FLAT, vol_pct=2.0, horizon=10)
    assert evaluation.decision is ScenarioDecision.ABSTAIN
    assert evaluation.round_trip_cost_pct == pytest.approx(0.0)
    assert evaluation.expected_value_pct == pytest.approx(0.0)


def test_best_action_abstains_when_neither_side_clears_costs() -> None:
    engine = ScenarioEngine()
    evaluation = engine.best_action(vol_pct=5.0, horizon=10)
    assert evaluation.decision is ScenarioDecision.ABSTAIN
    assert evaluation.expected_value_pct < 0.0


def test_best_action_picks_net_positive_side() -> None:
    engine = ScenarioEngine()
    evaluation = engine.best_action(vol_pct=0.5, horizon=10, expected_move_pct=1.5)
    assert evaluation.decision is ScenarioDecision.TRADE_LONG
    assert evaluation.expected_value_pct == pytest.approx(
        1.5 - round_trip_cost_pct(engine.costs), abs=1e-6
    )


def test_best_action_short_wins_on_negative_drift() -> None:
    engine = ScenarioEngine()
    evaluation = engine.best_action(vol_pct=0.5, horizon=10, expected_move_pct=-1.5)
    assert evaluation.decision is ScenarioDecision.TRADE_SHORT


# --- as_dict round-trip ---------------------------------------------


def test_scenario_evaluation_as_dict() -> None:
    engine = ScenarioEngine()
    evaluation = engine.evaluate(action=ScenarioAction.LONG, vol_pct=2.0, horizon=10)
    data = evaluation.as_dict()
    assert data["action"] == "long"
    assert data["decision"] == "abstain"
    assert data["expected_value_pct"] == pytest.approx(evaluation.expected_value_pct)
    assert set(data.keys()) == {
        "action",
        "decision",
        "scenarios",
        "gross_expected_value_pct",
        "round_trip_cost_pct",
        "expected_value_pct",
        "per_scenario_gross_pct",
        "per_scenario_net_pct",
    }


def test_scenario_set_as_dict() -> None:
    scenario_set = build_scenario_set(vol_pct=1.0, horizon=10, n_points=5)
    data = scenario_set.as_dict()
    assert data["horizon"] == 10
    assert len(data["scenarios"]) == 5
    assert all({"label", "return_pct", "probability"} <= set(s.keys()) for s in data["scenarios"])
