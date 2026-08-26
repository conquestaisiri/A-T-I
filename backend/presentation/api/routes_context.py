# backend/presentation/api/routes_context.py
"""Observability API over the persistence layer.

Exposes the durable event log and market-context history that the pipeline
produces. Repositories are injected via ``app.state`` so the router stays a
pure view over the ports; tests replace the state with in-memory SQLite.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security

from backend.application.interfaces.context_repository import ContextRepository
from backend.application.interfaces.observation_repository import ObservationRepository
from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1",
    tags=["observability"],
    dependencies=[Security(verify_api_key)],
)

DEFAULT_LIMIT = 20
MAX_LIMIT = 500


def _observation_repository(request: Request) -> ObservationRepository:
    repo = getattr(request.app.state, "observation_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Observation repository not initialized")
    return cast(ObservationRepository, repo)


def _context_repository(request: Request) -> ContextRepository:
    repo = getattr(request.app.state, "context_repository", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Context repository not initialized")
    return cast(ContextRepository, repo)


def _validate_symbol(symbol: str) -> str:
    if not symbol or not symbol.strip():
        raise HTTPException(status_code=422, detail="'symbol' must be a non-empty string")
    return symbol.strip().lower()


def _bound_limit(limit: int) -> int:
    if limit <= 0:
        raise HTTPException(status_code=422, detail="'limit' must be a positive integer")
    return min(limit, MAX_LIMIT)


@router.get("/context/latest")
def context_latest(
    symbol: str,
    repository: Annotated[ContextRepository, Depends(_context_repository)],
) -> dict[str, Any]:
    """Return the most recently created market context for a symbol."""
    symbol = _validate_symbol(symbol)
    context = repository.latest(symbol)
    if context is None:
        raise HTTPException(status_code=404, detail=f"No context found for symbol '{symbol}'")
    return context.as_dict()


@router.get("/context/history")
def context_history(
    symbol: str,
    repository: Annotated[ContextRepository, Depends(_context_repository)],
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict[str, Any]:
    """Return recent market contexts for a symbol, oldest first."""
    symbol = _validate_symbol(symbol)
    contexts = repository.history(symbol, _bound_limit(limit))
    return {"symbol": symbol, "contexts": [c.as_dict() for c in contexts]}


@router.get("/events/recent")
def events_recent(
    symbol: str,
    repository: Annotated[ObservationRepository, Depends(_observation_repository)],
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict[str, Any]:
    """Return the most recent normalized observation events for a symbol."""
    symbol = _validate_symbol(symbol)
    events = repository.find_recent(symbol, _bound_limit(limit))
    return {"symbol": symbol, "events": [e.model_dump(mode="json") for e in events]}
