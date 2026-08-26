"""Tests for the operator control center (runtime risk tuning + manual closes)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from backend.application.decision.rule_based_solver import RuleBasedSolver
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import OrderBook, PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features.volume import VolumeFeature
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.presentation.api.routes_operator import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Iterator[tuple[TestClient, PaperFillEngine, CircuitBreakerRiskGate]]:
    database = Database(tmp_path / "operator-api.db")
    fill_engine = PaperFillEngine()
    risk_gate = CircuitBreakerRiskGate()
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=SqliteLedgerRepository(database),
    )
    app = FastAPI()
    app.include_router(router)
    app.state.risk_gate = risk_gate
    app.state.simulator = simulator
    app.state.fill_engine = fill_engine
    app.state.supervisor = SupervisorService()
    app.state.operator_lock = __import__("threading").Lock()
    fill_engine.set_book(OrderBook(best_bid=100.0, best_ask=100.2))

    with TestClient(app) as test_client:
        yield test_client, fill_engine, risk_gate

    database.close()


class TestRiskConfigEndpoints:
    def test_get_risk_config_returns_active_limits(self, client) -> None:
        tc, _, gate = client
        resp = tc.get("/v1/operator/risk-config")
        assert resp.status_code == 200
        body = resp.json()
        assert body["config"]["max_daily_loss_pct"] == pytest.approx(gate.config.max_daily_loss_pct)

    def test_update_risk_config_applies_atomically(self, client) -> None:
        tc, _, _ = client
        resp = tc.post("/v1/operator/risk-config", json={"max_daily_loss_pct": 0.04})
        assert resp.status_code == 200
        assert resp.json()["config"]["max_daily_loss_pct"] == pytest.approx(0.04)

    def test_update_rejects_unknown_fields(self, client) -> None:
        tc, _, _ = client
        resp = tc.post("/v1/operator/risk-config", json={"bogus_field": 0.5})
        assert resp.status_code == 422  # pydantic rejects unknown fields


class TestManualClose:
    def test_close_without_position_404(self, client) -> None:
        tc, _, _ = client
        resp = tc.post("/v1/operator/close/BTCUSDT")
        assert resp.status_code in (404, 409)

    def test_flatten_with_no_positions(self, client) -> None:
        tc, _, _ = client
        resp = tc.post("/v1/operator/flatten")
        assert resp.status_code == 200
        assert resp.json()["status"] == "no_positions"

    def test_close_after_manual_open(self, client) -> None:
        """Drive an entry through the simulator directly, then close it."""
        from backend.domain.decision.proposal import (
            DecisionProposal,
            Hypothesis,
            ProposedAction,
            ProposedActionType,
        )

        tc, fill_engine, sim_gate = client
        simulator: PaperTradingSimulator = tc.app.state.simulator  # type: ignore[attr-defined]
        now = datetime.now(UTC)
        proposal = DecisionProposal(
            proposal_id=f"prop-test-{now.isoformat(timespec='milliseconds')}",
            correlation_id="TESTUSDT",
            created_at=now,
            symbol="TESTUSDT",
            hypothesis=Hypothesis(statement="test", supporting_evidence=(), opposing_evidence=()),
            confidence=0.9,
            uncertainty="none",
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.ENTER_LONG,
                    size_fraction=0.1,
                    order=1,
                    rationale="test entry",
                ),
            ),
            risk_context=simulator.risk_snapshot(mark_price=100.0),
            alternatives=(),
            rationale="test entry",
        )
        result = simulator.process(proposal, 100.0)
        if result.result.value not in ("opened", "no_action"):
            pytest.skip(f"risk gate refused test entry: {result.result.value}")
        if "TESTUSDT" not in simulator.positions:
            pytest.skip("risk gate refused test entry")

        resp = tc.post("/v1/operator/close/TESTUSDT")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "closed"
        assert "realized_pnl" in body

    def test_state_snapshot(self, client) -> None:
        tc, _, _ = client
        resp = tc.get("/v1/operator/state")
        assert resp.status_code == 200
        body = resp.json()
        assert "supervisor" in body and "positions" in body and "equity" in body


class TestSolverThresholdEnforcement:
    """``momentum_entry_pct`` must be enforced, not dead configuration."""

    def test_tiny_roc_is_not_a_signal(self) -> None:
        solver = RuleBasedSolver()
        trend = {"direction": "up", "change_pct": 0.01}
        momentum = {"rate_of_change_pct": 1e-9}
        assert solver._direction(trend, momentum) is None

    def test_material_roc_passes(self) -> None:
        solver = RuleBasedSolver()
        trend = {"direction": "up", "change_pct": 0.01}
        momentum = {"rate_of_change_pct": 0.02}
        assert solver._direction(trend, momentum) == "up"

    def test_volume_ratio_gate_vetoes_thin_print(self) -> None:
        assert RuleBasedSolver._volume_confirms({"volume_ratio": 0.2}) is False
        assert RuleBasedSolver._volume_confirms({"volume_ratio": 0.9}) is True
        assert RuleBasedSolver._volume_confirms(None) is True
        assert RuleBasedSolver._volume_confirms({}) is True


def _snapshot_with_volumes(volumes: list[float]) -> ContextSnapshot:
    events = [
        ObservationEvent(
            source_id="test",
            source_name="test",
            event_type=ObservationEventType.TRADE,
            timestamp=datetime.now(UTC),
            payload={"price": 100.0, "quantity": v},
        )
        for v in volumes
    ]
    return ContextSnapshot(
        start_timestamp=events[0].timestamp,
        end_timestamp=events[-1].timestamp,
        events=tuple(events),
    )


class TestVolumeFeatureRatio:
    def test_volume_feature_exposes_ratio(self) -> None:
        snapshot = _snapshot_with_volumes([10.0, 10.0, 1.0])
        feature = VolumeFeature.compute(snapshot)
        assert feature.value["last_volume"] == pytest.approx(1.0)
        assert feature.value["average_volume"] == pytest.approx(7.0)
        assert feature.value["volume_ratio"] == pytest.approx(1.0 / 7.0, rel=1e-4)


class TestMarketLoopCooldown:
    def test_cooldown_skips_duplicate_decisions(self) -> None:
        import threading as threading_mod
        import time as time_mod
        from unittest.mock import MagicMock

        from backend.application.pipeline.market_loop_service import MarketLoopService

        loop = MarketLoopService(
            bus=MagicMock(),
            ingest_pipeline=MagicMock(),
            decision_pipeline=MagicMock(),
            fill_engine=MagicMock(),
            thread_lock=threading_mod.Lock(),
            min_decision_interval_seconds=30.0,
        )
        context = MagicMock()
        context.snapshot.symbol.lower.return_value = "btcusdt"
        loop.handle(_event())
        first_stats = loop.stats()

        loop._last_decision_monotonic = time_mod.monotonic()
        loop.handle(_event())

        assert first_stats["decisions_driven"] >= 1 or (loop.stats()["decisions_driven"] >= 1)
        assert loop.stats()["decisions_skipped_cooldown"] == 1


def _event() -> ObservationEvent:
    return ObservationEvent(
        source_id="test",
        source_name="test",
        event_type=ObservationEventType.TRADE,
        timestamp=datetime.now(UTC),
        payload={"price": 100.0, "quantity": 1.0},
    )
