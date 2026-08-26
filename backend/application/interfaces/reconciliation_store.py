# backend/application/interfaces/reconciliation_store.py
"""Port for persisting reconciliation reports (P0-012 follow-up).

Reconciliation runs compare venue truth against internal state; their reports
must survive process restarts so an operator can review a history of
discrepancies even after the incident that produced them. This port is the
boundary the SQLite implementation satisfies.
"""

from __future__ import annotations

from typing import Protocol

from backend.domain.execution.reconciliation import ReconciliationReport


class ReconciliationStore(Protocol):
    """Persists and recalls reconciliation reports."""

    def save_report(self, report: ReconciliationReport) -> None:
        """Write one reconciliation report (upsert by symbol+timestamp)."""
        ...

    def recent_reports(
        self, *, symbol: str | None = None, limit: int = 20
    ) -> list[ReconciliationReport]:
        """Most recent reports (newest first), optionally filtered by symbol."""
        ...

    def count(self, symbol: str | None = None) -> int:
        """Number of stored reports, optionally per symbol."""
        ...
