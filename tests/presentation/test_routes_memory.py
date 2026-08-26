"""Tests for the episodic memory and reflection API routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from backend.application.context.bootstrap import build_reflection_service
from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.interfaces.proposal_repository import ProposalRepository
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.execution.order import OrderSide
from backend.domain.execution.trade_record import TradeRecord, TradeStatus
from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome
from backend.infrastructure.config.settings import settings
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository
from backend.presentation.api.routes_memory import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def ts() -> datetime:
    return datetime(2026, 2, 12, 10, 0, 0, tzinfo=UTC)


def make_proposal() -> DecisionProposal:
    return DecisionProposal(
        proposal_id="prop-1",
        correlation_id="corr-1",
        created_at=ts(),
        symbol="btcusdt",
        hypothesis=Hypothesis(
            statement="trend",
            supporting_evidence=(EvidenceItem(source="trend", summary="up", value=1.0),),
            opposing_evidence=(),
        ),
        confidence=0.8,
        uncertainty="none",
        actions=(
            ProposedAction(
                action_type=ProposedActionType.ENTER_LONG,
                size_fraction=0.1,
                order=1,
                rationale="go",
            ),
        ),
        risk_context=RiskContext(
            account_equity=100_000.0,
            open_exposure_pct=0.0,
            daily_loss_pct=0.0,
            monthly_loss_pct=0.0,
            total_loss_pct=0.0,
            drawdown_pct=0.0,
            position_count=0,
        ),
        alternatives=(),
        rationale="go",
    )


def make_episode(episode_id: str = "ep-1") -> MemoryEpisode:
    return MemoryEpisode(
        episode_id=episode_id,
        correlation_id="corr-1",
        symbol="btcusdt",
        created_at=ts(),
        proposal_id="prop-1",
        action_type="enter_long",
        confidence=0.9,
        outcome=MemoryOutcome.WIN,
        realized_pnl=100.0,
        summary="win",
    )


def closed_trade(trade_id: str = "trade-1") -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        proposal_id="prop-1",
        correlation_id="corr-1",
        symbol="btcusdt",
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=100.0,
        opened_at=ts(),
        exit_price=110.0,
        closed_at=ts(),
        realized_pnl=100.0,
        status=TradeStatus.CLOSED,
    )


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database = Database(tmp_path / "memory-api.db")
    memory = SqliteMemoryRepository(database)
    ledger: LedgerRepository = SqliteLedgerRepository(database)
    proposals: ProposalRepository = SqliteProposalRepository(database)

    app = FastAPI()
    app.include_router(router)
    app.state.memory_store = memory
    app.state.ledger = ledger
    app.state.proposals = proposals
    app.state.reflection = build_reflection_service(tmp_path / "memory-api.db", memory_store=memory)

    with TestClient(app) as test_client:
        yield test_client

    database.close()


class TestMemoryAPI:
    def test_count_empty(self, client):
        response = client.get("/v1/memory/count?symbol=btcusdt")
        assert response.status_code == 200
        assert response.json()["count"] == 0

    def test_count_all(self, client):
        client.app.state.memory_store.record(make_episode())
        response = client.get("/v1/memory/count")
        assert response.status_code == 200
        assert response.json()["count"] == 1

    def test_recall_empty(self, client):
        response = client.get("/v1/memory/recall?symbol=btcusdt")
        assert response.status_code == 200
        assert response.json()["episodes"] == []

    def test_recall_returns_saved(self, client):
        client.app.state.memory_store.record(make_episode())
        response = client.get("/v1/memory/recall?symbol=btcusdt")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["episodes"]) == 1
        assert payload["episodes"][0]["episode_id"] == "ep-1"

    def test_recall_rejects_empty_symbol(self, client):
        response = client.get("/v1/memory/recall?symbol=")
        assert response.status_code == 422


class TestReflectionAPI:
    def test_reflect_backfills_memory_from_ledger(self, client):
        client.app.state.proposals.save(make_proposal())
        client.app.state.ledger.save(closed_trade())

        response = client.post("/v1/reflection/reflect?symbol=btcusdt")
        assert response.status_code == 200
        payload = response.json()
        assert payload["trades_scanned"] == 1
        assert payload["episodes_recorded"] == 1
        assert payload["wins"] == 1
        assert client.app.state.memory_store.count("btcusdt") == 1

    def test_reflect_is_idempotent(self, client):
        client.app.state.proposals.save(make_proposal())
        client.app.state.ledger.save(closed_trade())

        client.post("/v1/reflection/reflect?symbol=btcusdt")
        client.post("/v1/reflection/reflect?symbol=btcusdt")

        assert client.app.state.memory_store.count("btcusdt") == 1


class TestMemoryAPIAuth:
    def test_reflection_trigger_requires_api_key_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "api_key", "secret-key")
        try:
            response = client.post("/v1/reflection/reflect?symbol=btcusdt")
            assert response.status_code == 401

            response = client.post(
                "/v1/reflection/reflect?symbol=btcusdt",
                headers={"X-API-Key": "secret-key"},
            )
            assert response.status_code == 200
        finally:
            monkeypatch.setattr(settings, "api_key", None)

    def test_memory_recall_requires_api_key_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "api_key", "secret-key")
        try:
            response = client.get("/v1/memory/recall?symbol=btcusdt")
            assert response.status_code == 401

            response = client.get(
                "/v1/memory/recall?symbol=btcusdt",
                headers={"X-API-Key": "secret-key"},
            )
            assert response.status_code == 200
        finally:
            monkeypatch.setattr(settings, "api_key", None)
