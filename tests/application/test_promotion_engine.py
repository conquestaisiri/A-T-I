# tests/application/test_promotion_engine.py
"""Tests for the controlled model-promotion pipeline (task P4-001).

The promotion engine is a pure, deterministic judge: the same evidence always
yields the same gate decision and the same rollback verdict. Nothing here
touches the network or any venue.
"""

from __future__ import annotations

from typing import Any

from backend.application.research.promotion_engine import (
    PromotionEngine,
    promote,
    promotion_chain,
    rollback_required,
)
from backend.domain.research.promotion import (
    CandidateEvidence,
    DeploymentMonitor,
    ModelEnvironment,
    PromotionConfig,
    PromotionRequest,
)


def _evidence(**overrides: Any) -> CandidateEvidence:
    defaults: dict[str, Any] = {
        "candidate_id": "model-a",
        "validation_samples": 500,
        "validation_sharpe": 1.2,
        "paper_days_deployed": 30,
        "paper_sharpe": 0.8,
        "canary_days_deployed": 14,
    }
    defaults.update(overrides)
    return CandidateEvidence(**defaults)


def _request(environment: ModelEnvironment, **evidence: Any) -> PromotionRequest:
    return PromotionRequest(
        candidate_id="model-a",
        environment=environment,
        evidence=_evidence(**evidence),
    )


def _config(**overrides: Any) -> PromotionConfig:
    return PromotionConfig(**overrides)


class TestEnvironmentChain:
    def test_chain_is_ordered_and_complete(self) -> None:
        assert promotion_chain() == (
            "research",
            "validation",
            "paper",
            "canary",
            "production",
        )

    def test_next_environment_walks_the_chain(self) -> None:
        from backend.domain.research.promotion import next_environment

        assert next_environment(ModelEnvironment.RESEARCH) is ModelEnvironment.VALIDATION
        assert next_environment(ModelEnvironment.VALIDATION) is ModelEnvironment.PAPER
        assert next_environment(ModelEnvironment.PAPER) is ModelEnvironment.CANARY
        assert next_environment(ModelEnvironment.CANARY) is ModelEnvironment.PRODUCTION
        assert next_environment(ModelEnvironment.PRODUCTION) is None

    def test_previous_environment_walks_backwards(self) -> None:
        from backend.domain.research.promotion import previous_environment

        assert previous_environment(ModelEnvironment.RESEARCH) is None
        assert previous_environment(ModelEnvironment.VALIDATION) is ModelEnvironment.RESEARCH
        assert previous_environment(ModelEnvironment.PAPER) is ModelEnvironment.VALIDATION
        assert previous_environment(ModelEnvironment.CANARY) is ModelEnvironment.PAPER
        assert previous_environment(ModelEnvironment.PRODUCTION) is ModelEnvironment.CANARY


class TestPromotionGates:
    def test_full_evidence_promotes_into_production(self) -> None:
        decision = promote(_request(ModelEnvironment.PRODUCTION))
        assert decision.allowed is True
        assert decision.reasons == ()
        assert decision.environment is ModelEnvironment.PRODUCTION

    def test_promotion_to_validation_always_allowed(self) -> None:
        # A candidate that exists may leave research; no empirical bar yet.
        decision = promote(_request(ModelEnvironment.VALIDATION, validation_samples=None))
        assert decision.allowed is True

    def test_insufficient_validation_samples_denies_paper(self) -> None:
        decision = promote(_request(ModelEnvironment.PAPER, validation_samples=10))
        assert decision.allowed is False
        assert "validation sample count" in decision.reasons

    def test_negative_validation_sharpe_denies_paper(self) -> None:
        decision = promote(_request(ModelEnvironment.PAPER, validation_sharpe=-0.4))
        assert decision.allowed is False
        assert "validation sharpe" in decision.reasons

    def test_missing_paper_window_denies_canary(self) -> None:
        decision = promote(_request(ModelEnvironment.CANARY, paper_days_deployed=1))
        assert decision.allowed is False
        assert "paper deployment window" in decision.reasons

    def test_paper_sharpe_below_floor_denies_canary(self) -> None:
        decision = promote(_request(ModelEnvironment.CANARY, paper_sharpe=0.05))
        assert decision.allowed is False
        assert "paper sharpe" in decision.reasons

    def test_missing_canary_window_denies_production(self) -> None:
        decision = promote(_request(ModelEnvironment.PRODUCTION, canary_days_deployed=2))
        assert decision.allowed is False
        assert "canary deployment window" in decision.reasons

    def test_none_evidence_is_a_missing_requirement(self) -> None:
        # None means "not applicable yet", never a free pass.
        decision = promote(_request(ModelEnvironment.PAPER, validation_sharpe=None))
        assert decision.allowed is False
        assert "validation sharpe" in decision.reasons

    def test_gates_are_cumulative_no_leapfrogging(self) -> None:
        # Great canary, but paper was only 1 day: production must be denied.
        decision = promote(
            _request(
                ModelEnvironment.PRODUCTION,
                paper_days_deployed=1,
                canary_days_deployed=100,
            )
        )
        assert decision.allowed is False
        assert "paper deployment window" in decision.reasons

    def test_all_missing_reasons_reported(self) -> None:
        # Evidence present at the validation layer but absent everywhere after
        # it: every gate from validation onward reports its missing condition.
        decision = promote(
            _request(
                ModelEnvironment.PRODUCTION,
                validation_sharpe=None,
                paper_sharpe=None,
                canary_days_deployed=None,
            )
        )
        assert decision.allowed is False
        for requirement in ("validation sharpe", "paper sharpe", "canary deployment window"):
            assert requirement in decision.reasons

    def test_satisfied_requirements_listed(self) -> None:
        decision = promote(
            _request(
                ModelEnvironment.PAPER,
                validation_samples=300,
                validation_sharpe=0.9,
            )
        )
        assert "validation sample count" in decision.satisfied
        assert "validation sharpe" in decision.satisfied

    def test_custom_config_raises_or_lowers_the_bar(self) -> None:
        config = _config(validation_sharpe_min=2.0)
        decision = promote(_request(ModelEnvironment.PAPER, validation_sharpe=1.2), config=config)
        assert decision.allowed is False
        decision_ok = promote(
            _request(ModelEnvironment.PAPER, validation_sharpe=2.5), config=config
        )
        assert decision_ok.allowed is True


class TestRollback:
    def test_healthy_candidate_stays(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.PRODUCTION,
            drawdown_pct=3.0,
            underperformance_bps=5.0,
            failed_orders_pct=0.5,
        )
        decision = rollback_required(monitor)
        assert decision.rollback is False
        assert decision.to_environment is None
        assert decision.reasons == ()

    def test_drawdown_breach_rolls_back_one_environment(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.PRODUCTION,
            drawdown_pct=15.0,
        )
        decision = rollback_required(monitor)
        assert decision.rollback is True
        assert decision.to_environment is ModelEnvironment.CANARY
        assert any("drawdown" in r for r in decision.reasons)

    def test_canary_rolls_back_to_paper(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.CANARY,
            drawdown_pct=12.0,
        )
        decision = rollback_required(monitor)
        assert decision.to_environment is ModelEnvironment.PAPER

    def test_underperformance_rolls_back(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.PAPER,
            drawdown_pct=0.0,
            underperformance_bps=40.0,
        )
        decision = rollback_required(monitor)
        assert decision.rollback is True
        assert decision.to_environment is ModelEnvironment.VALIDATION
        assert any("underperformance" in r for r in decision.reasons)

    def test_failed_orders_breach_rolls_back(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.CANARY,
            drawdown_pct=0.0,
            failed_orders_pct=12.0,
        )
        decision = rollback_required(monitor)
        assert decision.rollback is True
        assert decision.to_environment is ModelEnvironment.PAPER
        assert any("failed orders" in r for r in decision.reasons)

    def test_research_candidate_is_grounded(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.RESEARCH,
            drawdown_pct=20.0,
        )
        decision = rollback_required(monitor)
        assert decision.rollback is True
        assert decision.to_environment is None

    def test_multiple_breaches_are_all_reported(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.PRODUCTION,
            drawdown_pct=11.0,
            underperformance_bps=30.0,
            failed_orders_pct=6.0,
        )
        decision = rollback_required(monitor)
        assert len(decision.reasons) == 3
        assert decision.to_environment is ModelEnvironment.CANARY

    def test_custom_limits_change_rollback_trigger(self) -> None:
        config = _config(max_drawdown_pct=5.0)
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.CANARY,
            drawdown_pct=6.0,
        )
        decision = rollback_required(monitor, config=config)
        assert decision.rollback is True
        loosened = rollback_required(
            DeploymentMonitor(
                candidate_id="model-a",
                environment=ModelEnvironment.CANARY,
                drawdown_pct=4.0,
            ),
            config=config,
        )
        assert loosened.rollback is False


class TestDeterminismAndSerialization:
    def test_engine_instance_matches_module_function(self) -> None:
        request = _request(ModelEnvironment.PRODUCTION)
        assert PromotionEngine().evaluate(request).allowed == promote(request).allowed

    def test_same_input_same_decision(self) -> None:
        request = _request(ModelEnvironment.PRODUCTION, validation_sharpe=0.4)
        assert promote(request).as_dict() == promote(request).as_dict()

    def test_gate_decision_as_dict(self) -> None:
        decision = promote(_request(ModelEnvironment.PRODUCTION, paper_days_deployed=1))
        payload = decision.as_dict()
        assert payload["environment"] == "production"
        assert payload["allowed"] is False
        assert isinstance(payload["required"], list)
        assert isinstance(payload["reasons"], list)

    def test_rollback_decision_as_dict(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.PRODUCTION,
            drawdown_pct=12.0,
        )
        payload = rollback_required(monitor).as_dict()
        assert payload["rollback"] is True
        assert payload["to_environment"] == "canary"

    def test_candidate_evidence_as_dict(self) -> None:
        payload = _evidence(validation_sharpe=None).as_dict()
        assert payload["validation_sharpe"] is None
        assert payload["candidate_id"] == "model-a"

    def test_promotion_request_as_dict(self) -> None:
        payload = _request(ModelEnvironment.CANARY).as_dict()
        assert payload["environment"] == "canary"
        assert payload["evidence"]["candidate_id"] == "model-a"

    def test_monitor_as_dict(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.PAPER,
            drawdown_pct=2.0,
        )
        payload = monitor.as_dict()
        assert payload["environment"] == "paper"
        assert payload["drawdown_pct"] == 2.0

    def test_rollback_to_none_when_healthy_in_as_dict(self) -> None:
        monitor = DeploymentMonitor(
            candidate_id="model-a",
            environment=ModelEnvironment.PAPER,
            drawdown_pct=1.0,
        )
        payload = rollback_required(monitor).as_dict()
        assert payload["rollback"] is False
        assert payload["to_environment"] is None
