# backend/presentation/api/routes_operator.py
"""Operator control center: direct, explicit control surfaces for the paper
portfolio and the risk gate.

Every mutation here is paper-only by construction — they act on the shared
``PaperTradingSimulator`` whose fills are simulated — and every mutation takes
the shared ``operator_lock`` so a live market loop can never interleave on
simulator state. Manual closes reuse the simulator's own EXIT path so the
ledger, fees and attribution stay coherent with AI-driven exits.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel, Field

from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.domain.decision.proposal import (
    DecisionProposal,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
)
from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/operator",
    tags=["operator"],
    dependencies=[Security(verify_api_key)],
)


class RiskConfigUpdate(BaseModel):
    """Runtime risk-limit tuning. Omitted fields keep their current value."""

    max_risk_per_trade_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    max_risk_per_symbol_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    max_portfolio_risk_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    max_daily_loss_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    max_monthly_loss_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    max_drawdown_pct: float | None = Field(default=None, ge=0.0, le=1.0)


def _gate(request: Request) -> CircuitBreakerRiskGate:
    gate = getattr(request.app.state, "risk_gate", None)
    if gate is None:
        raise HTTPException(status_code=503, detail="Risk gate not initialized")
    return cast(CircuitBreakerRiskGate, gate)


def _simulator(request: Request) -> PaperTradingSimulator:
    sim = getattr(request.app.state, "simulator", None)
    if sim is None:
        raise HTTPException(status_code=503, detail="Simulator not initialized")
    return cast(PaperTradingSimulator, sim)


@router.get("/risk-config")
async def get_risk_config(request: Request) -> dict[str, Any]:
    """Current active risk limits."""
    gate = _gate(request)
    return {
        "config": asdict(gate.config),
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.post("/risk-config")
async def update_risk_config(payload: RiskConfigUpdate, request: Request) -> dict[str, Any]:
    """Atomically update risk limits at runtime (validated before applied)."""
    overrides = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not overrides:
        raise HTTPException(status_code=422, detail="No fields to update")
    gate = _gate(request)
    try:
        new_config = gate.update_config(**overrides)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "updated", "config": asdict(new_config)}


@router.get("/state")
async def operator_state(request: Request) -> dict[str, Any]:
    """One-shot control-room snapshot: supervisor + positions + loop + bus flow."""
    supervisor: SupervisorService = request.app.state.supervisor
    decision = supervisor.check()
    sim = _simulator(request)
    loop_stats: dict[str, Any] = {}
    loop = getattr(request.app.state, "market_loop", None)
    if loop is not None:
        loop_stats = loop.stats()
    bus_stats: dict[str, Any] = {}
    bus = getattr(request.app.state, "observation_bus", None)
    if bus is not None and hasattr(bus, "stats"):
        bus_stats = bus.stats()
    return {
        "supervisor": decision.status.value,
        "supervisor_reason": decision.reason,
        "kill_switch_engaged": supervisor.kill_switch_engaged,
        "equity": sim.equity,
        "positions": {
            sym: {
                "side": p.side.value,
                "quantity": p.quantity,
                "entry": p.average_entry_price,
            }
            for sym, p in list(sim.positions.items())
        },
        "market_loop": loop_stats,
        "bus": bus_stats,
    }


@router.post("/close/{symbol}")
async def close_position(symbol: str, request: Request) -> dict[str, Any]:
    """Manually close one paper position at the current mark price."""
    sim = _simulator(request)
    symbol_upper = symbol.strip().upper()
    if symbol_upper not in sim.positions:
        raise HTTPException(status_code=404, detail=f"No open position for {symbol_upper}")
    mark_price = _current_mark(request)
    if mark_price is None:
        raise HTTPException(status_code=409, detail="No mark price available; drive a price first")
    proposal = _exit_proposal(symbol_upper, mark_price, sim)

    with _operator_lock(request):
        step = sim.process(proposal, mark_price)
    record = step.record
    return {
        "status": step.result.value,
        "symbol": symbol_upper,
        "exit_price": mark_price,
        "realized_pnl": getattr(record, "realized_pnl", None) if record else None,
    }


@router.post("/flatten")
async def flatten_all(request: Request) -> dict[str, Any]:
    """Close ALL open paper positions at current marks (emergency button)."""
    sim = _simulator(request)
    symbols = list(sim.positions.keys())
    if not symbols:
        return {"status": "no_positions", "closed": []}

    mark_price = _current_mark(request)
    if mark_price is None:
        raise HTTPException(status_code=409, detail="No mark price available; drive a price first")

    closed: list[dict[str, Any]] = []
    with _operator_lock(request):
        for sym in symbols:
            if sym not in sim.positions:
                continue  # closed as a side effect of an earlier slice exit
            proposal = _exit_proposal(sym.upper(), mark_price, sim)
            step = sim.process(proposal, mark_price)
            closed.append({"symbol": sym, "result": step.result.value})
    return {"status": "flattened", "closed": closed}


class _OperatorLock:
    """Context manager wrapping the optional shared operator lock."""

    def __init__(self, request: Request) -> None:
        self._lock = getattr(request.app.state, "operator_lock", None)

    def __enter__(self) -> None:
        if self._lock is not None and not self._lock.acquire(timeout=5.0):
            raise HTTPException(status_code=503, detail="Operator lock busy; retry")

    def __exit__(self, *exc_info: object) -> None:
        if self._lock is not None:
            self._lock.release()


def _operator_lock(request: Request) -> _OperatorLock:
    return _OperatorLock(request)


def _current_mark(request: Request) -> float | None:
    fill_engine = getattr(request.app.state, "fill_engine", None)
    if fill_engine is None:
        return None
    try:
        mid = float(fill_engine.book.mid)
    except Exception:  # noqa: BLE001 -- no book yet is an expected cold-start state
        return None
    return mid if mid > 0 else None


def _exit_proposal(symbol: str, mark_price: float, sim: PaperTradingSimulator) -> DecisionProposal:
    """Build a minimal EXIT proposal that flows through the simulator's own
    close path (fees, ledger, attribution all stay coherent)."""
    now = datetime.now(UTC)
    rationale = f"Operator manual close at mark {mark_price:.6f}."
    return DecisionProposal(
        proposal_id=f"prop-operator-exit-{symbol}-{now.isoformat(timespec='milliseconds')}",
        correlation_id=f"operator-{symbol}",
        created_at=now,
        symbol=symbol,
        hypothesis=Hypothesis(
            statement="Operator-initiated manual exit.",
            supporting_evidence=(),
            opposing_evidence=(),
        ),
        confidence=1.0,
        uncertainty="Operator override; no model uncertainty.",
        actions=(
            ProposedAction(
                action_type=ProposedActionType.EXIT,
                size_fraction=1.0,
                order=1,
                rationale=rationale,
            ),
        ),
        risk_context=sim.risk_snapshot(mark_price=mark_price, symbol=symbol),
        alternatives=(),
        rationale=rationale,
    )
