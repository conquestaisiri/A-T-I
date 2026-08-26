# backend/presentation/api/routes_strategy.py
"""Strategy playbook registry — expose testable strategy families."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Security

from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/strategy", tags=["strategy"], dependencies=[Security(verify_api_key)]
)

_PLAYBOOK = [
    {
        "id": "liquidity_sweep",
        "name": "Buy/Sell Side Liquidity Sweep",
        "module": "backend.domain.strategy.playbook.liquidity_sweep",
    },
    {
        "id": "daily_open_close",
        "name": "Daily Open Momentum",
        "module": "backend.domain.strategy.playbook.daily_open_close",
    },
    {
        "id": "engulfing",
        "name": "Engulfing & Pin-Bar",
        "module": "backend.domain.strategy.playbook.engulfing",
    },
]


@router.get("/playbook")
async def playbook() -> dict[str, Any]:
    return {"count": len(_PLAYBOOK), "strategies": _PLAYBOOK}
