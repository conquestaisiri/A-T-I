# backend/application/interfaces/context_repository.py
"""Port for persisting and querying MarketContexts.

The port lives in the application layer; the concrete SQLite implementation
lives in infrastructure. Provides the durable history that observability and,
later, the decision layer depend on.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.context.market_context import MarketContext


class ContextRepository(ABC):
    """Contract for durable storage of computed market contexts."""

    @abstractmethod
    def save(self, context: MarketContext) -> None:
        """Persist a market context at-least-once."""

    @abstractmethod
    def latest(self, symbol: str) -> MarketContext | None:
        """Return the most recently created context for a symbol, or ``None``."""

    @abstractmethod
    def history(self, symbol: str, limit: int = 20) -> list[MarketContext]:
        """Return the most recent contexts for a symbol, newest first.

        ``limit`` bounds the result set; it must be a positive integer.
        """
