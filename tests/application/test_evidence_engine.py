"""Tests for the evidence engine (P5-003c): OOS report -> auditable passport.

The engine must compose the evaluator's report into a durable passport whose
verdict follows the conservative gates, keep the passport immutable (a second
issue with the same id is refused), and record every later change as an
append-only lifecycle event.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.decision_pipeline_evaluator import (
    DecisionPipelineEvaluator,
    OutOfSampleReport,
)
from backend.application.research.evidence_engine import EvidenceEngine
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
)
from backend.domain.research.pbo import PboResult
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import (
    SqlitePassportRepository,
)


def engine(tmp_path) -> EvidenceEngine:
    return EvidenceEngine(SqlitePassportRepository(Database(tmp_path / "p.db")))


def report_with(pooled: PooledEvidence) -> OutOfSampleReport:
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


def pbo(prob: float) -> PboResult:
    return PboResult(
        pbo=prob,
        mean_logit=0.0,
        n_trials=50,
        n_observations=160,
        n_splits=100,
        n_selected=25,
        metric="mean",
        seed=42,
    )


def base_kwargs() -> dict[str, Any]:
    return dict(
        hypothesis="OFI predicts 1-minute return",
        dataset_id="btcusdt",
        dataset_version=1,
        features=("ofi", "spread"),
        model="RuleBasedSolver",
        trial_count=50,
        train_period=("2021-01-01", "2024-12-31"),
        validation_period=("2025-01-01", "2025-12-31"),
        test_period=("2026-01-01", "2026-06-30"),
        experiment_id="EXP-1",
    )


class TestEvidenceEngine:
    def test_issues_passport_with_evidence_and_verdict(self, tmp_path):
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-000184",
            **base_kwargs(),
            report=report_with(good_evidence()),
            now=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )
        assert passport.status is PassportStatus.CANDIDATE
        assert passport.verdict.verdict is EvidenceVerdict.PROMOTE_TO_PAPER
        assert passport.evidence["pooled"]["deflated_sharpe"] == 1.1
        assert passport.evidence["symbol"] == "btcusdt"
        assert passport.cost_model == {
            "half_spread_pct": 0.0002,
            "taker_fee_pct": 0.0004,
        }
        assert passport.experiment_id == "EXP-1"
        stored = svc.passport("STRAT-000184")
        assert stored == passport

    def test_reject_verdict_retires_passport(self, tmp_path):
        svc = engine(tmp_path)
        bad = report_with(_with(good_evidence(), deflated_sharpe=-0.5))
        passport = svc.issue_passport(
            passport_id="STRAT-KILL",
            **base_kwargs(),
            report=bad,
        )
        assert passport.status is PassportStatus.RETIRED
        assert passport.verdict.verdict is EvidenceVerdict.REJECT

    def test_pbo_gate_rejects_and_records_reason(self, tmp_path):
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-PBO",
            **base_kwargs(),
            report=report_with(good_evidence()),
            pbo=pbo(0.85),
        )
        assert passport.verdict.verdict is EvidenceVerdict.REJECT
        assert any("PBO" in r for r in passport.verdict.reasons)

    def test_observe_verdict_when_dsr_missing(self, tmp_path):
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-OBS",
            **base_kwargs(),
            report=report_with(_with(good_evidence(), deflated_sharpe=None)),
        )
        assert passport.verdict.verdict is EvidenceVerdict.OBSERVE
        assert passport.status is PassportStatus.CANDIDATE

    def test_duplicate_issue_is_refused(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-1", **base_kwargs(), report=report_with(good_evidence())
        )
        with pytest.raises(ValueError, match="already exists"):
            svc.issue_passport(
                passport_id="STRAT-1", **base_kwargs(), report=report_with(good_evidence())
            )

    def test_transition_records_lifecycle_event(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-1", **base_kwargs(), report=report_with(good_evidence())
        )
        now = datetime(2026, 9, 1, tzinfo=UTC)
        updated = svc.transition(
            "STRAT-1", PassportStatus.PAPER, "paper campaign approved", now=now
        )
        assert updated.status is PassportStatus.PAPER
        events = svc.lifecycle("STRAT-1")
        assert len(events) == 1
        assert events[0].from_status is PassportStatus.CANDIDATE
        assert events[0].to_status is PassportStatus.PAPER
        assert events[0].occurred_at == now

    def test_transition_unknown_passport_is_refused(self, tmp_path):
        svc = engine(tmp_path)
        with pytest.raises(ValueError, match="unknown"):
            svc.transition("ghost", PassportStatus.PAPER, "x")

    def test_rerecord_evidence_updates_and_retires_on_reject(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-1", **base_kwargs(), report=report_with(good_evidence())
        )
        updated = svc.rerecord_evidence(
            "STRAT-1",
            report=report_with(_with(good_evidence(), deflated_sharpe=-1.0)),
            reason="re-evaluation after paper period",
        )
        assert updated.verdict.verdict is EvidenceVerdict.REJECT
        assert updated.status is PassportStatus.RETIRED
        events = svc.lifecycle("STRAT-1")
        assert events[-1].event_type == "evidence_update"

    def test_all_passports_orders_by_issue(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(passport_id="A", **base_kwargs(), report=report_with(good_evidence()))
        svc.issue_passport(passport_id="B", **base_kwargs(), report=report_with(good_evidence()))
        assert [p.passport_id for p in svc.all_passports()] == ["A", "B"]


class TestAttributionEvidence:
    """T2-16-1: the feature attribution report folds into the passport."""

    def _attribution(self) -> dict[str, Any]:
        return {
            "feature_names": ["trend", "momentum"],
            "regimes": ["all"],
            "full_by_regime": {
                "all": {
                    "accuracy": 0.61,
                    "precision": 0.5,
                    "recall": 0.5,
                    "f1": 0.5,
                    "n": 200,
                }
            },
            "attribution": [
                {
                    "feature": "trend",
                    "regime": "all",
                    "full_metrics": {
                        "accuracy": 0.61,
                        "precision": 0.5,
                        "recall": 0.5,
                        "f1": 0.5,
                        "n": 200,
                    },
                    "ablated_metrics": {
                        "accuracy": 0.55,
                        "precision": 0.45,
                        "recall": 0.45,
                        "f1": 0.45,
                        "n": 200,
                    },
                    "delta_accuracy": 0.06,
                    "delta_f1": 0.05,
                    "flip_count": 12,
                    "cost_pct": 0.1,
                    "lift_is_worth_cost": True,
                }
            ],
            "scorer_name": "threshold",
            "cost_model": {
                "half_spread_pct": 0.0002,
                "taker_fee_pct": 0.0004,
                "round_trip_multiplier": 2.0,
            },
        }

    def test_issue_embeds_attribution_evidence_verbatim(self, tmp_path):
        svc = engine(tmp_path)
        attribution = self._attribution()
        passport = svc.issue_passport(
            passport_id="STRAT-ATTR",
            **base_kwargs(),
            report=report_with(good_evidence()),
            attribution_evidence=attribution,
        )
        assert passport.evidence["attribution_evidence"] == attribution
        assert passport.evidence["attribution_evidence"]["feature_names"] == ["trend", "momentum"]

    def test_issue_without_attribution_leaves_no_key(self, tmp_path):
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-PLAIN",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        assert "attribution_evidence" not in passport.evidence

    def test_attribution_survives_round_trip(self, tmp_path):
        svc = engine(tmp_path)
        attribution = self._attribution()
        svc.issue_passport(
            passport_id="STRAT-ATTR2",
            **base_kwargs(),
            report=report_with(good_evidence()),
            attribution_evidence=attribution,
        )
        restored = svc.passport("STRAT-ATTR2")
        assert restored is not None
        assert restored.evidence["attribution_evidence"] == attribution

    def test_rerecord_replaces_attribution_evidence(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-ATTR3",
            **base_kwargs(),
            report=report_with(good_evidence()),
            attribution_evidence=self._attribution(),
        )
        updated = svc.rerecord_evidence(
            "STRAT-ATTR3",
            report=report_with(good_evidence()),
            attribution_evidence={"feature_names": ["only"], "regimes": ["all"]},
            reason="re-evaluation with feature cut",
        )
        assert updated.evidence["attribution_evidence"] == {
            "feature_names": ["only"],
            "regimes": ["all"],
        }


class TestRobustnessEvidence:
    ROBUSTNESS = {
        "perturbation": {
            "champion_label": "mom-20",
            "champion_excess_pct": 0.9,
            "variant_count": 5,
            "positive_variants": 4,
            "positive_fraction": 0.8,
            "robust": True,
        },
        "expense_stress": {
            "strategy_name": "mom",
            "survives_2x_cost": True,
            "survives_2x_slippage": False,
        },
        "selection_bias": {
            "n_experiments": 5,
            "champion_excess_pct": 0.9,
            "mean_excess_pct": 0.2,
            "std_excess_pct": 0.4,
            "expected_best_null_pct": 0.55,
            "selection_inflation_pct": 0.35,
            "adjusted_excess_pct": 0.55,
            "survives": True,
        },
    }

    def test_issue_embeds_robustness_verbatim(self, tmp_path):
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-ROB1",
            **base_kwargs(),
            report=report_with(good_evidence()),
            robustness_evidence=self.ROBUSTNESS,
        )
        assert passport.evidence["robustness_evidence"] == self.ROBUSTNESS
        # every sub-report survived verbatim, nothing summarised away
        assert (
            passport.evidence["robustness_evidence"]["perturbation"]["champion_label"] == "mom-20"
        )
        assert (
            passport.evidence["robustness_evidence"]["selection_bias"]["adjusted_excess_pct"]
            == 0.55
        )

    def test_issue_without_robustness_omits_block(self, tmp_path):
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-ROB2",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        assert "robustness_evidence" not in passport.evidence

    def test_rerecord_replaces_robustness_block(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-ROB3",
            **base_kwargs(),
            report=report_with(good_evidence()),
            robustness_evidence=self.ROBUSTNESS,
        )
        updated = svc.rerecord_evidence(
            "STRAT-ROB3",
            report=report_with(good_evidence()),
            robustness_evidence={"perturbation": {"champion_label": "new"}},
            reason="new robustness round",
        )
        assert updated.evidence["robustness_evidence"] == {
            "perturbation": {"champion_label": "new"}
        }

    def test_payload_survives_sqlite_roundtrip(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-ROB4",
            **base_kwargs(),
            report=report_with(good_evidence()),
            robustness_evidence=self.ROBUSTNESS,
        )
        restored = svc.passport("STRAT-ROB4")
        assert restored is not None
        assert restored.evidence["robustness_evidence"] == self.ROBUSTNESS


class TestPaperCampaignEvidence:
    def campaign_result(self) -> Any:
        from backend.domain.research.paper_campaign import (
            PaperCampaignAction,
            PaperCampaignResult,
            PaperDay,
            PaperDayAction,
        )
        from backend.domain.research.promotion import CandidateEvidence

        return PaperCampaignResult(
            candidate_id="STRAT-CAM",
            days_run=40,
            action=PaperCampaignAction.COMPLETED_ADVANCED,
            sharpe=1.4,
            drawdown_pct=-6.0,
            evidence=CandidateEvidence(
                candidate_id="STRAT-CAM",
                validation_samples=40,
                validation_sharpe=1.4,
                paper_days_deployed=40,
                paper_sharpe=1.4,
            ),
            periods=(
                PaperDay(day=1, action=PaperDayAction.CONTINUE, return_pct=0.2, drawdown_pct=-0.1),
                PaperDay(day=2, action=PaperDayAction.CONTINUE, return_pct=-0.3, drawdown_pct=-0.4),
            ),
        )

    def test_campaign_outcome_appends_to_paper_evidence(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAM1",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        result = self.campaign_result()
        updated = svc.record_paper_campaign(
            "STRAT-CAM1",
            result=result,
            reason="campaign completed",
            now=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        )
        payload = updated.paper_evidence["paper_campaign"]
        assert payload["candidate_id"] == "STRAT-CAM"
        assert payload["days_run"] == 40
        assert payload["action"] == "completed_advanced"
        assert payload["eligible_for_canary"] is True
        assert len(payload["periods"]) == 2
        assert payload["evidence"]["paper_days_deployed"] == 40
        # lifecycle status untouched (evidence append only)
        assert updated.status is PassportStatus.CANDIDATE

    def test_campaign_update_is_on_the_audit_trail(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAM2",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        svc.record_paper_campaign(
            "STRAT-CAM2", result=self.campaign_result(), reason="campaign completed"
        )
        events = svc.lifecycle("STRAT-CAM2")
        assert events[-1].event_type == "paper_campaign_update"
        assert events[-1].reason == "campaign completed"

    def test_mapping_result_accepted(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAM3",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        updated = svc.record_paper_campaign(
            "STRAT-CAM3",
            result={"days_run": 10, "action": "retired", "reason": "stay-limit breach"},
            reason="campaign retired",
        )
        assert updated.paper_evidence["paper_campaign"]["action"] == "retired"

    def test_unknown_passport_refused(self, tmp_path):
        svc = engine(tmp_path)
        with pytest.raises(ValueError, match="unknown passport"):
            svc.record_paper_campaign("STRAT-NOPE", result=self.campaign_result(), reason="x")

    def test_payload_survives_sqlite_roundtrip(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAM4",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        svc.record_paper_campaign("STRAT-CAM4", result=self.campaign_result(), reason="done")
        restored = svc.passport("STRAT-CAM4")
        assert restored is not None
        assert restored.paper_evidence["paper_campaign"]["days_run"] == 40
        assert restored.paper_evidence["paper_campaign"]["periods"][1]["day"] == 2


class TestCanaryEvidence:
    def canary_result(self) -> Any:
        from backend.domain.research.canary import (
            CanaryAction,
            CanaryPeriod,
            CanaryProgramResult,
        )

        return CanaryProgramResult(
            candidate_id="STRAT-CAN",
            authorized=True,
            days_run=21,
            action=CanaryAction.PRODUCTION_READY,
            periods=(
                CanaryPeriod(day=1, action=CanaryAction.CONTINUE),
                CanaryPeriod(day=20, action=CanaryAction.CONTINUE),
                CanaryPeriod(day=21, action=CanaryAction.PRODUCTION_READY, reason="gate granted"),
            ),
            reason="full window breach-free",
        )

    def test_canary_outcome_appends_to_live_evidence(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAN1",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        result = self.canary_result()
        updated = svc.record_canary(
            "STRAT-CAN1",
            result=result,
            reason="canary completed",
            now=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        )
        payload = updated.live_evidence["canary"]
        assert payload["candidate_id"] == "STRAT-CAN"
        assert payload["authorized"] is True
        assert payload["days_run"] == 21
        assert payload["action"] == "production_ready"
        assert len(payload["periods"]) == 3
        assert payload["periods"][-1]["reason"] == "gate granted"
        assert updated.status is PassportStatus.CANDIDATE

    def test_canary_update_is_on_the_audit_trail(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAN2",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        svc.record_canary("STRAT-CAN2", result=self.canary_result(), reason="canary completed")
        events = svc.lifecycle("STRAT-CAN2")
        assert events[-1].event_type == "canary_update"
        assert events[-1].reason == "canary completed"

    def test_mapping_result_accepted(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAN3",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        updated = svc.record_canary(
            "STRAT-CAN3",
            result={"days_run": 5, "action": "retired", "authorized": True},
            reason="stay-limit breach",
        )
        assert updated.live_evidence["canary"]["action"] == "retired"

    def test_unknown_passport_refused(self, tmp_path):
        svc = engine(tmp_path)
        with pytest.raises(ValueError, match="unknown passport"):
            svc.record_canary("STRAT-NOPE", result=self.canary_result(), reason="x")

    def test_payload_survives_sqlite_roundtrip(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-CAN4",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        svc.record_canary("STRAT-CAN4", result=self.canary_result(), reason="done")
        restored = svc.passport("STRAT-CAN4")
        assert restored is not None
        assert restored.live_evidence["canary"]["days_run"] == 21
        assert restored.live_evidence["canary"]["periods"][1]["day"] == 20


class TestRollbackEvidence:
    def rollback_decision(self) -> Any:
        from backend.domain.research.promotion import ModelEnvironment, RollbackDecision

        return RollbackDecision(
            candidate_id="STRAT-RB",
            rollback=True,
            to_environment=ModelEnvironment.VALIDATION,
            reasons=("paper campaign retired: drawdown",),
        )

    def test_rollback_record_appends_to_live_evidence(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RB1",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        updated = svc.record_rollback(
            "STRAT-RB1",
            decision=self.rollback_decision(),
            reason="automatic rollback",
            now=datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        )
        payload = updated.live_evidence["rollback"]
        assert payload["rollback"] is True
        assert payload["to_environment"] == "validation"
        assert payload["reasons"] == ["paper campaign retired: drawdown"]
        assert updated.status is PassportStatus.CANDIDATE

    def test_rollback_update_is_on_the_audit_trail(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RB2",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        svc.record_rollback("STRAT-RB2", decision=self.rollback_decision(), reason="rollback")
        events = svc.lifecycle("STRAT-RB2")
        assert events[-1].event_type == "rollback_update"
        assert events[-1].reason == "rollback"

    def test_mapping_decision_accepted(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RB3",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        updated = svc.record_rollback(
            "STRAT-RB3",
            decision={"rollback": True, "to_environment": None, "reasons": ["decay"]},
            reason="terminal",
        )
        assert updated.live_evidence["rollback"]["to_environment"] is None

    def test_unknown_passport_refused(self, tmp_path):
        svc = engine(tmp_path)
        with pytest.raises(ValueError, match="unknown passport"):
            svc.record_rollback("STRAT-NOPE", decision=self.rollback_decision(), reason="x")

    def test_payload_survives_sqlite_roundtrip(self, tmp_path):
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RB4",
            **base_kwargs(),
            report=report_with(good_evidence()),
        )
        svc.record_rollback("STRAT-RB4", decision=self.rollback_decision(), reason="done")
        restored = svc.passport("STRAT-RB4")
        assert restored is not None
        assert restored.live_evidence["rollback"]["rollback"] is True
        assert restored.live_evidence["rollback"]["to_environment"] == "validation"


class TestTerminalRetirement:
    """T3-28-1: RETIRED is a tombstone — terminality enforced by the engine.

    A retired passport can never be transitioned, re-evaluated,
    re-calibrated, or handed a new campaign. Only the rollback record (the
    death audit closing) remains appendable. The dead hypothesis must be
    revised and re-issued as a NEW passport.
    """

    def retire(self, svc: EvidenceEngine, passport_id: str) -> None:
        # Engine-native death: re-verdict with failing evidence -> REJECT -> RETIRED.
        svc.rerecord_evidence(
            passport_id,
            report=report_with(_with(good_evidence(), deflated_sharpe=-1.0)),
            reason="rejection gate",
        )

    def test_transition_refuses_retired(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RET1", **base_kwargs(), report=report_with(good_evidence())
        )
        self.retire(svc, "STRAT-RET1")
        with pytest.raises(ValueError, match="retired"):
            svc.transition("STRAT-RET1", to_status=PassportStatus.CANDIDATE, reason="resurrect")

    def test_rerecord_refuses_retired(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RET2", **base_kwargs(), report=report_with(good_evidence())
        )
        self.retire(svc, "STRAT-RET2")
        with pytest.raises(ValueError, match="retired"):
            svc.rerecord_evidence(
                "STRAT-RET2", report=report_with(good_evidence()), reason="re-verify corpse"
            )

    def test_paper_campaign_refuses_retired(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RET3", **base_kwargs(), report=report_with(good_evidence())
        )
        self.retire(svc, "STRAT-RET3")
        with pytest.raises(ValueError, match="retired"):
            svc.record_paper_campaign(
                "STRAT-RET3",
                result={"action": "completed_advanced", "days_run": 5},
                reason="new campaign on corpse",
            )

    def test_canary_refuses_retired(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RET4", **base_kwargs(), report=report_with(good_evidence())
        )
        self.retire(svc, "STRAT-RET4")
        with pytest.raises(ValueError, match="retired"):
            svc.record_canary(
                "STRAT-RET4",
                result={"verdict": "retired", "days_run": 3, "actions": (), "reasons": ()},
                reason="new canary on corpse",
            )

    def test_calibration_refuses_retired(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RET5", **base_kwargs(), report=report_with(good_evidence())
        )
        self.retire(svc, "STRAT-RET5")
        with pytest.raises(ValueError, match="retired"):
            svc.record_calibration(
                "STRAT-RET5", report={"impact_bps": 1.2}, reason="calibrate corpse"
            )

    def test_rollback_still_recorded_on_retired(self, tmp_path: object) -> None:
        # The audit trail may always append the reason a strategy died:
        # the death system records the rollback right after the RETIRE
        # transition, so this must stay legal on the tombstone.
        svc = engine(tmp_path)
        svc.issue_passport(
            passport_id="STRAT-RET6", **base_kwargs(), report=report_with(good_evidence())
        )
        self.retire(svc, "STRAT-RET6")
        updated = svc.record_rollback(
            "STRAT-RET6",
            decision={"rollback": True, "to_environment": None, "reasons": ["terminal"]},
            reason="closing the death record",
        )
        assert updated.status is PassportStatus.RETIRED
        assert updated.live_evidence["rollback"]["reasons"] == ["terminal"]
        assert svc.lifecycle("STRAT-RET6")[-1].event_type == "rollback_update"

    def test_unknown_passport_still_refused(self, tmp_path: object) -> None:
        svc = engine(tmp_path)
        with pytest.raises(ValueError, match="unknown passport"):
            svc.transition("STRAT-NOPE", to_status=PassportStatus.RETIRED, reason="x")


class TestEvidenceEngineEndToEnd:
    def test_real_evaluator_report_composes_into_passport(self, tmp_path):
        events = _market()
        evaluator = DecisionPipelineEvaluator(
            cv=WalkForwardCV(train_size=80, test_size=20, embargo=3.0)
        )
        report = evaluator.evaluate(events)
        svc = engine(tmp_path)
        passport = svc.issue_passport(
            passport_id="STRAT-E2E",
            **base_kwargs(),
            report=report,
            now=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )
        assert passport.verdict.verdict in (
            EvidenceVerdict.REJECT,
            EvidenceVerdict.OBSERVE,
            EvidenceVerdict.PROMOTE_TO_PAPER,
        )
        assert passport.evidence["pooled"]["n_folds"] == report.pooled.n_folds
        assert passport.evidence["cv_spec"]["train_size"] == 80
        # T1-6-1: the gap/embargo that was applied must be in the report
        assert passport.evidence["cv_spec"]["embargo"] == 3.0
        assert passport.evidence["cv_spec"]["method"] == "walk_forward"
        restored = svc.passport("STRAT-E2E")
        assert restored is not None
        assert restored == passport
        assert restored.evidence["pooled"] == passport.evidence["pooled"]


def _with(evidence: PooledEvidence, **changes) -> PooledEvidence:
    import dataclasses

    return dataclasses.replace(evidence, **changes)


def _market(seed: int = 11, n: int = 280) -> list[ObservationEvent]:
    """Seeded deterministic synthetic trade series (same shape as the
    evaluator's own tests, kept local to avoid cross-test coupling)."""
    import random
    from datetime import timedelta

    rng = random.Random(seed)
    price = 100.0
    events: list[ObservationEvent] = []
    t = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        price *= 1.0 + rng.gauss(0.0003, 0.004)
        events.append(
            ObservationEvent(
                source_id="synthetic",
                source_name="Synthetic",
                event_type=ObservationEventType.TRADE,
                timestamp=t,
                payload={
                    "symbol": "btcusdt",
                    "trade_id": i,
                    "price": round(price, 4),
                    "quantity": 1.0,
                },
            )
        )
        t += timedelta(seconds=5)
    return events
