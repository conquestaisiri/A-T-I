"""Tests for the decision pipeline API routes."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.decision.trade_plan import PreTradePlan, StopLevel
from backend.domain.execution.order import OrderSide
from backend.domain.execution.trade_record import TradeRecord, TradeStatus
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository
from backend.presentation.api.routes_decision import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_proposal(proposal_id: str = "prop-1", symbol: str = "btcusdt") -> DecisionProposal:
    return DecisionProposal(
        proposal_id=proposal_id,
        correlation_id="corr-1",
        created_at=ts(),
        symbol=symbol,
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
        pre_trade_plan=PreTradePlan(
            stop_loss=StopLevel(distance_pct=0.05),
            take_profit=StopLevel(distance_pct=0.10),
            risk_per_trade_pct=0.02,
            risk_reward_ratio=2.0,
        ),
    )


def make_trade(trade_id: str = "trade-1", symbol: str = "btcusdt") -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        proposal_id="prop-1",
        correlation_id="corr-1",
        symbol=symbol,
        side=OrderSide.BUY,
        quantity=10.0,
        entry_price=100.0,
        opened_at=ts(),
        exit_price=None,
        closed_at=None,
        realized_pnl=None,
        status=TradeStatus.OPEN,
    )


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database = Database(tmp_path / "decision-api.db")
    proposal_repo = SqliteProposalRepository(database)
    ledger_repo = SqliteLedgerRepository(database)
    engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=CircuitBreakerRiskGate(),
        order_gateway=engine,
        ledger=ledger_repo,
    )

    app = FastAPI()
    app.include_router(router)
    app.state.proposal_repository = proposal_repo
    app.state.ledger_repository = ledger_repo
    app.state.simulator = simulator
    app.state.simulator_order_gateway = engine

    with TestClient(app) as test_client:
        yield test_client

    database.close()


class TestProposalAPI:
    def test_proposals_recent_empty(self, client):
        response = client.get("/v1/proposals/recent?symbol=btcusdt")
        assert response.status_code == 200
        assert response.json()["proposals"] == []

    def test_proposals_recent_returns_saved(self, client):
        client.app.state.proposal_repository.save(make_proposal())
        response = client.get("/v1/proposals/recent?symbol=btcusdt")
        assert response.status_code == 200
        payload = response.json()
        assert len(payload["proposals"]) == 1
        assert payload["proposals"][0]["proposal_id"] == "prop-1"

    def test_proposal_by_id_found(self, client):
        client.app.state.proposal_repository.save(make_proposal())
        response = client.get("/v1/proposals/prop-1")
        assert response.status_code == 200
        assert response.json()["symbol"] == "btcusdt"

    def test_proposal_by_id_missing(self, client):
        response = client.get("/v1/proposals/missing")
        assert response.status_code == 404

    def test_proposals_recent_rejects_empty_symbol(self, client):
        response = client.get("/v1/proposals/recent?symbol=")
        assert response.status_code == 422


class TestLedgerAPI:
    def test_ledger_recent_empty(self, client):
        response = client.get("/v1/ledger/recent?symbol=btcusdt")
        assert response.status_code == 200
        assert response.json()["trades"] == []

    def test_ledger_recent_returns_saved(self, client):
        client.app.state.ledger_repository.save(make_trade())
        response = client.get("/v1/ledger/recent?symbol=btcusdt")
        assert response.status_code == 200
        assert len(response.json()["trades"]) == 1

    def test_ledger_open_returns_open_only(self, client):
        repo = client.app.state.ledger_repository
        repo.save(make_trade(trade_id="trade-1"))
        repo.save(
            replace(
                make_trade(trade_id="trade-2"),
                exit_price=110.0,
                closed_at=ts(),
                realized_pnl=100.0,
                status=TradeStatus.CLOSED,
            )
        )
        response = client.get("/v1/ledger/open")
        assert response.status_code == 200
        assert [t["trade_id"] for t in response.json()["trades"]] == ["trade-1"]

    def test_ledger_by_id_found(self, client):
        client.app.state.ledger_repository.save(make_trade())
        response = client.get("/v1/ledger/trade-1")
        assert response.status_code == 200
        assert response.json()["trade_id"] == "trade-1"

    def test_ledger_by_id_missing(self, client):
        response = client.get("/v1/ledger/missing")
        assert response.status_code == 404

    def test_ledger_attribution_empty(self, client):
        response = client.get("/v1/ledger/attribution")
        assert response.status_code == 200
        payload = response.json()
        assert payload["aggregate"]["trade_count"] == 0
        assert payload["trades"] == []

    def test_ledger_attribution_returns_decomposition(self, client):
        repo = client.app.state.ledger_repository
        repo.save(
            replace(
                make_trade(trade_id="trade-1"),
                exit_price=110.0,
                closed_at=ts(),
                realized_pnl=100.0,
                gross_pnl=102.0,
                fee=2.0,
                status=TradeStatus.CLOSED,
                entry_arrival_price=99.0,
                exit_arrival_price=111.0,
            )
        )
        response = client.get("/v1/ledger/attribution?symbol=btcusdt")
        assert response.status_code == 200
        payload = response.json()
        assert payload["aggregate"]["trade_count"] == 1
        assert payload["aggregate"]["net_pnl"] == pytest.approx(100.0)
        [trade] = payload["trades"]
        assert trade["gross_pnl"] == pytest.approx(102.0)
        # Identity: gross = alpha - entry_slippage - exit_slippage
        assert trade["alpha_pnl"] - trade["entry_slippage"] - trade[
            "exit_slippage"
        ] == pytest.approx(trade["gross_pnl"])


class TestSimulatorAPI:
    def test_simulator_status_initial(self, client):
        response = client.get("/v1/simulator")
        assert response.status_code == 200
        payload = response.json()
        assert payload["equity"] == 100_000.0
        assert payload["positions"] == {}
        assert payload["risk"]["position_count"] == 0

    def test_simulator_status_reflects_open_position(self, client):
        simulator = client.app.state.simulator
        engine = client.app.state.simulator_order_gateway
        engine.set_mark_price(100.0)
        simulator.process(make_proposal(proposal_id="prop-open"), mark_price=100.0)

        response = client.get("/v1/simulator")
        assert response.status_code == 200
        payload = response.json()
        assert payload["risk"]["position_count"] == 1
        assert "btcusdt" in payload["positions"]
