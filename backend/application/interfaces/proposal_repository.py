# backend/application/interfaces/proposal_repository.py
"""Port for durable storage of Decision Proposals."""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.decision.proposal import DecisionProposal


class ProposalRepository(ABC):
    """Contract for persisting and querying decision proposals."""

    @abstractmethod
    def save(self, proposal: DecisionProposal) -> None:
        """Persist a proposal at-least-once.

        Re-saving the same ``proposal_id`` must be idempotent.
        """

    @abstractmethod
    def find_by_id(self, proposal_id: str) -> DecisionProposal | None:
        """Return a proposal by id, or ``None`` if absent."""

    @abstractmethod
    def find_recent(self, symbol: str, limit: int = 20) -> list[DecisionProposal]:
        """Return the most recent proposals for a symbol, oldest first.

        ``limit`` must be a positive integer.
        """

    @abstractmethod
    def count(self, symbol: str | None = None) -> int:
        """Return the number of persisted proposals, optionally per symbol."""
