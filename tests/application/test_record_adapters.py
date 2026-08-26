"""Tests for the autonomy record adapters and promotion audit adapter (WS1.4/WS1.6).

The adapters translate rich harness results into durable records deterministically,
and the audit adapter persists every promotion gate decision and automatic
rollback to the outcome corpus.
"""

from __future__ import annotations

import pytest
from backend.application.research.autonomy_audit import (
    audit_promotion_evaluation,
    audit_promotion_granted,
    audit_rollback,
)
from backend.application.research.record_adapters import (
    campaign_record_from_result,
    day_outcome_record,
    program_run_record,
    promotion_decision_record,
    rollback_record,
)
from backend.domain.research.autonomy_program import (
    AutonomyProgramResult,
    ProgramStage,
    StageResult,
    StageVerdict,
)
from backend.domain.research.paper_campaign import (
    PaperCampaignAction,
    PaperCampaignResult,
    PaperDayOutcome,
)
from backend.domain.research.promotion import (
    CandidateEvidence,
    GateDecision,
    ModelEnvironment,
    RollbackDecision,
)
from backend.domain.research.records import (
    CampaignStatus,
    PromotionAction,
)
from backend.infrastructure.sqlite.autonomy_repository import SqliteAutonomyStore
from backend.infrastructure.sqlite.database import Database

T0 = "2026-08-13T00:00:00.000+00:00"


@pytest.fixture
def store(tmp_path) -> SqliteAutonomyStore:
    return SqliteAutonomyStore(Database(tmp_path / "audit.db"))


def evidence(**overrides: int | None) -> CandidateEvidence:
    # candidate_id is required positional
    defaults = {
        "candidate_id": "model-a",
        "validation_samples": 300,
        "validation_sharpe": 0.7,
        "paper_days_deployed": 30,
        "paper_sharpe": 0.5,
        "canary_days_deployed": 7,
    }
    defaults.update(overrides)
    return CandidateEvidence(**defaults)


class TestCampaignAdapter:
    def test_advanced_result_becomes_completed(self) -> None:
        result = PaperCampaignResult(
            candidate_id="model-a",
            days_run=30,
            action=PaperCampaignAction.COMPLETED_ADVANCED,
            sharpe=1.2,
            drawdown_pct=3.5,
            evidence=evidence(),
            reason="clean window",
        )
        record = campaign_record_from_result(
            result, campaign_id="camp-1", target_days=30, started_at=T0, completed_at=T0
        )
        assert record.status is CampaignStatus.COMPLETED
        assert record.action is PaperCampaignAction.COMPLETED_ADVANCED
        assert record.days_run == 30
        assert record.sharpe == 1.2

    def test_retired_result_becomes_retired(self) -> None:
        result = PaperCampaignResult(
            candidate_id="model-a",
            days_run=7,
            action=PaperCampaignAction.RETIRED,
            sharpe=-0.4,
            drawdown_pct=14.0,
            evidence=evidence(),
            reason="drawdown 14.00% > 10.00%",
        )
        record = campaign_record_from_result(
            result, campaign_id="camp-2", target_days=30, started_at=T0, completed_at=T0
        )
        assert record.status is CampaignStatus.RETIRED
        assert record.terminal is True

    def test_day_outcome_adapter(self) -> None:
        record = day_outcome_record(
            "model-a",
            "camp-1",
            PaperDayOutcome(
                day=3, return_pct=0.2, expected_return_pct=0.05, failed_orders=1, total_orders=8
            ),
            recorded_at=T0,
        )
        assert record.day == 3
        assert record.return_pct == 0.2
        assert record.failed_orders == 1
        assert record.recorded_at == T0


class TestProgramRunAdapter:
    def test_program_result_to_record(self) -> None:
        result = AutonomyProgramResult(
            program_id="prog-1",
            candidate_id="model-a",
            final_environment="production",
            stages=(
                StageResult(ProgramStage.RESEARCH, StageVerdict.PASSED, "ok"),
                StageResult(
                    ProgramStage.PAPER,
                    StageVerdict.PASSED,
                    "ok",
                    evidence=evidence(paper_days_deployed=30),
                ),
            ),
            notes=("clean run",),
        )
        record = program_run_record(result, started_at=T0, completed_at=T0)
        assert record.program_id == "prog-1"
        assert record.final_environment == "production"
        assert record.earned == ("research", "paper")
        assert record.stages[1].evidence is not None
        assert record.stages[1].evidence["paper_days_deployed"] == 30


class TestPromotionAdapter:
    def test_gate_decision_to_record(self) -> None:
        decision = GateDecision(
            candidate_id="model-a",
            environment=ModelEnvironment.PAPER,
            allowed=False,
            required=("validation sample count", "validation sharpe"),
            satisfied=("validation sample count",),
        )
        record = promotion_decision_record(decision, occurred_at=T0)
        assert record.allowed is False
        assert record.reasons == ("validation sharpe",)
        assert record.action is PromotionAction.EVALUATE

    def test_rollback_decision_to_record(self) -> None:
        decision = RollbackDecision(
            candidate_id="model-a",
            rollback=True,
            to_environment=ModelEnvironment.VALIDATION,
            reasons=("drawdown 12.00% > 10.00%",),
        )
        record = rollback_record(decision, from_environment=ModelEnvironment.PAPER, occurred_at=T0)
        assert record.from_environment is ModelEnvironment.PAPER
        assert record.to_environment is ModelEnvironment.VALIDATION

    def test_no_rollback_when_not_breached(self) -> None:
        decision = RollbackDecision(candidate_id="model-a", rollback=False, to_environment=None)
        record = rollback_record(decision, from_environment=ModelEnvironment.PAPER, occurred_at=T0)
        assert record.to_environment is None
        assert record.reasons == ()


class TestAuditAdapter:
    def test_audit_evaluation_persisted(self, store: SqliteAutonomyStore) -> None:
        decision = GateDecision(
            candidate_id="model-a",
            environment=ModelEnvironment.CANARY,
            allowed=False,
            required=("paper deployment window", "paper sharpe"),
            satisfied=(),
        )
        audit_promotion_evaluation(store, decision, occurred_at=T0)
        rows = store.list_promotion_decisions(candidate_id="model-a")
        assert len(rows) == 1
        assert rows[0].allowed is False
        assert rows[0].action is PromotionAction.EVALUATE
        assert rows[0].environment is ModelEnvironment.CANARY

    def test_audit_promotion_persisted(self, store: SqliteAutonomyStore) -> None:
        decision = GateDecision(
            candidate_id="model-a",
            environment=ModelEnvironment.PAPER,
            allowed=True,
            required=("research complete", "validation sample count"),
            satisfied=("research complete", "validation sample count"),
        )
        audit_promotion_granted(store, decision, occurred_at=T0)
        rows = store.list_promotion_decisions(candidate_id="model-a")
        assert rows[0].action is PromotionAction.PROMOTE
        assert rows[0].allowed is True

    def test_audit_rollback_persisted_only_when_breached(self, store: SqliteAutonomyStore) -> None:
        audit_rollback(
            store,
            RollbackDecision(candidate_id="model-a", rollback=False, to_environment=None),
            from_environment=ModelEnvironment.CANARY,
            occurred_at=T0,
        )
        assert store.list_rollbacks(candidate_id="model-a") == []

        audit_rollback(
            store,
            RollbackDecision(
                candidate_id="model-a",
                rollback=True,
                to_environment=ModelEnvironment.PAPER,
                reasons=("drawdown 12.00% > 10.00%",),
            ),
            from_environment=ModelEnvironment.CANARY,
            occurred_at=T0,
        )
        rows = store.list_rollbacks(candidate_id="model-a")
        assert len(rows) == 1
        assert rows[0].from_environment is ModelEnvironment.CANARY
        assert rows[0].to_environment is ModelEnvironment.PAPER
