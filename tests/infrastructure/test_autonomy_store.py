"""Tests for the SQLite autonomy store (WS1).

The outcome corpus must guarantee:

1. Immutable keys — a campaign, day, or program run can never be written twice.
2. Forward-only campaign lifecycle — PENDING -> RUNNING -> terminal, and a
   terminal campaign is never reopened.
3. Complete day outcomes — the corpus records every day exactly once, so a
   finished campaign's stored days match its actual run.
4. A full audit trail — promotion decisions and rollbacks append-only, never
   deleted, retrievable by candidate.
"""

from __future__ import annotations

import pytest
from backend.domain.research.paper_campaign import PaperCampaignAction
from backend.domain.research.promotion import ModelEnvironment
from backend.domain.research.records import (
    CampaignRunRecord,
    CampaignStatus,
    DayOutcomeRecord,
    ProgramRunRecord,
    PromotionAction,
    PromotionDecisionRecord,
    RollbackRecord,
    StageSnapshot,
)
from backend.infrastructure.sqlite.autonomy_repository import SqliteAutonomyStore
from backend.infrastructure.sqlite.database import Database


@pytest.fixture
def store(tmp_path) -> SqliteAutonomyStore:
    return SqliteAutonomyStore(Database(tmp_path / "autonomy.db"))


def make_campaign(
    campaign_id: str = "camp-1",
    candidate_id: str = "model-a",
    status: CampaignStatus = CampaignStatus.PENDING,
    target_days: int = 30,
    days_run: int = 0,
) -> CampaignRunRecord:
    return CampaignRunRecord(
        candidate_id=candidate_id,
        campaign_id=campaign_id,
        status=status,
        target_days=target_days,
        days_run=days_run,
    )


def make_day(
    day: int,
    campaign_id: str = "camp-1",
    candidate_id: str = "model-a",
) -> DayOutcomeRecord:
    return DayOutcomeRecord(
        candidate_id=candidate_id,
        campaign_id=campaign_id,
        day=day,
        return_pct=0.1,
        expected_return_pct=0.05,
        failed_orders=1,
        total_orders=10,
        recorded_at="2026-08-13T00:00:00.000+00:00",
    )


def make_program_run(program_id: str = "prog-1", candidate_id: str = "model-a") -> ProgramRunRecord:
    return ProgramRunRecord(
        program_id=program_id,
        candidate_id=candidate_id,
        final_environment="production",
        earned=("research", "validation", "paper", "canary", "production"),
        stages=(
            StageSnapshot("research", "passed", "earned it"),
            StageSnapshot("production", "passed", "earned it"),
        ),
        notes=("clean run",),
        started_at="2026-08-13T00:00:00.000+00:00",
        completed_at="2026-08-13T12:00:00.000+00:00",
    )


class TestCampaignLifecycle:
    def test_save_and_get(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign())
        stored = store.get_campaign("camp-1")
        assert stored is not None
        assert stored.candidate_id == "model-a"
        assert stored.status is CampaignStatus.PENDING
        assert stored.terminal is False

    def test_duplicate_campaign_rejected(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign())
        with pytest.raises(ValueError, match="already exists"):
            store.save_campaign(make_campaign())

    def test_forward_transition_pending_to_running(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign())
        stored = store.set_campaign_status("camp-1", CampaignStatus.RUNNING)
        assert stored.status is CampaignStatus.RUNNING

    def test_terminal_campaign_never_reopened(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign())
        store.set_campaign_status("camp-1", CampaignStatus.COMPLETED, action="completed_advanced")
        with pytest.raises(ValueError, match="terminal"):
            store.set_campaign_status("camp-1", CampaignStatus.RUNNING)

    def test_retired_never_reopened(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign())
        store.set_campaign_status("camp-1", CampaignStatus.RETIRED, action="retired")
        with pytest.raises(ValueError, match="terminal"):
            store.set_campaign_status("camp-1", CampaignStatus.CANCELLED)

    def test_cannot_reopen_to_pending(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign())
        store.set_campaign_status("camp-1", CampaignStatus.RUNNING)
        with pytest.raises(ValueError, match="pending"):
            store.set_campaign_status("camp-1", CampaignStatus.PENDING)

    def test_unknown_campaign_transition_rejected(self, store: SqliteAutonomyStore) -> None:
        with pytest.raises(ValueError, match="unknown campaign"):
            store.set_campaign_status("nope", CampaignStatus.RUNNING)

    def test_status_update_preserves_metrics(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign(days_run=0))
        store.set_campaign_status(
            "camp-1",
            CampaignStatus.RUNNING,
            days_run=5,
        )
        stored = store.set_campaign_status(
            "camp-1",
            CampaignStatus.COMPLETED,
            days_run=30,
            sharpe=1.2,
            drawdown_pct=3.5,
            action="completed_advanced",
            reason="clean window",
            completed_at="2026-08-13T12:00:00.000+00:00",
        )
        assert stored.sharpe == 1.2
        assert stored.drawdown_pct == 3.5
        assert stored.action is PaperCampaignAction.COMPLETED_ADVANCED
        assert stored.reason == "clean window"
        assert stored.terminal is True

    def test_list_filters_candidate_and_status(self, store: SqliteAutonomyStore) -> None:
        store.save_campaign(make_campaign("camp-1", "model-a", CampaignStatus.PENDING))
        store.save_campaign(make_campaign("camp-2", "model-a", CampaignStatus.RUNNING))
        store.save_campaign(make_campaign("camp-3", "model-b", CampaignStatus.PENDING))
        assert len(store.list_campaigns(candidate_id="model-a")) == 2
        assert len(store.list_campaigns(status=CampaignStatus.PENDING)) == 2
        assert len(store.list_campaigns(candidate_id="model-a", status=CampaignStatus.RUNNING)) == 1
        assert store.list_campaigns()[0].campaign_id == "camp-3"  # newest first


class TestDayOutcomes:
    def test_save_and_list_ordered_by_day(self, store: SqliteAutonomyStore) -> None:
        for day in (2, 1, 3):
            store.save_day_outcome(make_day(day))
        days = store.list_day_outcomes(campaign_id="camp-1")
        assert [d.day for d in days] == [1, 2, 3]

    def test_day_written_once(self, store: SqliteAutonomyStore) -> None:
        store.save_day_outcome(make_day(1))
        with pytest.raises(ValueError, match="already recorded"):
            store.save_day_outcome(make_day(1))

    def test_filter_by_candidate(self, store: SqliteAutonomyStore) -> None:
        store.save_day_outcome(make_day(1))
        store.save_day_outcome(make_day(1, campaign_id="camp-2", candidate_id="model-b"))
        assert len(store.list_day_outcomes(candidate_id="model-b")) == 1


class TestProgramRuns:
    def test_save_and_round_trip(self, store: SqliteAutonomyStore) -> None:
        store.save_program_run(make_program_run())
        stored = store.get_program_run("prog-1")
        assert stored is not None
        assert stored.candidate_id == "model-a"
        assert stored.final_environment == "production"
        assert stored.earned == ("research", "validation", "paper", "canary", "production")
        assert [s.stage for s in stored.stages] == ["research", "production"]
        assert stored.notes == ("clean run",)

    def test_duplicate_program_rejected(self, store: SqliteAutonomyStore) -> None:
        store.save_program_run(make_program_run())
        with pytest.raises(ValueError, match="already exists"):
            store.save_program_run(make_program_run())

    def test_list_filters(self, store: SqliteAutonomyStore) -> None:
        store.save_program_run(make_program_run("prog-1", "model-a"))
        store.save_program_run(make_program_run("prog-2", "model-b"))
        assert len(store.list_program_runs(candidate_id="model-b")) == 1
        assert len(store.list_program_runs(final_environment="production")) == 2
        assert store.list_program_runs()[0].program_id == "prog-2"  # newest first


class TestAuditTrail:
    def test_promotion_decision_appends(self, store: SqliteAutonomyStore) -> None:
        first = PromotionDecisionRecord(
            candidate_id="model-a",
            action=PromotionAction.EVALUATE,
            environment=ModelEnvironment.PAPER,
            allowed=False,
            required=("validation sample count",),
            satisfied=(),
            reasons=("validation sample count",),
            occurred_at="2026-08-13T00:00:00.000+00:00",
        )
        store.save_promotion_decision(first)
        granted = PromotionDecisionRecord(
            candidate_id="model-a",
            action=PromotionAction.PROMOTE,
            environment=ModelEnvironment.PAPER,
            allowed=True,
            occurred_at="2026-08-13T01:00:00.000+00:00",
        )
        store.save_promotion_decision(granted)
        all_decisions = store.list_promotion_decisions(candidate_id="model-a")
        assert len(all_decisions) == 2
        assert all_decisions[0].allowed is True  # newest first
        assert store.list_promotion_decisions(candidate_id="model-b") == []

    def test_rollback_appends(self, store: SqliteAutonomyStore) -> None:
        store.save_rollback(
            RollbackRecord(
                candidate_id="model-a",
                from_environment=ModelEnvironment.CANARY,
                to_environment=ModelEnvironment.PAPER,
                reasons=("drawdown 12.00% > 10.00%",),
                occurred_at="2026-08-13T00:00:00.000+00:00",
            )
        )
        store.save_rollback(
            RollbackRecord(
                candidate_id="model-a",
                from_environment=ModelEnvironment.PAPER,
                to_environment=ModelEnvironment.VALIDATION,
                reasons=("failed orders 8.00% > 5.00%",),
                occurred_at="2026-08-13T02:00:00.000+00:00",
            )
        )
        rollbacks = store.list_rollbacks(candidate_id="model-a")
        assert len(rollbacks) == 2
        assert rollbacks[0].to_environment is ModelEnvironment.VALIDATION  # newest first
        assert rollbacks[1].reasons == ("drawdown 12.00% > 10.00%",)
