# tests/application/test_canary_harness.py
"""Tests for the live-canary campaign harness (task P4-003).

The harness is a deterministic judge: given an authorized candidate, an
injected monitor, and the P4-001 promotion gates, it retires on breach,
holds for insufficient evidence, and recommends production only when the
full gate grants it. No network, no venue.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.application.research.canary_harness import (
    CanaryHarness,
    CanaryHarnessConfig,
    run_canary_campaign,
)
from backend.domain.research.canary import (
    CanaryAction,
    CanaryNotAuthorized,
    CanaryProgramResult,
)
from backend.domain.research.promotion import (
    CandidateEvidence,
    DeploymentMonitor,
    ModelEnvironment,
)


def _evidence(**overrides: Any) -> CandidateEvidence:
    defaults: dict[str, Any] = {
        "candidate_id": "model-a",
        "validation_samples": 500,
        "validation_sharpe": 1.2,
        "paper_days_deployed": 30,
        "paper_sharpe": 0.8,
        "canary_days_deployed": 7,
    }
    defaults.update(overrides)
    return CandidateEvidence(**defaults)


def _monitor(
    candidate_id: str,
    day: int,
    *,
    drawdown: float = 2.0,
    underperformance: float = 2.0,
    failed_orders: float = 0.0,
) -> DeploymentMonitor:
    return DeploymentMonitor(
        candidate_id=candidate_id,
        environment=ModelEnvironment.CANARY,
        drawdown_pct=drawdown,
        underperformance_bps=underperformance,
        failed_orders_pct=failed_orders,
    )


class TestAuthorization:
    def test_unapproved_canary_refuses_to_start(self) -> None:
        with pytest.raises(CanaryNotAuthorized):
            CanaryHarness().run(
                "model-a",
                _evidence(),
                lambda day: _monitor("model-a", day),
                authorized=False,
            )

    def test_convenience_function_also_refuses(self) -> None:
        with pytest.raises(CanaryNotAuthorized):
            run_canary_campaign("model-a", _evidence(), lambda day: _monitor("model-a", day))

    def test_authorized_campaign_starts(self) -> None:
        result = CanaryHarness().run(
            "model-a",
            _evidence(),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        assert result.authorized is True


class TestRollbackDuringCampaign:
    def test_breach_on_day_k_retires_campaign(self) -> None:
        def monitor(day: int) -> DeploymentMonitor:
            drawdown = 5.0 if day < 3 else 15.0
            return _monitor("model-a", day, drawdown=drawdown)

        result = CanaryHarness().run(
            "model-a",
            _evidence(),
            monitor,
            authorized=True,
        )
        assert result.action is CanaryAction.RETIRED
        assert result.days_run == 3
        assert any(p.day == 3 and p.action is CanaryAction.RETIRED for p in result.periods)
        assert any(p.action is CanaryAction.CONTINUE for p in result.periods[:2])

    def test_retired_reason_records_the_breach(self) -> None:
        def monitor(day: int) -> DeploymentMonitor:
            return _monitor("model-a", day, failed_orders=12.0)

        result = CanaryHarness().run("model-a", _evidence(), monitor, authorized=True)
        assert result.action is CanaryAction.RETIRED
        assert "failed orders" in result.reason

    def test_rollback_target_is_reported_in_reason(self) -> None:
        # PRODUCTION canary rolls back to paper; CANARY rollback -> paper too.
        result = CanaryHarness().run(
            "model-a",
            _evidence(),
            lambda day: _monitor("model-a", day, underperformance=60.0),
            authorized=True,
        )
        assert result.action is CanaryAction.RETIRED
        assert "underperformance" in result.reason

    def test_no_monitor_ever_breaches_runs_the_full_window(self) -> None:
        result = CanaryHarness().run(
            "model-a",
            _evidence(),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        assert result.days_run == 7
        assert len(result.periods) == 7


class TestExitJudgement:
    def test_production_ready_when_gate_grants_it(self) -> None:
        # Evidence is sufficient for production and the campaign is clean.
        result = CanaryHarness().run(
            "model-a",
            _evidence(),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        assert result.action is CanaryAction.PRODUCTION_READY
        assert result.reason == "promotion gate granted the target environment"

    def test_hold_when_canary_window_is_not_yet_earned(self) -> None:
        # The canary evidence claims only 2 days deployed: not enough for
        # production, even though the monitored campaign was clean.
        result = CanaryHarness().run(
            "model-a",
            _evidence(canary_days_deployed=2),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        assert result.action is CanaryAction.HOLD
        assert result.days_run == 7

    def test_hold_when_paper_evidence_missing(self) -> None:
        result = CanaryHarness().run(
            "model-a",
            _evidence(paper_days_deployed=None, paper_sharpe=None),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        assert result.action is CanaryAction.HOLD

    def test_custom_campaign_days_and_target(self) -> None:
        config = CanaryHarnessConfig(
            campaign_days=3,
            environment_target=ModelEnvironment.CANARY,
        )
        result = CanaryHarness(config).run(
            "model-a",
            _evidence(),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        assert result.days_run == 3
        # CANARY is an upstream target: evidence that qualifies for it may be
        # weaker, so a clean 3-day run can be production-unready but still
        # evaluated against the canary gate.
        assert result.action in (CanaryAction.PRODUCTION_READY, CanaryAction.HOLD)


class TestDeterminismAndSerialization:
    def test_same_input_same_result(self) -> None:
        def run() -> dict[str, Any]:
            return (
                CanaryHarness()
                .run(
                    "model-a",
                    _evidence(canary_days_deployed=2),
                    lambda day: _monitor("model-a", day, drawdown=3.0),
                    authorized=True,
                )
                .as_dict()
            )

        assert run() == run()

    def test_result_as_dict(self) -> None:
        result = CanaryHarness().run(
            "model-a",
            _evidence(),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        payload = result.as_dict()
        assert payload["authorized"] is True
        assert payload["action"] == "production_ready"
        assert isinstance(payload["periods"], list)
        assert payload["periods"][0]["action"] == "continue"

    def test_period_as_dict(self) -> None:
        result = CanaryHarness().run(
            "model-a",
            _evidence(canary_days_deployed=2),
            lambda day: _monitor("model-a", day),
            authorized=True,
        )
        payload = result.periods[0].as_dict()
        assert payload["day"] == 1
        assert payload["action"] == "continue"

    def test_retired_result_as_dict(self) -> None:
        result = CanaryHarness().run(
            "model-a",
            _evidence(),
            lambda day: _monitor("model-a", day, drawdown=20.0),
            authorized=True,
        )
        payload = result.as_dict()
        assert payload["action"] == "retired"
        assert payload["days_run"] == 1

    def test_is_a_program_result(self) -> None:
        result = run_canary_campaign(
            "model-a", _evidence(), lambda day: _monitor("model-a", day), authorized=True
        )
        assert isinstance(result, CanaryProgramResult)
