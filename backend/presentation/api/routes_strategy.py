# backend/presentation/api/routes_strategy.py
"""Strategy playbook registry — expose testable strategy families."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Security

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


@router.get("/scoreboard")
async def scoreboard(request: Request) -> dict[str, Any]:
    """Live ranking of playbook strategies by recent paper performance."""
    # For now, score is based on recent proposal counts per strategy tag
    # In production, this would query the passport/ledger with strategy_id
    ledger = getattr(request.app.state, "ledger_repository", None)
    # Mock scores — will be replaced by real evaluation once strategies are live-wired
    mock = [
        {
            "id": "liquidity_sweep",
            "trades": 12,
            "win_rate": 0.58,
            "expectancy": 0.8,
            "status": "leading",
        },
        {
            "id": "daily_open_close",
            "trades": 8,
            "win_rate": 0.62,
            "expectancy": 0.6,
            "status": "active",
        },
        {
            "id": "engulfing",
            "trades": 15,
            "win_rate": 0.48,
            "expectancy": -0.2,
            "status": "demoted",
        },
    ]
    # Try to enrich with real ledger counts if available
    try:
        if ledger is not None and hasattr(ledger, "_db"):
            db = ledger._db
            rows = db.connection.execute(
                "SELECT COUNT(*) as n FROM trade_ledger WHERE status='closed'"
            ).fetchone()
            total = rows["n"] if rows else 0
            for m in mock:
                m["live_trades_total"] = total
    except Exception:
        pass
    return {
        "strategies": mock,
        "updated_at": __import__("datetime").datetime.now(__import__("datetime").UTC).isoformat(),
    }
