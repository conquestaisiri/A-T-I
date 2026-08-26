# backend/application/interfaces/supervisor.py
"""Port for the platform supervisor (kill switch + data freshness gate).

The supervisor is the safety authority above the risk gate: it decides whether
the platform is currently safe to take new risk at all. The decision pipeline
checks it before producing any proposal; a non-healthy verdict means no trading.

Implementations must be deterministic given the same observations.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class SupervisorStatus(enum.StrEnum):
    """Platform health from the operator's safety standpoint."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    HALTED = "halted"


@dataclass(frozen=True, slots=True)
class SupervisorDecision:
    """Outcome of a supervisor health check.

    Attributes
    ----------
    status: SupervisorStatus
        Whether the platform may keep trading.
    reason: str
        Human-readable explanation (empty when healthy).
    checked_at: datetime
        When the check ran (aware UTC).
    stale_symbols: tuple[str, ...]
        Symbols whose latest observation is older than the configured max age.
    """

    status: SupervisorStatus
    reason: str
    checked_at: datetime
    stale_symbols: tuple[str, ...] = ()

    @property
    def may_trade(self) -> bool:
        """Whether the platform is safe to take new risk right now."""
        return self.status is SupervisorStatus.HEALTHY

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "checked_at": self.checked_at.isoformat(timespec="milliseconds"),
            "stale_symbols": list(self.stale_symbols),
        }


class Supervisor(ABC):
    """Contract for inspecting current platform safety."""

    @abstractmethod
    def record_observation(self, symbol: str, timestamp: datetime) -> None:
        """Note the latest known-good market-data timestamp for ``symbol``."""
        raise NotImplementedError

    @abstractmethod
    def check(self, now: datetime | None = None) -> SupervisorDecision:
        """Return the current platform safety verdict.

        ``now`` may be injected for deterministic replay/testing; when omitted
        the implementation uses its own clock.
        """
        raise NotImplementedError
