"""Tests for the reconciliation API routes (P0-012 follow-up).

The venue is the source of truth: POST /v1/reconcile compares venue positions
against internal simulator state, persists a report, and reports discrepancies
without coercing internal records.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import OrderBook, PaperFillEngine
from backend.application.simulation.sandbox_venue import SandboxVenue
from backend.domain.execution.order import OrderRequest, OrderSide, OrderType
from backend.domain.execution.position import Position
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.reconciliation_repository import (
    SqliteReconciliationRepository,
)
from backend.presentation.api.routes_reconciliation import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestRiskGateFeed:
    """POST /v1/reconcile feeds reconciliation health into the shared gate."""

    def test_mismatch_post_blocks_new_risk_gate_wide(self, client) -> None:
        gate = CircuitBreakerRiskGate()
        client.app.state.risk_gate = gate
        response = client.post(
            "/v1/reconcile",
            json={
                "positions": [
                    {
                        "symbol": "btcusdt",
                        "side": "buy",
                        "quantity": 2.0,  # internal holds 1.5 -> mismatch
                        "average_entry_price": 100.0,
                    }
                ]
            },
        )
        assert response.status_code == 200
        # btcusdt mismatches; ethusdt (internal-only, unreported) is also
        # flagged INTERNAL_ONLY, which is honest venue truth.
        assert "btcusdt" in gate.reconciliation_mismatches()
        assert gate.reconciliation_mismatches()

    def test_consistent_post_clears_the_symbol(self, client) -> None:
        gate = CircuitBreakerRiskGate()
        client.app.state.risk_gate = gate
        client.post(
            "/v1/reconcile",
            json={
                "positions": [
                    {
                        "symbol": "btcusdt",
                        "side": "buy",
                        "quantity": 2.0,
                        "average_entry_price": 100.0,
                    }
                ]
            },
        )
        assert "btcusdt" in gate.reconciliation_mismatches()
        client.post(
            "/v1/reconcile",
            json={
                "positions": [
                    {
                        "symbol": "btcusdt",
                        "side": "buy",
                        "quantity": 1.5,  # matches internal -> consistent
                        "average_entry_price": 100.0,
                    }
                ]
            },
        )
        assert "btcusdt" not in gate.reconciliation_mismatches()

    def test_absent_gate_does_not_break_the_route(self, client) -> None:
        # Route stays functional when no gate is wired (app.state fallback).
        response = client.post(
            "/v1/reconcile",
            json={
                "positions": [
                    {
                        "symbol": "btcusdt",
                        "side": "buy",
                        "quantity": 2.0,
                        "average_entry_price": 100.0,
                    }
                ]
            },
        )
        assert response.status_code == 200

    def test_sandbox_reconcile_feeds_the_gate(self, sandbox_client) -> None:
        gate = CircuitBreakerRiskGate()
        sandbox_client.app.state.risk_gate = gate
        response = sandbox_client.post("/v1/reconcile/sandbox")
        assert response.status_code == 200
        # Sandbox venue self-reports 1.5 BUY; internal also 1.5 -> consistent.
        assert gate.reconciliation_mismatches() == frozenset()


def ts() -> datetime:
    return datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)


def internal_position(
    symbol: str = "btcusdt",
    side: OrderSide = OrderSide.BUY,
    quantity: float = 1.5,
    entry: float = 100.0,
) -> Position:
    return Position(
        symbol=symbol,
        side=side,
        quantity=quantity,
        average_entry_price=entry,
        opened_at=ts(),
    )


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database = Database(tmp_path / "reconcile-api.db")
    repo = SqliteReconciliationRepository(database)
    simulator = SimpleNamespace(
        positions={"btcusdt": internal_position(), "ethusdt": internal_position("ethusdt")}
    )

    app = FastAPI()
    app.include_router(router)
    app.state.reconciliation_store = repo
    app.state.simulator = simulator

    with TestClient(app) as test_client:
        yield test_client

    database.close()


class TestReconcileRoute:
    def test_consistent_positions_report_clean(self, client) -> None:
        response = client.post(
            "/v1/reconcile",
            json={
                "positions": [
                    {
                        "symbol": "btcusdt",
                        "side": "buy",
                        "quantity": 1.5,
                        "average_entry_price": 100.0,
                    }
                ]
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["consistent"] == 1
        assert payload["total"] == 2
        assert "btcusdt" in payload["symbols"]
        assert "btcusdt" not in payload["discrepancies"]

    def test_quantity_mismatch_surfaces_discrepancy(self, client) -> None:
        response = client.post(
            "/v1/reconcile",
            json={
                "positions": [
                    {
                        "symbol": "btcusdt",
                        "side": "buy",
                        "quantity": 2.0,
                        "average_entry_price": 100.0,
                    }
                ]
            },
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["consistent"] == 0
        discrepancy = payload["discrepancies"]["btcusdt"][0]
        assert discrepancy["kind"] == "quantity"
        assert discrepancy["venue_signed"] == 2.0
        assert discrepancy["internal_signed"] == 1.5

    def test_venue_only_position_surfaces_discrepancy(self, client) -> None:
        response = client.post(
            "/v1/reconcile",
            json={
                "positions": [
                    {
                        "symbol": "solusdt",
                        "side": "buy",
                        "quantity": 1.0,
                        "average_entry_price": 50.0,
                    }
                ]
            },
        )
        assert response.status_code == 200
        payload = response.json()
        discrepancy = payload["discrepancies"]["solusdt"][0]
        assert discrepancy["kind"] == "venue_only"
        assert discrepancy["internal_signed"] == 0.0

    def test_reconcile_persists_report(self, client) -> None:
        client.post(
            "/v1/reconcile",
            json={"positions": [{"symbol": "btcusdt", "side": "buy", "quantity": 1.5}]},
        )
        response = client.get("/v1/reconcile/count")
        assert response.status_code == 200
        assert response.json()["count"] == 2

    def test_invalid_side_returns_422(self, client) -> None:
        response = client.post(
            "/v1/reconcile",
            json={"positions": [{"symbol": "btcusdt", "side": "hold", "quantity": 1.0}]},
        )
        assert response.status_code == 422

    def test_non_positive_quantity_returns_422(self, client) -> None:
        response = client.post(
            "/v1/reconcile",
            json={"positions": [{"symbol": "btcusdt", "side": "buy", "quantity": 0}]},
        )
        assert response.status_code == 422


class TestReportsRoute:
    def test_reports_empty_when_none_saved(self, client) -> None:
        response = client.get("/v1/reconcile/reports")
        assert response.status_code == 200
        assert response.json() == {"reports": [], "count": 0}

    def test_reports_recall_saved(self, client) -> None:
        client.post(
            "/v1/reconcile",
            json={"positions": [{"symbol": "btcusdt", "side": "buy", "quantity": 1.5}]},
        )
        response = client.get("/v1/reconcile/reports")
        payload = response.json()
        assert payload["count"] == 2
        by_symbol = {report["symbol"]: report for report in payload["reports"]}
        btcusdt = by_symbol["btcusdt"]
        assert btcusdt["consistent"] is True
        assert btcusdt["reconciled_at"] is not None
        ethusdt = by_symbol["ethusdt"]
        assert ethusdt["consistent"] is False

    def test_reports_filter_by_symbol(self, client) -> None:
        client.post(
            "/v1/reconcile",
            json={"positions": [{"symbol": "btcusdt", "side": "buy", "quantity": 1.5}]},
        )
        response = client.get("/v1/reconcile/reports", params={"symbol": "ethusdt"})
        payload = response.json()
        assert payload["count"] == 1
        assert payload["reports"][0]["symbol"] == "ethusdt"

    def test_zero_limit_returns_422(self, client) -> None:
        response = client.get("/v1/reconcile/reports", params={"limit": 0})
        assert response.status_code == 422

    def test_blank_symbol_returns_422(self, client) -> None:
        response = client.get("/v1/reconcile/reports", params={"symbol": " "})
        assert response.status_code == 422


class TestCountRoute:
    def test_count_initial_zero(self, client) -> None:
        response = client.get("/v1/reconcile/count")
        assert response.json() == {"symbol": None, "count": 0}

    def test_count_is_symbol_scoped(self, client) -> None:
        client.post(
            "/v1/reconcile",
            json={"positions": [{"symbol": "btcusdt", "side": "buy", "quantity": 1.5}]},
        )
        assert client.get("/v1/reconcile/count").json()["count"] == 2
        assert client.get("/v1/reconcile/count", params={"symbol": "btcusdt"}).json()["count"] == 1
        assert client.get("/v1/reconcile/count", params={"symbol": "ethusdt"}).json()["count"] == 1


class TestUninitializedState:
    def test_missing_store_is_503(self, tmp_path) -> None:
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            assert client.get("/v1/reconcile/count").status_code == 503
            assert client.get("/v1/reconcile/reports").status_code == 503

    def test_missing_simulator_is_503(self, tmp_path) -> None:
        database = Database(tmp_path / "no-sim.db")
        app = FastAPI()
        app.include_router(router)
        app.state.reconciliation_store = SqliteReconciliationRepository(database)
        with TestClient(app) as client:
            response = client.post(
                "/v1/reconcile",
                json={"positions": [{"symbol": "btcusdt", "side": "buy", "quantity": 1.0}]},
            )
            assert response.status_code == 503
        database.close()


def _filled_venue(symbol: str = "btcusdt", quantity: float = 1.5) -> SandboxVenue:
    """A sandbox venue that already holds one filled buy position."""
    engine = PaperFillEngine()
    engine.set_book(OrderBook(best_bid=99.0, best_ask=101.0, bid_size=1e9, ask_size=1e9))
    venue = SandboxVenue(engine, resting_ttl_hours=24.0)
    venue.submit(
        OrderRequest(
            order_id=f"{symbol}-entry",
            proposal_id="prop-1",
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=quantity,
            limit_price=None,
            created_at=ts(),
        )
    )
    return venue


@pytest.fixture
def sandbox_client(tmp_path) -> Iterator[TestClient]:
    venue = _filled_venue(quantity=1.5)
    simulator = SimpleNamespace(positions={"btcusdt": internal_position(quantity=1.5)})

    database = Database(tmp_path / "sandbox-reconcile.db")
    app = FastAPI()
    app.include_router(router)
    app.state.reconciliation_store = SqliteReconciliationRepository(database)
    app.state.simulator = simulator
    app.state.sandbox_venue = venue

    with TestClient(app) as test_client:
        yield test_client

    database.close()


class TestSandboxReconcileRoute:
    def test_sandbox_venue_reports_itself_consistently(self, sandbox_client) -> None:
        response = sandbox_client.post("/v1/reconcile/sandbox")
        assert response.status_code == 200
        payload = response.json()
        assert payload["consistent"] == payload["total"] == 1
        assert payload["symbols"] == ["btcusdt"]
        assert payload["discrepancies"] == {}

    def test_sandbox_drift_surfaces_quantity_discrepancy(self, tmp_path) -> None:
        venue = _filled_venue()
        simulator = SimpleNamespace(positions={"btcusdt": internal_position(quantity=0.5)})
        database = Database(tmp_path / "sandbox-drift.db")
        app = FastAPI()
        app.include_router(router)
        app.state.reconciliation_store = SqliteReconciliationRepository(database)
        app.state.simulator = simulator
        app.state.sandbox_venue = venue
        with TestClient(app) as client:
            response = client.post("/v1/reconcile/sandbox")
        assert response.status_code == 200
        payload = response.json()
        assert payload["consistent"] == 0
        discrepancy = payload["discrepancies"]["btcusdt"][0]
        assert discrepancy["kind"] == "quantity"
        assert discrepancy["venue_signed"] == 1.5
        assert discrepancy["internal_signed"] == 0.5
        database.close()

    def test_sandbox_route_requires_venue(self, tmp_path) -> None:
        database = Database(tmp_path / "no-venue.db")
        app = FastAPI()
        app.include_router(router)
        app.state.reconciliation_store = SqliteReconciliationRepository(database)
        app.state.simulator = SimpleNamespace(positions={})
        with TestClient(app) as client:
            assert client.post("/v1/reconcile/sandbox").status_code == 503
        database.close()
