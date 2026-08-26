# backend/domain/memory/episode.py
"""The episodic memory value: one decision and its realised outcome.

An episode is the smallest unit of experience ATI remembers (Constitution
Document 05). It binds a decision (proposal id, action, confidence) to the
market outcome that followed (realised PnL, win/loss). Bounded, explainable,
and durable — never raw prompts, never secrets.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class MemoryOutcome(enum.StrEnum):
    """What a remembered decision actually produced."""

    OPEN = "open"
    WIN = "win"
    LOSS = "loss"
    FLAT = "flat"
    STAND_ASIDE = "stand_aside"


@dataclass(frozen=True, slots=True)
class MemoryEpisode:
    """One bounded episodic record.

    Attributes
    ----------
    episode_id: str
        Unique identifier of this memory episode.
    correlation_id: str
        Stable id tying this episode to the pipeline that produced it.
    symbol: str
        Market symbol the episode concerns.
    created_at: datetime
        When the underlying decision was made (aware UTC).
    proposal_id: str
        The proposal that produced this episode.
    action_type: str
        Primary proposed action (``ProposedActionType`` value).
    confidence: float
        Confidence recorded on the proposal, in [0, 1].
    outcome: MemoryOutcome
        Realised result of the decision.
    realized_pnl: float | None
        Realised PnL when the outcome was monetary (None while open).
    summary: str
        One-line, human-readable explanation of what happened.
    """

    episode_id: str
    correlation_id: str
    symbol: str
    created_at: datetime
    proposal_id: str
    action_type: str
    confidence: float
    outcome: MemoryOutcome
    realized_pnl: float | None
    summary: str

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")

    def as_dict(self) -> dict[str, Any]:
        """Serialise the episode to a plain dictionary."""
        return {
            "episode_id": self.episode_id,
            "correlation_id": self.correlation_id,
            "symbol": self.symbol,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "proposal_id": self.proposal_id,
            "action_type": self.action_type,
            "confidence": self.confidence,
            "outcome": self.outcome.value,
            "realized_pnl": self.realized_pnl,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEpisode:
        """Reconstruct an episode from :meth:`as_dict` output."""
        return cls(
            episode_id=str(data["episode_id"]),
            correlation_id=str(data["correlation_id"]),
            symbol=str(data["symbol"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            proposal_id=str(data["proposal_id"]),
            action_type=str(data["action_type"]),
            confidence=float(data["confidence"]),
            outcome=MemoryOutcome(data["outcome"]),
            realized_pnl=(
                float(data["realized_pnl"]) if data.get("realized_pnl") is not None else None
            ),
            summary=str(data["summary"]),
        )
