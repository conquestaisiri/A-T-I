# backend/presentation/api/routes_memory.py
"""Episodic memory and reflection API.

Exposes the outcome memory the reasoner recalls (ADR 0010) and an explicit
reflection trigger for operators who want to replay/backfill outcomes into
memory outside the automatic post-close hook. Repositories are injected via
``app.state``; tests replace the state with in-memory SQLite.
"""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Security

from backend.application.interfaces.memory_store import MemoryStore
from backend.application.reflection.reflection_service import ReflectionService
from backend.presentation.api.auth import verify_api_key

router = APIRouter(prefix="/v1", tags=["memory"], dependencies=[Security(verify_api_key)])

DEFAULT_LIMIT = 10
MAX_LIMIT = 100


def _memory(request: Request) -> MemoryStore:
    store = getattr(request.app.state, "memory_store", None)
    if store is None:
        raise HTTPException(status_code=503, detail="Memory store not initialized")
    return cast(MemoryStore, store)


def _reflection(request: Request) -> ReflectionService:
    service = getattr(request.app.state, "reflection", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Reflection not initialized")
    return cast(ReflectionService, service)


def _validate_symbol(symbol: str) -> str:
    if not symbol or not symbol.strip():
        raise HTTPException(status_code=422, detail="'symbol' must be a non-empty string")
    return symbol.strip().lower()


def _bound_limit(limit: int) -> int:
    if limit <= 0:
        raise HTTPException(status_code=422, detail="'limit' must be a positive integer")
    return min(limit, MAX_LIMIT)


@router.get("/memory/count")
def memory_count(
    store: Annotated[MemoryStore, Depends(_memory)],
    symbol: str | None = Query(default=None),
) -> dict[str, Any]:
    """Return the number of episodic memories, optionally per symbol."""
    return {
        "symbol": symbol,
        "count": store.count(symbol.lower() if symbol else None),
    }


@router.get("/memory/recall")
def memory_recall(
    store: Annotated[MemoryStore, Depends(_memory)],
    symbol: str,
    limit: int = Query(default=DEFAULT_LIMIT),
) -> dict[str, Any]:
    """Return recent episodic memories for a symbol, oldest first (bounded)."""
    symbol = _validate_symbol(symbol)
    episodes = store.recall(symbol, _bound_limit(limit))
    return {"symbol": symbol, "episodes": [e.as_dict() for e in episodes]}


@router.post("/reflection/reflect")
def reflect(
    service: Annotated[ReflectionService, Depends(_reflection)],
    symbol: str,
    limit: int = Query(default=50),
) -> dict[str, Any]:
    """Explicitly reflect a symbol's closed trades into episodic memory.

    Idempotent: closed trades re-record their episode (ep-<trade_id>) without
    duplication. This is the operator run for backfilling memory from an
    existing ledger; the automatic post-close hook covers new trades.
    """
    symbol = _validate_symbol(symbol)
    stats = service.reflect(symbol, limit)
    return {
        "symbol": symbol,
        "trades_scanned": stats.trades_scanned,
        "episodes_recorded": stats.episodes_recorded,
        "wins": stats.wins,
        "losses": stats.losses,
        "flats": stats.flats,
    }
