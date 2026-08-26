"""Tests for outcome aggregation over the autonomy corpus (WS2.4).

Aggregation turns stored immutable records into the operator-observable
picture: verdict counts, days run, best sharpe, completeness of each finished
campaign's day corpus, and in-flight campaigns that have not yet decided.
"""

from __future__ import annotations

import pytest
from backend.application.research.outcome_aggregation import (
    campaign_summary,
    candidate_outcomes,
    corpus_outcomes,
)
from backend.domain.research.paper_campaign import PaperCampaignAction
from backend.domain.research.records import (
    CampaignRunRecord,
    CampaignStatus,
    DayOutcomeRecord,
)
from backend.infrastructure.sqlite.autonomy_repository import SqliteAutonomyStore
from backend.infrastructure.sqlite.database import Database


@pytest.fixture
def store(tmp_path) -> SqliteAutonomyStore:
    return SqliteAutonomyStore(Database(tmp_path / "corpus.db"))


def make_campaign(
    campaign_id: str,
    *,
    candidate_id: str = "model-a",
    status: CampaignStatus = CampaignStatus.COMPLETED,
    target_days: int = 30,
    days_run: int = 30,
    sharpe: float | None = 1.2,
    action: PaperCampaignAction | None = None,
    drawdown_pct: float | None = 8.0,
) -> CampaignRunRecord:
    if action is None:
        action = (
            PaperCampaignAction.COMPLETED_ADVANCED if status is CampaignStatus.COMPLETED else None
        )
    return CampaignRunRecord(
        candidate_id=candidate_id,
        campaign_id=campaign_id,
        status=status,
        action=action,
        target_days=target_days,
        days_run=days_run,
        sharpe=sharpe,
        drawdown_pct=drawdown_pct,
        started_at="2026-08-13T00:00:00.000+00:00",
    )


def fill_days(store: SqliteAutonomyStore, campaign_id: str, candidate_id: str, n: int) -> None:
    for day in range(1, n + 1):
        store.save_day_outcome(
            DayOutcomeRecord(
                candidate_id=candidate_id,
                campaign_id=campaign_id,
                day=day,
                return_pct=0.05,
                expected_return_pct=0.04,
                total_orders=2,
            )
        )


class TestCampaignSummary:
    def test_unknown_campaign_returns_none(self, store):
        assert campaign_summary(store, "ghost") is None

    def test_complete_campaign_reported_complete(self, store):
        store.save_campaign(make_campaign("c1"))
        fill_days(store, "c1", "model-a", 30)
        summary = campaign_summary(store, "c1")
        assert summary is not None
        assert summary.complete is True
        assert summary.stored_days == 30
        assert summary.days_run == 30

    def test_missing_days_is_a_gap(self, store):
        store.save_campaign(make_campaign("c1", days_run=30))
        fill_days(store, "c1", "model-a", 10)
        summary = campaign_summary(store, "c1")
        assert summary is not None
        assert summary.complete is False
        assert summary.stored_days == 10


class TestCandidateOutcomes:
    def test_empty_candidate(self, store):
        outcomes = candidate_outcomes(store, "nobody")
        assert outcomes.completed == 0
        assert outcomes.total_days_run == 0
        assert outcomes.days_run_ratio == 0.0

    def test_counts_terminal_verdicts(self, store):
        store.save_campaign(make_campaign("c1", status=CampaignStatus.COMPLETED))
        store.save_campaign(make_campaign("c2", status=CampaignStatus.RETIRED))
        store.save_campaign(
            make_campaign(
                "c3",
                status=CampaignStatus.CANCELLED,
                days_run=7,
                sharpe=None,
                drawdown_pct=None,
            )
        )
        outcomes = candidate_outcomes(store, "model-a")
        assert outcomes.completed == 1
        assert outcomes.retired == 1
        assert outcomes.cancelled == 1
        assert outcomes.total_days_run == 67
        assert outcomes.best_sharpe == 1.2

    def test_in_flight_is_not_a_verdict(self, store):
        store.save_campaign(make_campaign("c1", status=CampaignStatus.RUNNING, days_run=5))
        store.save_campaign(make_campaign("c2", status=CampaignStatus.PENDING, days_run=0))
        outcomes = candidate_outcomes(store, "model-a")
        assert outcomes.in_flight == 2
        assert outcomes.completed == 0
        # in-flight days are not verdict days
        assert outcomes.total_days_run == 0

    def test_days_run_ratio(self, store):
        store.save_campaign(make_campaign("c1", target_days=30, days_run=15))
        store.save_campaign(make_campaign("c2", target_days=30, days_run=30))
        outcomes = candidate_outcomes(store, "model-a")
        assert outcomes.total_target_days == 60
        assert outcomes.total_days_run == 45
        assert outcomes.days_run_ratio == 0.75

    def test_gaps_collect_incomplete_campaigns(self, store):
        store.save_campaign(make_campaign("c1"))
        fill_days(store, "c1", "model-a", 30)
        store.save_campaign(make_campaign("c2", days_run=30))
        fill_days(store, "c2", "model-a", 12)
        outcomes = candidate_outcomes(store, "model-a")
        assert outcomes.complete_campaigns == 1
        assert outcomes.gaps == ("c2",)

    def test_best_sharpe_ignores_empty(self, store):
        store.save_campaign(make_campaign("c1", sharpe=None, status=CampaignStatus.RETIRED))
        store.save_campaign(make_campaign("c2", sharpe=0.9, status=CampaignStatus.COMPLETED))
        outcomes = candidate_outcomes(store, "model-a")
        assert outcomes.best_sharpe == 0.9


class TestCorpusOutcomes:
    def test_aggregates_all_candidates(self, store):
        store.save_campaign(make_campaign("a1", candidate_id="model-a"))
        store.save_campaign(make_campaign("a2", candidate_id="model-a"))
        store.save_campaign(
            make_campaign("b1", candidate_id="model-b", status=CampaignStatus.RETIRED)
        )
        store.save_campaign(
            make_campaign("b2", candidate_id="model-b", status=CampaignStatus.PENDING, days_run=0)
        )
        totals = corpus_outcomes(store)
        assert totals.candidates == 2
        assert totals.campaigns == 4
        assert totals.completed == 2
        assert totals.retired == 1
        assert totals.cancelled == 0
        assert totals.in_flight == 1

    def test_empty_corpus(self, store):
        totals = corpus_outcomes(store)
        assert totals.candidates == 0
        assert totals.campaigns == 0
        assert totals.total_days_run == 0
