# backend/presentation/api/routes_drive.py
"""Live paper-trading drive API.

Accepts an observation and runs it through the **real** decision path:
ContextBuilder -> DecisionPipelineService (reason -> risk -> simulator),
with post-close reflection into episodic memory. This is the operational
surface that makes the loop observable to the operator (a paper action, never
a live path; the simulated ledger is the only side effect).
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Security
from pydantic import BaseModel, Field

from backend.application.pipeline.context_pipeline_service import ContextPipelineService
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.presentation.api.auth import verify_api_key

router = APIRouter(prefix="/v1/drive", tags=["drive"], dependencies=[Security(verify_api_key)])


class DriveRequest(BaseModel):
    """An observation to drive through the decision loop.

    Attributes
    ----------
    symbol: str
        Market symbol, lowercased.
    price: float
        Venue price at which the proposal is executed.
    trade_id: str | None
        Optional native trade id for deduplication.
    timestamp: datetime | None
        Optional observation time (defaults to now).
    """

    symbol: str = Field(..., min_length=1, max_length=20, pattern=r"^[A-Za-z0-9/_\-.:]+$")
    price: float = Field(..., gt=0, le=1e7)
    trade_id: str | None = Field(default=None, max_length=64)
    timestamp: datetime | None = None


def _ingest_pipeline(request: Request) -> ContextPipelineService:
    pipeline = getattr(request.app.state, "ingest_pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Ingest pipeline not initialized")
    return cast(ContextPipelineService, pipeline)


def _pipeline(request: Request) -> DecisionPipelineService:
    pipeline = getattr(request.app.state, "decision_pipeline", None)
    if pipeline is None:
        raise HTTPException(status_code=503, detail="Decision pipeline not initialized")
    return cast(DecisionPipelineService, pipeline)


def _fill_engine(request: Request) -> PaperFillEngine:
    engine = getattr(request.app.state, "fill_engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="Fill engine not initialized")
    return cast(PaperFillEngine, engine)


def _now() -> datetime:
    return datetime.now(UTC)


def _operator_lock(request: Request) -> threading.Lock | None:
    return getattr(request.app.state, "operator_lock", None)


@router.post("")
def drive(
    request_body: DriveRequest,
    request: Request,
    ingest: Annotated[ContextPipelineService, Depends(_ingest_pipeline)],
    pipeline: Annotated[DecisionPipelineService, Depends(_pipeline)],
    engine: Annotated[PaperFillEngine, Depends(_fill_engine)],
) -> dict[str, Any]:
    """Drive one observation through the decision path in paper mode."""
    symbol = request_body.symbol.strip().lower()
    event = ObservationEvent(
        source_id="operator",
        source_name="Operator Drive",
        event_type=ObservationEventType.TRADE,
        timestamp=request_body.timestamp or _now(),
        payload={
            "symbol": symbol,
            "trade_id": request_body.trade_id or 0,
            "price": request_body.price,
            "quantity": 1.0,
        },
    )
    context = ingest.handle(event)
    lock = _operator_lock(request)
    if lock is not None:
        with lock:
            engine.set_mark_price(request_body.price)
            step = pipeline.process(context, request_body.price)
    else:
        engine.set_mark_price(request_body.price)
        step = pipeline.process(context, request_body.price)

    risk = pipeline.risk_snapshot()
    return {
        "symbol": symbol,
        "proposal_id": step.proposal_id,
        "result": step.result.value,
        "risk_verdict": step.risk_verdict,
        "closed_trade": step.record.as_dict() if step.record is not None else None,
        "equity": risk.account_equity,
        "position_count": risk.position_count,
        "open_exposure_pct": risk.open_exposure_pct,
    }
