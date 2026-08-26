# backend/application/interfaces/ledger_repository.py
"""Port for the durable Trade Outcome Ledger.

The ledger is the first learning artifact (Constitution Document 05). Every
trade outcome is recorded durably; reflection and learning read from it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.execution.trade_record import TradeRecord


class LedgerRepository(ABC):
    """Contract for persisting and querying trade records."""

    @abstractmethod
    def save(self, record: TradeRecord) -> None:
        """Persist a trade record. Re-saving the same ``trade_id`` is idempotent."""

    @abstractmethod
    def find_by_id(self, trade_id: str) -> TradeRecord | None:
        """Return a trade record by id, or ``None`` if absent."""

    @abstractmethod
    def find_recent(self, symbol: str, limit: int = 20) -> list[TradeRecord]:
        """Return the most recent trade records for a symbol, oldest first.

        ``limit`` must be a positive integer.
        """

    @abstractmethod
    def open_trades(self) -> list[TradeRecord]:
        """Return all currently open trade records."""

    @abstractmethod
    def closed_trades(self, limit: int = 100) -> list[TradeRecord]:
        """Return the most recent closed trade records, oldest first.

        ``limit`` must be a positive integer.
        """

    @abstractmethod
    def count(self, symbol: str | None = None) -> int:
        """Return the number of ledger rows, optionally per symbol."""
