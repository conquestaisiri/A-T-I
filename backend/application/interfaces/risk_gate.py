# backend/application/interfaces/risk_gate.py
"""Port for the risk gate with veto authority.

The risk gate is deterministic, fully tested, and holds veto power over every
proposal. It must never predict markets — it only protects capital. The AI and
any strategy code may not bypass it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.decision.proposal import DecisionProposal
from backend.domain.risk.risk_decision import RiskDecision


class RiskGate(ABC):
    """Contract for evaluating a Decision Proposal against risk policy."""

    @abstractmethod
    def evaluate(self, proposal: DecisionProposal, mark_price: float | None = None) -> RiskDecision:
        """Evaluate a proposal and return a verdict with veto authority.

        ``mark_price`` is the current reference price, used for sizing logic
        that must be deterministic in replay (simulator-supplied, never
        clock/feed-driven).

        The gate is deterministic: the same proposal plus the same internal
        state yields the same verdict.
        """
        raise NotImplementedError
