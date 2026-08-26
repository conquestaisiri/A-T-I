# backend/domain/decision/__init__.py
"""Decision domain: the Decision Proposal schema of record (Document 05).

A Decision Proposal is a structured, serializable contract describing one
candidate decision. It flows out of the (out-of-band) reasoning layer and into
the risk gate. Proposals are validated, not trusted; the risk service holds
veto authority. The AI never emits orders — only proposals.
"""

from .proposal import (
    AlternativeConsidered,
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)

__all__ = [
    "AlternativeConsidered",
    "DecisionProposal",
    "EvidenceItem",
    "Hypothesis",
    "ProposedAction",
    "ProposedActionType",
    "RiskContext",
]
