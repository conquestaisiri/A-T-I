# backend/application/interfaces/ai_reasoner.py
"""Port for the reasoning step: MarketContext in, DecisionProposal out.

The reasoner is the V1 reasoning surface (ADR 0006, 0009). It consumes an
immutable persisted MarketContext plus the current risk snapshot and emits a
DecisionProposal. It never emits orders and never imports an AI client: the
deterministic solver is one implementation, a free-tier LLM another, both
behind this port.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import DecisionProposal, RiskContext


class AIReasoner(ABC):
    """Contract for turning a MarketContext into a DecisionProposal."""

    @abstractmethod
    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        """Produce a decision proposal for ``context``.

        Parameters
        ----------
        context: MarketContext
            Immutable features + snapshot metadata for one symbol.
        risk_context: RiskContext
            Current account risk state used for sizing and context.

        Returns
        -------
        DecisionProposal
            A validated, serialisable proposal. Deterministic implementations
            must return identical proposals for identical inputs.
        """
        raise NotImplementedError
