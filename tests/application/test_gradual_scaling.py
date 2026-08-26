# tests/application/test_gradual_scaling.py
"""Tests for the gradual post-canary capital-scaling harness (build-order #40).

The ramp turns earned production eligibility into a responsible deployment:
exposure starts at the floor, climbs by an operator-bounded step only after a
clean stay, hits the operator-bounded ceiling as a terminal state, and any
P4-001 stay-limit breach cuts straight back to the floor. The harness only
reads injected monitors; it never touches a venue.
"""

from __future__ import annotations

from typing import Any

from backend.application.research.gradual_scaling import (
    GradualScalingRunner,
    ScalingConfig,
    run_gradual_scaling,
)
from backend.domain.research.promotion import DeploymentMonitor, ModelEnvironment
from backend.domain.research.scaling import (
    ScalingAction,
    ScalingBoundary,
    ScalingProgramResult,
)


def _monitor(
    candidate_id: str,
    period: int,
    *,
    drawdown: float = 2.0,
    underperformance: float = 2.0,
    failed_orders: float = 0.0,
) -> DeploymentMonitor:
    return DeploymentMonitor(
        candidate_id=candidate_id,
        environment=ModelEnvironment.PRODUCTION,
        drawdown_pct=drawdown,
        underperformance_bps=underperformance,
        failed_orders_pct=failed_orders,
    )


class TestRamp:
    def test_starts_at_floor_and_advances_after_clean_stay(self) -> None:
        result = GradualScalingRunner(
            ScalingConfig(
                max_fraction=0.20, floor_fraction=0.05, step_fraction=0.05, hold_periods=2
            )
        ).run("model-a", lambda p: _monitor("model-a", p))
        # floor 0.05 -> advance 0.10 -> advance 0.15 -> advance 0.20 (capped)
        assert result.current_fraction == 0.20
        assert result.boundary is ScalingBoundary.CAPPED
        advances = [t for t in result.tiers if t.action is ScalingAction.ADVANCE]
        assert [t.capital_fraction for t in advances] == [0.10, 0.15, 0.20]

    def test_never_exceeds_max_fraction(self) -> None:
        result = GradualScalingRunner(
            ScalingConfig(
                max_fraction=0.11, floor_fraction=0.05, step_fraction=0.05, hold_periods=1
            )
        ).run("model-a", lambda p: _monitor("model-a", p))
        assert result.current_fraction == 0.11
        assert result.boundary is ScalingBoundary.CAPPED
        assert all(t.capital_fraction <= 0.11 for t in result.tiers)

    def test_hold_records_partial_clean_streak(self) -> None:
        # 5 clean periods with hold_periods=3: one advance at period 3, then
        # the streak restarts (periods 4-5 are holding).
        result = GradualScalingRunner(
            ScalingConfig(max_fraction=0.20, step_fraction=0.05, hold_periods=3)
        ).run("model-a", lambda p: _monitor("model-a", p), max_periods=5)
        assert result.current_fraction == 0.10
        assert result.boundary is ScalingBoundary.RAMPING
        advances = [t for t in result.tiers if t.action is ScalingAction.ADVANCE]
        assert len(advances) == 1
        assert advances[0].capital_fraction == 0.10
        assert any(t.action is ScalingAction.HOLD for t in result.tiers)

    def test_capped_is_terminal_and_recorded(self) -> None:
        result = GradualScalingRunner(
            ScalingConfig(max_fraction=0.05, floor_fraction=0.05, hold_periods=3)
        ).run("model-a", lambda p: _monitor("model-a", p))
        assert result.boundary is ScalingBoundary.CAPPED
        assert result.tiers[0].action is ScalingAction.CAPPED

    def test_still_ramping_after_max_periods(self) -> None:
        result = GradualScalingRunner(
            ScalingConfig(max_fraction=0.95, floor_fraction=0.05, hold_periods=5)
        ).run("model-a", lambda p: _monitor("model-a", p), max_periods=7)
        assert result.boundary is ScalingBoundary.RAMPING
        assert len(result.tiers) == 7


class TestRollbackOnBreach:
    def test_breach_cuts_to_floor(self) -> None:
        def monitor(period: int) -> DeploymentMonitor:
            drawdown = 3.0 if period < 4 else 20.0
            return _monitor("model-a", period, drawdown=drawdown)

        result = GradualScalingRunner(
            ScalingConfig(max_fraction=0.5, floor_fraction=0.05, step_fraction=0.05, hold_periods=3)
        ).run("model-a", monitor)
        assert result.boundary is ScalingBoundary.CUT
        assert result.current_fraction == 0.05
        assert any(t.action is ScalingAction.CUT for t in result.tiers)
        assert "drawdown" in result.reason

    def test_underperformance_breach_also_cuts(self) -> None:
        def monitor(period: int) -> DeploymentMonitor:
            return _monitor("model-a", period, underperformance=60.0)

        result = GradualScalingRunner(ScalingConfig(max_fraction=0.5, hold_periods=3)).run(
            "model-a", monitor
        )
        assert result.boundary is ScalingBoundary.CUT
        assert "underperformance" in result.reason

    def test_cut_can_happen_after_advance(self) -> None:
        # Capped is not reachable here because a breach occurs after two
        # advances but before the ceiling; exposure snaps back to the floor.
        def monitor(period: int) -> DeploymentMonitor:
            return _monitor("model-a", period, drawdown=30.0 if period >= 5 else 2.0)

        result = GradualScalingRunner(
            ScalingConfig(max_fraction=0.5, floor_fraction=0.05, hold_periods=2)
        ).run("model-a", monitor)
        assert result.boundary is ScalingBoundary.CUT
        assert result.current_fraction == 0.05
        cuts = [t for t in result.tiers if t.action is ScalingAction.CUT]
        assert len(cuts) == 1


class TestSerialization:
    def test_convenience_function_wires_the_runner(self) -> None:
        result = run_gradual_scaling(
            "model-a", lambda p: _monitor("model-a", p), config=ScalingConfig()
        )
        assert isinstance(result, ScalingProgramResult)

    def test_result_as_dict(self) -> None:
        result = GradualScalingRunner(
            ScalingConfig(max_fraction=0.20, step_fraction=0.05, hold_periods=2)
        ).run("model-a", lambda p: _monitor("model-a", p))
        payload = result.as_dict()
        assert payload["boundary"] == "capped"
        assert payload["action"] == "capped"
        assert payload["current_fraction"] == 0.20
        assert isinstance(payload["tiers"], list)
        assert payload["tiers"][0]["action"] == "hold"

    def test_tier_as_dict(self) -> None:
        result = GradualScalingRunner(
            ScalingConfig(max_fraction=0.20, step_fraction=0.05, hold_periods=1)
        ).run("model-a", lambda p: _monitor("model-a", p))
        advance = next(t for t in result.tiers if t.action is ScalingAction.ADVANCE)
        payload = advance.as_dict()
        assert payload["action"] == "advance"
        assert payload["capital_fraction"] == 0.10

    def test_cut_result_as_dict(self) -> None:
        result = GradualScalingRunner(ScalingConfig(max_fraction=0.5, hold_periods=1)).run(
            "model-a", lambda p: _monitor("model-a", p, drawdown=25.0)
        )
        payload = result.as_dict()
        assert payload["boundary"] == "cut"
        assert payload["current_fraction"] == 0.05

    def test_same_input_same_result(self) -> None:
        def run() -> dict[str, Any]:
            return (
                GradualScalingRunner(
                    ScalingConfig(max_fraction=0.20, step_fraction=0.05, hold_periods=2)
                )
                .run("model-a", lambda p: _monitor("model-a", p))
                .as_dict()
            )

        assert run() == run()
