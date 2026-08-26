# backend/application/interfaces/memory_store.py
"""Port for durable, bounded episodic memory.

Constitution Document 05: ATI governs what is remembered and how memory is
used, but does NOT own the storage backend (SQLite first, swappable behind
this contract). Memory is bounded, explainable, and grounded in market
outcomes — not in raw prompts or conversations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.memory.episode import MemoryEpisode


class MemoryStore(ABC):
    """Contract for persisting and recalling episodic market memory."""

    @abstractmethod
    def record(self, episode: MemoryEpisode) -> None:
        """Persist an episode at-least-once (idempotent by ``episode_id``)."""

    @abstractmethod
    def recall(self, symbol: str, limit: int = 10) -> list[MemoryEpisode]:
        """Return the most recent episodes for a symbol, oldest first.

        ``limit`` bounds the episode count; it must be a positive integer.
        """

    @abstractmethod
    def count(self, symbol: str | None = None) -> int:
        """Return the number of stored episodes, optionally per symbol."""
