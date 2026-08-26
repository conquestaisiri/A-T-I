# backend/presentation/api/routes_reconciliation.py
"""Venue reconciliation API (P0-012 follow-up).

Exposes the reconciliation service to the operator: compare venue-reported
positions against internal state, persist every report, and recall report
history. The venue is always the source of truth; this surface only *reports*
discrepancies — it never coerces internal records to match.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    Security,
)
from pydantic import BaseModel, Field

from backend.application.execution.reconciliation_service import ReconciliationService
from backend.application.interfaces.reconciliation_store import ReconciliationStore
from backend.domain.execution.order import OrderSide
from backend.domain.execution.position import Position
from backend.domain.execution.reconciliation import ReconciliationReport, VenuePosition
from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/reconcile",
    tags=["reconciliation"],
    dependencies=[Security(verify_api_key)],
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


class VenuePositionRequest(BaseModel):
    """A venue-reported position (the unqueryable half of reconciliation)."""

    symbol: str = Field(..., min_length=1, max_length=30, pattern=r"^[A-Za-z0-9/_\-.:]+$")
    side: str = Field(..., pattern="^(buy|sell)$")
    quantity: float = Field(..., gt=0, le=1e9)
    average_entry_price: float | None = Field(default=None, gt=0, le=1e7)


class ReconcileRequest(BaseModel):
    """Venue positions to reconcile against internal state."""

    positions: list[VenuePositionRequest] = Field(default_factory=list, max_length=1000)


def _store(request: Request) -> ReconciliationStore:
    store = getattr(request.app.state, "reconciliation_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Reconciliation store not initialized")
    return cast(ReconciliationStore, store)


def _internal_positions(request: Request) -> dict[str, Position]:
    simulator = getattr(request.app.state, "simulator", None)
    if simulator is None:
        raise HTTPException(status_code=503, detail="Paper simulator not initialized")
    return cast(dict[str, Position], simulator.positions)


def _now() -> datetime:
    return datetime.now(UTC)


def _venue_positions(payload: ReconcileRequest) -> list[VenuePosition]:
    return [
        VenuePosition(
            symbol=p.symbol.strip().lower(),
            side=OrderSide(p.side),
            quantity=p.quantity,
            average_entry_price=p.average_entry_price,
            reported_at=_now(),
        )
        for p in payload.positions
    ]


def _reconcile(
    venue_positions: list[VenuePosition],
    request: Request,
    store: ReconciliationStore,
) -> dict[str, ReconciliationReport]:
    """Run the comparison, persist every report, and return them by symbol."""
    internal = list(_internal_positions(request).values())
    reports = ReconciliationService.reconcile(venue_positions, internal, reconciled_at=_now())
    for report in reports.values():
        store.save_report(report)
    _feed_risk_gate(request, reports)
    return reports


def _feed_risk_gate(request: Request, reports: dict[str, ReconciliationReport]) -> None:
    """Push reconciliation health into the shared risk gate (P0-012).

    A symbol reported inconsistent blocks new risk gate-wide until a later
    reconciliation reports it consistent again (spec §9.5). The gate is the
    same instance the simulator evaluates with (gap G3 wiring), so the veto is
    effective end-to-end. Absent a gate, reports are still persisted and the
    route stays functional.
    """
    risk_gate = getattr(request.app.state, "risk_gate", None)
    if risk_gate is None:
        return
    for symbol, report in reports.items():
        risk_gate.set_reconciliation_state(symbol, report.consistent)


def _reconcile_response(reports: dict[str, ReconciliationReport]) -> dict[str, Any]:
    """Shape reconciliation reports into the API response body."""
    return {
        "reconciled_at": _now().isoformat(timespec="milliseconds"),
        "symbols": sorted(reports),
        "discrepancies": {
            symbol: [d.as_dict() for d in report.discrepancies]
            for symbol, report in reports.items()
            if not report.consistent
        },
        "consistent": sum(1 for r in reports.values() if r.consistent),
        "total": len(reports),
    }


@router.post("")
def reconcile(
    request_body: ReconcileRequest,
    request: Request,
    store: Annotated[ReconciliationStore, Depends(_store)],
) -> dict[str, Any]:
    """Reconcile venue positions against internal simulator state and persist."""
    venue_positions = _venue_positions(request_body)
    reports = _reconcile(venue_positions, request, store)
    return _reconcile_response(reports)


@router.post("/sandbox")
def reconcile_sandbox(
    request: Request,
    store: Annotated[ReconciliationStore, Depends(_store)],
) -> dict[str, Any]:
    """Reconcile the sandbox venue's self-reported positions against internal state.

    The sandbox venue is a :class:`VenueStateSource`: it reports its own
    positions as venue truth (derived from the fills it acknowledges), exactly
    as a live adapter would. Any disagreement with internal simulator records
    is surfaced — never coerced (P0-012 / ADR 0008).
    """
    venue = getattr(request.app.state, "sandbox_venue", None)
    if venue is None:
        raise HTTPException(status_code=503, detail="Sandbox venue not initialized")
    reports = _reconcile(venue.fetch_open_positions(), request, store)
    return _reconcile_response(reports)


@router.get("/reports")
def reports(
    store: Annotated[ReconciliationStore, Depends(_store)],
    symbol: str | None = Query(default=None),
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict[str, Any]:
    """Recall persisted reconciliation reports, newest first."""
    if symbol is not None and not symbol.strip():
        raise HTTPException(status_code=422, detail="'symbol' must be non-empty when given")
    if limit <= 0:
        raise HTTPException(status_code=422, detail="'limit' must be a positive integer")
    reports = store.recent_reports(
        symbol=symbol.strip().lower() if symbol else None,
        limit=min(limit, MAX_LIMIT),
    )
    return {
        "reports": [r.as_dict() for r in reports],
        "count": len(reports),
    }


@router.get("/count")
def count(
    store: Annotated[ReconciliationStore, Depends(_store)],
    symbol: str | None = Query(default=None),
) -> dict[str, Any]:
    """Number of stored reconciliation reports, optionally per symbol."""
    return {
        "symbol": symbol,
        "count": store.count(symbol.strip().lower() if symbol else None),
    }
