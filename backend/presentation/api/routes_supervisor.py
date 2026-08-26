# backend/presentation/api/routes_supervisor.py
"""Platform supervisor API (kill switch + data freshness observability).

The router is a thin view over ``app.state.supervisor``. If no supervisor is
wired on the application state, every endpoint returns 503 — the supervisor is
optional (backtests run without one) so this router never fabricates state.
"""

from __future__ import annotations

from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel, Field

from backend.application.interfaces.supervisor import SupervisorDecision
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/supervisor",
    tags=["supervisor"],
    dependencies=[Security(verify_api_key)],
)


class KillSwitchRequest(BaseModel):
    """Operator-supplied reason for engaging the kill switch."""

    reason: str = Field(
        min_length=1, max_length=500, description="Why the platform is being halted"
    )

    model_config = {"str_strip_whitespace": True}


def _supervisor(request: Request) -> SupervisorService:
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor is None:
        raise HTTPException(status_code=503, detail="Supervisor not initialized")
    return cast(SupervisorService, supervisor)


@router.get("/status")
def supervisor_status(request: Request) -> dict[str, Any]:
    """Return the current platform verdict (healthy / degraded / halted)."""
    supervisor = _supervisor(request)
    decision: SupervisorDecision = supervisor.check()
    return {
        **decision.as_dict(),
        "kill_switch_engaged": supervisor.kill_switch_engaged,
    }


@router.post("/kill")
def engage_kill_switch(request: Request, payload: KillSwitchRequest) -> dict[str, Any]:
    """Pull the platform kill switch: halt all further trading."""
    supervisor = _supervisor(request)
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(status_code=422, detail="'reason' must be a non-empty string")
    supervisor.engage_kill_switch(payload.reason)
    decision = supervisor.check()
    return decision.as_dict()


@router.post("/release")
def release_kill_switch(request: Request) -> dict[str, Any]:
    """Release the platform kill switch (operator confirmation only)."""
    supervisor = _supervisor(request)
    supervisor.release_kill_switch()
    decision = supervisor.check()
    return decision.as_dict()
