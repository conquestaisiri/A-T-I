# backend/domain/risk/risk_decision.py
"""The risk gate's verdict: approval with veto authority."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class RiskVerdict(enum.StrEnum):
    """Outcome of a risk evaluation.

    APPROVED — the proposal may proceed.
    REJECTED — denied outright (circuit breaker or rule).
    REDUCED — approved on a smaller size than requested.
    CONFIRM — requires operator confirmation before proceeding.
    """

    APPROVED = "approved"
    REJECTED = "rejected"
    REDUCED = "reduced"
    CONFIRM = "confirm"


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Result of subjecting a proposal to the risk gate.

    Attributes
    ----------
    verdict: RiskVerdict
        The gate's decision.
    reason: str
        Human-readable explanation of the decision.
    approved_size_fraction: float | None
        Maximum size fraction allowed, when relevant (REDUCED/APPROVED).
    evaluated_at: datetime
        When the decision was made (aware UTC).
    """

    verdict: RiskVerdict
    reason: str
    approved_size_fraction: float | None
    evaluated_at: datetime

    def __post_init__(self) -> None:
        import math

        if self.evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if self.approved_size_fraction is not None:
            v = self.approved_size_fraction
            if not math.isfinite(v) or not 0 <= v <= 1:
                raise ValueError("approved_size_fraction must be finite in [0,1] when set")

    @property
    def approved(self) -> bool:
        """Whether the proposal may act (fully or reduced)."""
        return self.verdict in (RiskVerdict.APPROVED, RiskVerdict.REDUCED)

    def as_dict(self) -> dict[str, Any]:
        """Serialise the decision to a plain dictionary."""
        return {
            "verdict": self.verdict.value,
            "reason": self.reason,
            "approved_size_fraction": self.approved_size_fraction,
            "evaluated_at": self.evaluated_at.isoformat(timespec="milliseconds"),
        }
