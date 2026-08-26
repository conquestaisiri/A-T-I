# backend/domain/context/features/insider.py
"""Insider trading and institutional holdings feature from SEC EDGAR.

Reads cached signals from EdgarService (Form 4 insider transactions + 13F holdings).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot

if TYPE_CHECKING:
    from backend.application.sec_edgar import (
        EdgarService,  # A2 waiver: TYPE_CHECKING-only port type
    )


class InsiderFeature:
    """Insider trading signal (-1 to +1) and institutional holdings signal."""

    name: ClassVar[str] = "insider"

    _service_instance: ClassVar[EdgarService | None] = None

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        """Read insider and institutional signals from the global service cache."""
        params = parameters or {}
        symbol = params.get("symbol", "BTC").upper()

        service = InsiderFeature._service_instance
        if service is None:
            return ContextFeature(
                name=InsiderFeature.name,
                value={
                    "insider_signal": 0.0,
                    "institutional_signal": 0.0,
                    "insider_count": 0,
                    "institutional_count": 0,
                    "cache_status": "unavailable",
                },
                computation_timestamp=snapshot.end_timestamp,
                execution_time=0.0,
            )

        insider = service.get_insider_signal(symbol)
        institutional = service.get_institutional_signal(symbol)

        return ContextFeature(
            name=InsiderFeature.name,
            value={
                "insider_signal": insider["signal"],
                "insider_count": insider["count"],
                "insider_net_shares": insider.get("net_shares", 0.0),
                "institutional_signal": institutional["signal"],
                "institutional_count": institutional["institutions"],
                "institutional_change_pct": institutional.get("change_pct", 0.0),
                "cache_status": "warm"
                if (insider["count"] > 0 or institutional["institutions"] > 0)
                else "cold",
            },
            computation_timestamp=snapshot.end_timestamp,
            execution_time=0.0,
        )


def set_service(service: EdgarService) -> None:
    """Set the global EdgarService instance."""
    InsiderFeature._service_instance = service
