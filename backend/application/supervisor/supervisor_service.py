# backend/application/supervisor/supervisor_service.py
"""Platform supervisor: kill switch + data-freshness gating (blueprint Tier 1).

The supervisor is a second safety authority above the risk gate. The risk gate
protects capital; the supervisor protects the platform itself — it stops trading
when an operator pulls the kill switch or when the data feed goes stale, so the
AI never acts on outdated or unverified information. Like the gate, the
supervisor is deterministic: the same observations yield the same verdict.

Every decision pipeline starts with a supervisor check. A non-``HEALTHY``
verdict means the pipeline produces no proposal at all — refusing is always
safe.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.application.interfaces.supervisor import (
    Supervisor,
    SupervisorDecision,
    SupervisorStatus,
)


class SupervisorService(Supervisor):
    """Kill-switch + staleness supervisor backed by in-memory state.

    Parameters
    ----------
    max_data_age_seconds: float
        Maximum allowed age of the latest observation for a symbol before the
        platform is DEGRADED (stale data gate). Defaults to the context window
        (5 minutes).
    """

    def __init__(self, max_data_age_seconds: float = 300.0) -> None:
        if max_data_age_seconds <= 0:
            raise ValueError("max_data_age_seconds must be positive")
        self._max_data_age_seconds = max_data_age_seconds
        self._kill_reason: str | None = None
        self._kill_since: datetime | None = None
        self._last_observation: dict[str, datetime] = {}

    # -- operator controls -----------------------------------------------------

    def engage_kill_switch(self, reason: str, *, now: datetime | None = None) -> None:
        """Halt all further trading until the switch is released."""
        if not reason or not reason.strip():
            raise ValueError("kill switch requires a non-empty reason")
        self._kill_reason = reason
        self._kill_since = now or datetime.now(UTC)

    def release_kill_switch(self) -> None:
        """Allow trading again (operator confirmation only)."""
        self._kill_reason = None
        self._kill_since = None

    @property
    def kill_switch_engaged(self) -> bool:
        return self._kill_reason is not None

    # -- data freshness ---------------------------------------------------------

    def record_observation(self, symbol: str, timestamp: datetime) -> None:
        """Note the latest known-good observation timestamp for ``symbol``."""
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        self._last_observation[symbol] = timestamp

    # -- health check -----------------------------------------------------------

    def check(self, now: datetime | None = None) -> SupervisorDecision:
        """Return the current platform verdict.

        Precedence: a halted (kill-switch) platform is reported as HALTED even
        if its data is also stale; stale data alone degrades, never halts
        without operator action.
        """
        checked_at = now or datetime.now(UTC)
        if self._kill_reason is not None:
            return SupervisorDecision(
                status=SupervisorStatus.HALTED,
                reason=self._kill_reason,
                checked_at=checked_at,
            )

        stale = [
            symbol
            for symbol, ts in self._last_observation.items()
            if (checked_at - ts).total_seconds() > self._max_data_age_seconds
        ]
        if stale:
            return SupervisorDecision(
                status=SupervisorStatus.DEGRADED,
                reason=f"Stale market data for: {', '.join(sorted(stale))}.",
                checked_at=checked_at,
                stale_symbols=tuple(sorted(stale)),
            )

        return SupervisorDecision(
            status=SupervisorStatus.HEALTHY,
            reason="All systems nominal.",
            checked_at=checked_at,
        )
