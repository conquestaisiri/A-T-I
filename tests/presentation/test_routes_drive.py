"""Tests for the operator drive route."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from backend.application.context.bootstrap import (
    _build_reflection,
    build_context_pipeline_from_config,
    build_observation_enrichment,
)
from backend.application.decision.rule_based_solver import RuleBasedSolver
from backend.application.pipeline.context_pipeline_service import ContextPipelineService
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository
from backend.presentation.api.routes_context import router as observability_router
from backend.presentation.api.routes_drive import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database = Database(tmp_path / "drive-api.db")
    ledger_repo = SqliteLedgerRepository(database)
    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=CircuitBreakerRiskGate(),
        order_gateway=fill_engine,
        ledger=ledger_repo,
    )
    supervisor = SupervisorService()
    pipeline = DecisionPipelineService(
        reasoner=RuleBasedSolver(),
        proposal_repository=SqliteProposalRepository(database),
        simulator=simulator,
        reflection=_build_reflection(database, SqliteMemoryRepository(database)),
        supervisor=supervisor,
    )

    app = FastAPI()
    app.include_router(router)
    app.include_router(observability_router)
    context_builder = build_context_pipeline_from_config()[0]
    ingest = ContextPipelineService(
        bus=ObservationBus(maxsize=1024),
        context_builder=context_builder,
        observation_repository=SqliteObservationRepository(database),
        context_repository=SqliteContextRepository(database),
        supervisor=supervisor,
        enrichment=build_observation_enrichment(),
    )
    app.state.context_builder = context_builder
    app.state.ingest_pipeline = ingest
    app.state.observation_repository = SqliteObservationRepository(database)
    app.state.context_repository = SqliteContextRepository(database)
    app.state.decision_pipeline = pipeline
    app.state.fill_engine = fill_engine

    with TestClient(app) as test_client:
        yield test_client

    database.close()


class TestDriveRoute:
    def test_run_through_loop(self, client):
        response = client.post("/v1/drive", json={"symbol": "btcusdt", "price": 100.0})
        assert response.status_code == 200
        payload = response.json()
        assert payload["symbol"] == "btcusdt"
        assert payload["result"] in {"opened", "no_action", "rejected", "closed"}
        assert payload["equity"] == 100_000.0
        assert "proposal_id" in payload

    def test_drive_persists_observation_and_context(self, client):
        response = client.post("/v1/drive", json={"symbol": "btcusdt", "price": 100.0})
        assert response.status_code == 200

        events = client.get("/v1/events/recent", params={"symbol": "btcusdt"})
        assert events.status_code == 200
        assert len(events.json()["events"]) >= 1

        contexts = client.get("/v1/context/history", params={"symbol": "btcusdt"})
        assert contexts.status_code == 200
        assert len(contexts.json()["contexts"]) >= 1

    def test_invalid_price_returns_422(self, client):
        response = client.post("/v1/drive", json={"symbol": "btcusdt", "price": -1})
        assert response.status_code == 422

    def test_empty_symbol_returns_422(self, client):
        response = client.post("/v1/drive", json={"symbol": "", "price": 100.0})
        assert response.status_code == 422


class TestDashboardMount:
    def test_dashboard_file_present(self):
        from pathlib import Path

        path = (
            Path(__file__).parent.parent.parent
            / "backend"
            / "presentation"
            / "static"
            / "index.html"
        )
        assert path.exists()
        html = path.read_text(encoding="utf-8")
        assert "ATI" in html
        assert "/v1/drive" in html
