# backend/presentation/api/routes_decision.py
"""Decision pipeline observability API.

Exposes the durable proposal store, the trade outcome ledger, and the live
paper-simulator portfolio. Repositories and the simulator are injected via
``app.state`` so the router stays a pure view over the ports; tests replace
the state with in-memory SQLite.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security

from backend.application.execution.execution_attribution import ExecutionAttributionService
from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.interfaces.proposal_repository import ProposalRepository
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.presentation.api.auth import verify_api_key

router = APIRouter(prefix="/v1", tags=["decision"], dependencies=[Security(verify_api_key)])

DEFAULT_LIMIT = 20
MAX_LIMIT = 500


def _proposal_repository(request: Request) -> ProposalRepository:
    repo = getattr(request.app.state, "proposal_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Proposal repository not initialized")
    return cast(ProposalRepository, repo)


def _ledger_repository(request: Request) -> LedgerRepository:
    repo = getattr(request.app.state, "ledger_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Ledger repository not initialized")
    return cast(LedgerRepository, repo)


def _simulator(request: Request) -> PaperTradingSimulator:
    simulator = getattr(request.app.state, "simulator", None)
    if simulator is None:
        raise HTTPException(status_code=503, detail="Simulator not initialized")
    return cast(PaperTradingSimulator, simulator)


def _validate_symbol(symbol: str) -> str:
    if not symbol or not symbol.strip():
        raise HTTPException(status_code=422, detail="'symbol' must be a non-empty string")
    return symbol.strip().lower()


def _bound_limit(limit: int) -> int:
    if limit <= 0:
        raise HTTPException(status_code=422, detail="'limit' must be a positive integer")
    return min(limit, MAX_LIMIT)


@router.get("/proposals/recent")
def proposals_recent(
    symbol: str,
    repository: Annotated[ProposalRepository, Depends(_proposal_repository)],
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict[str, Any]:
    """Return the most recent decision proposals for a symbol, oldest first."""
    symbol = _validate_symbol(symbol)
    proposals = repository.find_recent(symbol, _bound_limit(limit))
    return {"symbol": symbol, "proposals": [p.as_dict() for p in proposals]}


@router.get("/proposals/{proposal_id}")
def proposal_by_id(
    proposal_id: str,
    repository: Annotated[ProposalRepository, Depends(_proposal_repository)],
) -> dict[str, Any]:
    """Return a single decision proposal by id."""
    proposal = repository.find_by_id(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail=f"No proposal found for '{proposal_id}'")
    return proposal.as_dict()


@router.get("/ledger/recent")
def ledger_recent(
    symbol: str,
    repository: Annotated[LedgerRepository, Depends(_ledger_repository)],
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict[str, Any]:
    """Return the most recent trade records for a symbol, oldest first."""
    symbol = _validate_symbol(symbol)
    records = repository.find_recent(symbol, _bound_limit(limit))
    return {"symbol": symbol, "trades": [t.as_dict() for t in records]}


@router.get("/ledger/open")
def ledger_open(
    repository: Annotated[LedgerRepository, Depends(_ledger_repository)],
) -> dict[str, Any]:
    """Return all currently open trade records."""
    records = repository.open_trades()
    return {"trades": [t.as_dict() for t in records]}


@router.get("/ledger/attribution")
def ledger_attribution(
    repository: Annotated[LedgerRepository, Depends(_ledger_repository)],
    symbol: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict[str, Any]:
    """Return per-trade PnL attribution plus the aggregate cost drag."""
    limit = _bound_limit(limit)
    if symbol is not None:
        symbol = _validate_symbol(symbol)
    service = ExecutionAttributionService(repository)
    report, attributions = service.recent(symbol=symbol, limit=limit)
    return {
        "symbol": symbol,
        "aggregate": report.as_dict(),
        "trades": [a.as_dict() for a in attributions],
    }


@router.get("/ledger/{trade_id}")
def ledger_by_id(
    trade_id: str,
    repository: Annotated[LedgerRepository, Depends(_ledger_repository)],
) -> dict[str, Any]:
    """Return a single trade record by id."""
    record = repository.find_by_id(trade_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"No trade found for '{trade_id}'")
    return record.as_dict()


@router.get("/simulator")
def simulator_status(
    simulator: Annotated[PaperTradingSimulator, Depends(_simulator)],
) -> dict[str, Any]:
    """Return the live paper-simulator portfolio state."""
    risk = simulator.risk_snapshot()
    return {
        "equity": simulator.equity,
        "positions": {
            sym: {
                "symbol": position.symbol,
                "side": position.side.value,
                "quantity": position.quantity,
                "average_entry_price": position.average_entry_price,
                "opened_at": position.opened_at.isoformat(timespec="milliseconds"),
            }
            for sym, position in simulator.positions.items()
        },
        "risk": risk.as_dict(),
    }
