"""Tests for the death system (T3-26-1): degrade -> demote -> retire.

The death system is the explicit policy for strategies that stop working.
It must decide deterministically from two evidence sources (the edge
monitor's advisory trigger and the campaign verdicts recorded on the
passport), follow the documented risk-precedence rule (the harshest
action wins on disagreement), and apply its verdict as an auditable
passport lifecycle transition. Library-only: nothing here touches the
live path, and STAY records nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.research.death_system import DeathSystemService
from backend.application.research.edge_monitor import (
    environment_for_status,
    status_for_environment,
)
from backend.application.research.evidence_engine import EvidenceEngine
from backend.domain.research.death_system import (
    DeathDecision,
    DemotionAction,
    harshest,
)
from backend.domain.research.edge_monitor import EdgeDemotionTrigger
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
)
from backend.domain.research.promotion import (
    ENVIRONMENT_CHAIN,
    ModelEnvironment,
    previous_environment,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import (
    SqlitePassportRepository,
)


def engine(tmp_path) -> EvidenceEngine:
    return EvidenceEngine(SqlitePassportRepository(Database(tmp_path / "p.db")))


def base_kwargs() -> dict[str, Any]:
    return {
        "hypothesis": "OFI predicts 1-minute return",
        "dataset_id": "btcusdt",
        "dataset_version": 1,
        "features": ("ofi", "spread"),
        "model": "RuleBasedSolver",
        "trial_count": 50,
        "experiment_id": "EXP-1",
    }


def report_with(pooled: PooledEvidence):
    from backend.application.research.baseline_evaluation import EvaluationCosts
    from backend.application.research.decision_pipeline_evaluator import (
        OutOfSampleReport,
    )

    return OutOfSampleReport(
        symbol="btcusdt",
        costs=EvaluationCosts(half_spread_pct=0.0002, taker_fee_pct=0.0004),
        cv_spec={"train_size": 80, "test_size": 20, "expanding": True},
        folds=(),
        pooled=pooled,
    )


def good_evidence() -> PooledEvidence:
    return PooledEvidence(
        n_folds=8,
        total_test_bars=160,
        total_trades=40,
        total_wins=22,
        total_losses=18,
        total_fees=12.0,
        total_slippage_bps=3.5,
        gross_profit=250.0,
        gross_loss=180.0,
        mean_return_pct=1.2,
        median_return_pct=0.9,
        mean_excess_return_pct=0.7,
        positive_fold_rate=0.75,
        beats_buy_and_hold_rate=0.75,
        mean_max_drawdown_pct=-8.0,
        deflated_sharpe=1.1,
        reasoner="RuleBasedSolver",
        cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
    )


def trigger(passport_id: str, *, fired: bool, env: str | None) -> EdgeDemotionTrigger:
    return EdgeDemotionTrigger(
        passport_id=passport_id,
        triggered=fired,
        reason="ADWIN cut left window mean below decay threshold",
        recommended_environment=env,
    )


def passport_with(status: PassportStatus, **evidence) -> StrategyPassport:
    return StrategyPassport(
        passport_id="STRAT-DEATH",
        created_at=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        hypothesis="h",
        dataset_id="d",
        dataset_version=1,
        features=(),
        model="m",
        trial_count=1,
        evidence={},
        verdict=PassportVerdict(verdict=EvidenceVerdict.PROMOTE_TO_PAPER),
        status=status,
        paper_evidence=evidence.get("paper_evidence", {}),
        live_evidence=evidence.get("live_evidence", {}),
    )


class TestSeverityLadder:
    def test_harshest_is_terminal(self):
        assert harshest(DemotionAction.STAY, DemotionAction.DEGRADE) is DemotionAction.DEGRADE
        assert harshest(DemotionAction.DEGRADE, DemotionAction.DEMOTE) is DemotionAction.DEMOTE
        assert harshest(DemotionAction.DEMOTE, DemotionAction.RETIRE) is DemotionAction.RETIRE
        assert harshest(DemotionAction.STAY) is DemotionAction.STAY
        assert harshest() is DemotionAction.STAY

    def test_empty_and_unknown_rejected(self):
        with pytest.raises(ValueError, match="unknown demotion action"):
            harshest("nope")  # type: ignore[arg-type]


class TestEvaluate:
    def test_no_trigger_stays(self):
        decision = DeathSystemService().evaluate(passport_with(PassportStatus.PAPER))
        assert decision.action is DemotionAction.STAY
        assert decision.reasons == ()
        assert decision.demotes is False

    def test_retired_passport_is_never_relitigated(self):
        # T3-28-1: a corpse cannot be double-dead — even with a fired edge
        # trigger and a retired campaign verdict on the record, a retired
        # passport gets STAY with the tombstone reason.
        decision = DeathSystemService().evaluate(
            passport_with(
                PassportStatus.RETIRED,
                paper_evidence={"paper_campaign": {"action": "retired"}},
            ),
            trigger("x", fired=True, env="candidate"),
        )
        assert decision.action is DemotionAction.STAY
        assert "retired" in decision.reasons[0]
        assert decision.demotes is False

    def test_edge_trigger_degrades_one_step(self):
        decision = DeathSystemService().evaluate(
            passport_with(PassportStatus.CANARY),
            trigger("x", fired=True, env="paper"),
        )
        assert decision.action is DemotionAction.DEGRADE
        assert decision.to_environment is ModelEnvironment.PAPER
        assert decision.demotes is True
        assert "edge monitor" in decision.reasons[0]

    def test_edge_decay_at_bottom_retires(self):
        decision = DeathSystemService().evaluate(
            passport_with(PassportStatus.RESEARCH),
            trigger("x", fired=True, env=None),
        )
        assert decision.action is DemotionAction.RETIRE
        assert decision.to_environment is None

    def test_retired_paper_campaign_demotes_two_steps(self):
        decision = DeathSystemService().evaluate(
            passport_with(
                PassportStatus.PAPER,
                paper_evidence={"paper_campaign": {"action": "retired", "reason": "drawdown"}},
            )
        )
        assert decision.action is DemotionAction.DEMOTE
        assert decision.to_environment is ModelEnvironment.RESEARCH
        assert "paper campaign retired" in decision.reasons[0]

    def test_retired_canary_demotes_two_steps(self):
        decision = DeathSystemService().evaluate(
            passport_with(
                PassportStatus.CANARY,
                live_evidence={"canary": {"action": "retired", "reason": "underperformance"}},
            )
        )
        assert decision.action is DemotionAction.DEMOTE
        assert decision.to_environment is ModelEnvironment.VALIDATION

    def test_harshest_wins_on_disagreement(self):
        decision = DeathSystemService().evaluate(
            passport_with(
                PassportStatus.PAPER,
                paper_evidence={"paper_campaign": {"action": "retired"}},
            ),
            trigger("x", fired=True, env="validation"),
        )
        assert decision.action is DemotionAction.DEMOTE  # DEMOTE > DEGRADE
        assert decision.to_environment is ModelEnvironment.RESEARCH
        assert len(decision.reasons) == 2  # both sources preserved

    def test_failed_trigger_ignored(self):
        decision = DeathSystemService().evaluate(
            passport_with(PassportStatus.LIVE),
            trigger("x", fired=False, env="canary"),
        )
        assert decision.action is DemotionAction.STAY

    def test_healthy_campaign_ignored(self):
        decision = DeathSystemService().evaluate(
            passport_with(
                PassportStatus.PAPER,
                paper_evidence={"paper_campaign": {"action": "completed_advanced"}},
            )
        )
        assert decision.action is DemotionAction.STAY


class TestApply:
    def test_stay_records_nothing(self, tmp_path):
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-DEATH1", **base_kwargs(), report=report_with(good_evidence())
        )
        decision = DeathDecision(
            passport_id="STRAT-DEATH1", action=DemotionAction.STAY, from_status=passport.status
        )
        result = DeathSystemService().apply(svc, decision)
        assert result.status is passport.status
        assert svc.lifecycle("STRAT-DEATH1") == ()

    def test_retire_moves_to_terminal_status(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-DEATH2", **base_kwargs(), report=report_with(good_evidence())
        )
        decision = DeathDecision(
            passport_id="STRAT-DEATH2",
            action=DemotionAction.RETIRE,
            reasons=("edge decay at bottom of chain",),
            from_status=PassportStatus.CANDIDATE,
        )
        result = DeathSystemService().apply(svc, decision)
        assert result.status is PassportStatus.RETIRED
        events = svc.lifecycle("STRAT-DEATH2")
        transition = [e for e in events if e.event_type == "status_change"]
        assert len(transition) == 1
        assert transition[0].to_status is PassportStatus.RETIRED
        assert transition[0].reason == "edge decay at bottom of chain"
        assert events[-1].event_type == "rollback_update"
        restored = svc.passport("STRAT-DEATH2")
        assert restored is not None
        assert restored.live_evidence["rollback"]["rollback"] is True
        assert restored.live_evidence["rollback"]["reasons"] == ["edge decay at bottom of chain"]

    def test_demote_moves_to_target_status(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-DEATH3", **base_kwargs(), report=report_with(good_evidence())
        )
        decision = DeathDecision(
            passport_id="STRAT-DEATH3",
            action=DemotionAction.DEMOTE,
            to_environment=ModelEnvironment.VALIDATION,
            reasons=("paper campaign retired: drawdown",),
            from_status=PassportStatus.PAPER,
        )
        result = DeathSystemService().apply(svc, decision)
        assert result.status is PassportStatus.CANDIDATE
        assert svc.lifecycle("STRAT-DEATH3")[-1].to_status is PassportStatus.CANDIDATE

    def test_decision_survives_roundtrip(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-DEATH4", **base_kwargs(), report=report_with(good_evidence())
        )
        decision = DeathDecision(
            passport_id="STRAT-DEATH4",
            action=DemotionAction.RETIRE,
            reasons=("x",),
            from_status=PassportStatus.CANDIDATE,
        )
        DeathSystemService().apply(svc, decision)
        restored = svc.passport("STRAT-DEATH4")
        assert restored is not None
        assert restored.status is PassportStatus.RETIRED

    def test_retire_then_apply_again_records_nothing(self, tmp_path: object) -> None:
        # T3-28-1: applying a second death verdict to a corpse is a no-op —
        # evaluate() returns STAY, and STAY records nothing.
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-DEATH5", **base_kwargs(), report=report_with(good_evidence())
        )
        decision = DeathDecision(
            passport_id="STRAT-DEATH5",
            action=DemotionAction.RETIRE,
            reasons=("first death",),
            from_status=PassportStatus.CANDIDATE,
        )
        DeathSystemService().apply(svc, decision)
        before = len(svc.lifecycle("STRAT-DEATH5"))
        corpse = svc.passport("STRAT-DEATH5")
        assert corpse is not None
        assert corpse.status is PassportStatus.RETIRED
        second = DeathSystemService().evaluate(corpse)
        assert second.action is DemotionAction.STAY
        DeathSystemService().apply(svc, second)
        assert len(svc.lifecycle("STRAT-DEATH5")) == before  # nothing appended


class TestStatusEnvironmentMapping:
    def test_roundtrip(self):
        for status in (
            "research",
            "candidate",
            "paper",
            "canary",
            "live",
        ):
            environment = environment_for_status(status)
            assert environment is not None
            assert status_for_environment(environment) == status

    def test_production_has_live_status(self):
        assert status_for_environment(ModelEnvironment.PRODUCTION) == "live"

    def test_chain_is_monotonic(self):
        for index, environment in enumerate(ENVIRONMENT_CHAIN):
            previous = previous_environment(environment)
            if index == 0:
                assert previous is None  # research: bottom of the chain
            else:
                assert previous is ENVIRONMENT_CHAIN[index - 1]
