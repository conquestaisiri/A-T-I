"""Tests for PaperCampaignService: the durable campaign lifecycle (WS2.3).

The service composes the state machine, the store, and a day-function into the
operator-observable campaign lifecycle: create -> start -> run -> terminal,
with cancellation honoured at day boundaries and never reopening a finished
campaign.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest
from backend.application.research.paper_campaign_service import (
    CampaignAlreadyFinished,
    CampaignCancelled,
    PaperCampaignService,
)
from backend.domain.research.paper_campaign import PaperDayOutcome
from backend.domain.research.promotion import CandidateEvidence
from backend.domain.research.records import CampaignRunRecord, CampaignStatus
from backend.infrastructure.sqlite.autonomy_repository import SqliteAutonomyStore
from backend.infrastructure.sqlite.database import Database

T0 = "2026-08-13T00:00:00.000+00:00"


@pytest.fixture
def store(tmp_path) -> SqliteAutonomyStore:
    return SqliteAutonomyStore(Database(tmp_path / "campaigns.db"))


@pytest.fixture
def clock() -> Callable[[], str]:
    times = [
        T0,
        "2026-08-13T00:00:01.000+00:00",
        "2026-08-13T00:00:02.000+00:00",
    ]
    state = {"i": 0}

    def next_tick() -> str:
        tick = times[state["i"] % len(times)]
        state["i"] += 1
        return tick

    return next_tick


@pytest.fixture
def service(store, clock) -> PaperCampaignService:
    return PaperCampaignService(store=store, clock=clock)


def evidence(**overrides: int | None) -> CandidateEvidence:
    defaults = {
        "candidate_id": "model-a",
        "validation_samples": 300,
        "validation_sharpe": 0.7,
    }
    defaults.update(overrides)
    return CandidateEvidence(**defaults)


def flat_day(day: int) -> PaperDayOutcome:
    return PaperDayOutcome(
        day=day,
        return_pct=0.0625,  # a dull but positive daily return
        expected_return_pct=0.05,
        failed_orders=0,
        total_orders=3,
    )


def _created(service: PaperCampaignService, *, campaign_id: str = "camp-1") -> CampaignRunRecord:
    return service.create_campaign(
        candidate_id="model-a",
        campaign_id=campaign_id,
        target_days=5,
        initial_evidence=evidence(),
    )


class TestCreate:
    def test_creates_pending_record(self, service, store):
        record = _created(service)
        assert record.status is CampaignStatus.PENDING
        assert record.target_days == 5
        stored = store.get_campaign("camp-1")
        assert stored is not None
        assert stored.status is CampaignStatus.PENDING

    def test_negative_window_rejected(self, service):
        with pytest.raises(ValueError):
            service.create_campaign(
                candidate_id="model-a",
                campaign_id="bad",
                target_days=0,
                initial_evidence=evidence(),
            )

    def test_duplicate_id_rejected(self, service):
        _created(service)
        with pytest.raises(ValueError):
            _created(service)


class TestStart:
    def test_pending_to_running(self, service, store):
        _created(service)
        service.start_campaign("camp-1")
        assert store.get_campaign("camp-1").status is CampaignStatus.RUNNING

    def test_start_is_idempotent(self, service, store):
        _created(service)
        service.start_campaign("camp-1")
        service.start_campaign("camp-1")  # a retried tick must not crash
        assert store.get_campaign("camp-1").status is CampaignStatus.RUNNING

    def test_cannot_start_unknown_campaign(self, service):
        with pytest.raises(ValueError):
            service.start_campaign("nope")


class TestRun:
    def test_full_window_completes_campaign(self, service, store):
        _created(service)
        result = service.run_campaign(
            campaign_id="camp-1",
            candidate_id="model-a",
            initial_evidence=evidence(),
            day_fn=flat_day,
        )
        assert result.days_run == 5
        record = store.get_campaign("camp-1")
        assert record.status is CampaignStatus.COMPLETED
        assert record.days_run == 5
        assert record.sharpe is not None

    def test_every_day_is_persisted(self, service, store):
        _created(service)
        service.run_campaign(
            campaign_id="camp-1",
            candidate_id="model-a",
            initial_evidence=evidence(),
            day_fn=flat_day,
        )
        days = store.list_day_outcomes(campaign_id="camp-1")
        assert [d.day for d in days] == [1, 2, 3, 4, 5]
        assert all(d.total_orders == 3 for d in days)

    def test_run_starts_an_unstarted_campaign(self, service, store):
        _created(service)
        service.run_campaign(
            campaign_id="camp-1",
            candidate_id="model-a",
            initial_evidence=evidence(),
            day_fn=flat_day,
        )
        record = store.get_campaign("camp-1")
        # running once implicitly started it, and it reached terminal
        assert record.status in (CampaignStatus.COMPLETED, CampaignStatus.RETIRED)

    def test_run_after_terminal_raises(self, service):
        _created(service)
        service.run_campaign(
            campaign_id="camp-1",
            candidate_id="model-a",
            initial_evidence=evidence(),
            day_fn=flat_day,
        )
        with pytest.raises(CampaignAlreadyFinished):
            service.run_campaign(
                campaign_id="camp-1",
                candidate_id="model-a",
                initial_evidence=evidence(),
                day_fn=flat_day,
            )

    def test_run_unknown_campaign_raises(self, service):
        with pytest.raises(ValueError):
            service.run_campaign(
                campaign_id="ghost",
                candidate_id="model-a",
                initial_evidence=evidence(),
                day_fn=flat_day,
            )


class TestCancel:
    def test_cancel_pending(self, service, store):
        _created(service)
        service.cancel_campaign("camp-1")
        assert store.get_campaign("camp-1").status is CampaignStatus.CANCELLED

    def test_cancel_running(self, service, store):
        _created(service)
        service.start_campaign("camp-1")
        service.cancel_campaign("camp-1")
        assert store.get_campaign("camp-1").status is CampaignStatus.CANCELLED

    def test_cancel_terminal_raises(self, service, store):
        _created(service)
        service.run_campaign(
            campaign_id="camp-1",
            candidate_id="model-a",
            initial_evidence=evidence(),
            day_fn=flat_day,
        )
        assert store.get_campaign("camp-1").terminal
        with pytest.raises(ValueError):
            service.cancel_campaign("camp-1")

    def test_cancelled_campaign_cannot_run(self, service, store):
        _created(service)
        service.cancel_campaign("camp-1")
        with pytest.raises(CampaignAlreadyFinished):
            service.run_campaign(
                campaign_id="camp-1",
                candidate_id="model-a",
                initial_evidence=evidence(),
                day_fn=flat_day,
            )
        assert store.get_campaign("camp-1").status is CampaignStatus.CANCELLED

    def test_cancel_mid_run_never_finishes(self, service, store):
        _created(service)
        service.start_campaign("camp-1")

        def cancelling_day(day: int) -> PaperDayOutcome:
            if day == 2:
                service.cancel_campaign("camp-1")
            return flat_day(day)

        with pytest.raises(CampaignCancelled):
            service.run_campaign(
                campaign_id="camp-1",
                candidate_id="model-a",
                initial_evidence=evidence(),
                day_fn=cancelling_day,
            )
        record = store.get_campaign("camp-1")
        assert record.status is CampaignStatus.CANCELLED
        # days 1 and 2 are recorded, but the cancelled run never reaches terminal
        assert len(store.list_day_outcomes(campaign_id="camp-1")) == 2
