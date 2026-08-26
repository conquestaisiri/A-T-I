# backend/presentation/api/routes_engine.py
"""Trading engine lifecycle: START/STOP + AUTO-TRADE toggle.

The self-feeding MarketLoopService and MT5 adapter are the "engine".
They are PAUSED by default (recommended: you press START). This router
is the dashboard's START button and the AI concierge's actuation surface —
both go through the same guarded, persisted state, so a restart resumes
what you asked for, and paper-mode + supervisor still gate every fill.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Security

from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/engine",
    tags=["engine"],
    dependencies=[Security(verify_api_key)],
)

_STATE_PATH = Path("data/engine_state.json")


def _load_state() -> dict[str, Any]:
    if _STATE_PATH.exists():
        try:
            raw = json.loads(_STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return dict(raw)
            return {}
        except Exception:
            return {}
    return {}


def _save_state(state: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _engine_snapshot(request: Request) -> dict[str, Any]:
    loop = getattr(request.app.state, "market_loop", None)
    adapter = getattr(request.app.state, "mt5_adapter", None)
    pipeline = getattr(request.app.state, "decision_pipeline", None)
    supervisor = getattr(request.app.state, "supervisor", None)
    from backend.infrastructure.config.settings import settings

    persisted = _load_state()
    auto_trade = (
        bool(getattr(pipeline, "_auto_trade", True))
        if pipeline
        else bool(persisted.get("auto_trade", False))
    )
    running = False
    stats: dict[str, Any] = {}
    if loop is not None:
        try:
            stats = loop.stats()
            running = bool(getattr(loop, "_running", False))
        except Exception:
            stats = {}
    # MT5 adapter counts as engine activity in forex mode
    if not running and adapter is not None:
        try:
            running = getattr(adapter, "_task", None) is not None and not adapter._task.done()
        except Exception:
            running = False

    sup = None
    sup_reason = None
    if supervisor is not None:
        try:
            d = supervisor.check()
            sup = d.status.value if hasattr(d.status, "value") else str(d.status)
            sup_reason = d.reason
        except Exception:
            pass

    return {
        "mode": settings.trading_mode.strip().lower(),
        "paper_mode": bool(settings.paper_mode),
        "auto_trade": auto_trade,
        "running": running,
        "supervisor": sup,
        "supervisor_reason": sup_reason,
        "stats": stats,
        "persisted": persisted,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@router.get("/status")
async def engine_status(request: Request) -> dict[str, Any]:
    return _engine_snapshot(request)


@router.post("/start")
async def engine_start(request: Request) -> dict[str, Any]:
    from backend.infrastructure.config.settings import settings

    if settings.paper_mode and not getattr(settings, "live_trading_authorized", False):
        # Paper mode is fine — this is the default. We just log it.
        pass
    # Flip persisted intent so a restart resumes.
    state = _load_state()
    state["desired_running"] = True
    state["auto_trade"] = state.get("auto_trade", True)
    _save_state(state)

    # Try to start in-process loops if they exist and are stopped.
    started: list[str] = []
    loop = getattr(request.app.state, "market_loop", None)
    if loop is not None and not getattr(loop, "_running", False):
        import asyncio

        try:
            # MarketLoopService.start() blocks; run it in a task.
            request.app.state.market_tasks = getattr(request.app.state, "market_tasks", [])
            task = asyncio.create_task(loop.start())
            request.app.state.market_tasks.append(task)
            # Also flip the internal flag immediately so status reflects running
            setattr(loop, "_running", True)  # noqa: B010
            started.append("market_loop")
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"Failed to start market loop: {exc}"
            ) from exc

    adapter = getattr(request.app.state, "mt5_adapter", None)
    if adapter is not None:
        _t: Any = getattr(adapter, "_task", None)
        if _t is None or _t.done():
            try:
                adapter.start()
                started.append("mt5_adapter")
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(
                    status_code=500, detail=f"Failed to start MT5 adapter: {exc}"
                ) from exc

    snap = _engine_snapshot(request)
    snap["started"] = started
    return snap


@router.post("/stop")
async def engine_stop(request: Request) -> dict[str, Any]:
    state = _load_state()
    state["desired_running"] = False
    _save_state(state)

    stopped: list[str] = []
    loop = getattr(request.app.state, "market_loop", None)
    if loop is not None and getattr(loop, "_running", False):
        try:
            loop.stop()
            stopped.append("market_loop")
        except Exception:
            pass
    adapter = getattr(request.app.state, "mt5_adapter", None)
    if adapter is not None:
        try:
            await adapter.stop()
            stopped.append("mt5_adapter")
        except Exception:
            pass

    snap = _engine_snapshot(request)
    snap["stopped"] = stopped
    return snap


@router.post("/mode")
async def engine_mode(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    if "auto_trade" not in payload:
        raise HTTPException(status_code=422, detail="Missing 'auto_trade' boolean")
    auto_trade = bool(payload["auto_trade"])
    pipeline = getattr(request.app.state, "decision_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "_auto_trade"):
        setattr(pipeline, "_auto_trade", auto_trade)  # noqa: B010

    state = _load_state()
    state["auto_trade"] = auto_trade
    _save_state(state)

    snap = _engine_snapshot(request)
    snap["mode_switched_to"] = "auto_trade" if auto_trade else "manual"
    return snap
