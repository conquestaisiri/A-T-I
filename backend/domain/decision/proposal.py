# backend/domain/decision/proposal.py
"""The Decision Proposal schema of record (Constitution Document 05).

A Decision Proposal is an immutable, serializable contract describing one
candidate decision. It carries everything downstream — planning, risk,
execution, ledger, learning — the model around: hypothesis, evidence,
confidence, uncertainty, ordered action set, risk context, alternatives, and
rationale.

The AI never emits orders; it emits proposals. Proposals are validated on the
way in (``from_dict`` rejects malformed input) and are never trusted blindly:
the risk gate holds veto authority.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.domain.decision.trade_plan import PostTradePlan, PreTradePlan


class ProposedActionType(enum.StrEnum):
    """Ordered candidate actions a proposal may recommend."""

    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT = "exit"
    SCALE_IN = "scale_in"
    SCALE_OUT = "scale_out"
    STAND_ASIDE = "stand_aside"
    REDUCE_RISK = "reduce_risk"


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    """A single piece of supporting or opposing evidence.

    Attributes
    ----------
    source: str
        Feature or observation name the evidence comes from.
    summary: str
        Human-readable summary of what the evidence shows.
    value: Any
        The quantitative value, if any.
    """

    source: str
    summary: str
    value: Any

    def as_dict(self) -> dict[str, Any]:
        """Serialise the evidence item to a plain dictionary."""
        return {"source": self.source, "summary": self.summary, "value": self.value}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        """Reconstruct an evidence item from :meth:`as_dict` output."""
        return cls(source=str(data["source"]), summary=str(data["summary"]), value=data["value"])


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """What the AI believes and why.

    Attributes
    ----------
    statement: str
        The hypothesis, e.g. "trend continuation".
    supporting_evidence: tuple[EvidenceItem, ...]
        Observations/features that support it.
    opposing_evidence: tuple[EvidenceItem, ...]
        Observations/features that argue against it.
    """

    statement: str
    supporting_evidence: tuple[EvidenceItem, ...]
    opposing_evidence: tuple[EvidenceItem, ...]

    def as_dict(self) -> dict[str, Any]:
        """Serialise the hypothesis to a plain dictionary."""
        return {
            "statement": self.statement,
            "supporting_evidence": [e.as_dict() for e in self.supporting_evidence],
            "opposing_evidence": [e.as_dict() for e in self.opposing_evidence],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Hypothesis:
        """Reconstruct a hypothesis from :meth:`as_dict` output."""
        supporting = _evidence_list(data["supporting_evidence"])
        opposing = _evidence_list(data["opposing_evidence"])
        return cls(
            statement=str(data["statement"]),
            supporting_evidence=supporting,
            opposing_evidence=opposing,
        )


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """A single ordered candidate action within a proposal.

    Attributes
    ----------
    action_type: ProposedActionType
        What the action is (enter/exit/scale/stand aside/reduce risk).
    size_fraction: float
        Fraction of the account/position to apply (0 < size <= 1).
    order: int
        Position of this action in the ordered action set.
    rationale: str
        Why this action is proposed.
    """

    action_type: ProposedActionType
    size_fraction: float
    order: int
    rationale: str

    def __post_init__(self) -> None:
        if not 0.0 < self.size_fraction <= 1.0:
            raise ValueError("size_fraction must be in (0, 1]")

    def as_dict(self) -> dict[str, Any]:
        """Serialise the action to a plain dictionary."""
        return {
            "action_type": self.action_type.value,
            "size_fraction": self.size_fraction,
            "order": self.order,
            "rationale": self.rationale,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProposedAction:
        """Reconstruct an action from :meth:`as_dict` output."""
        return cls(
            action_type=ProposedActionType(data["action_type"]),
            size_fraction=float(data["size_fraction"]),
            order=int(data["order"]),
            rationale=str(data["rationale"]),
        )


@dataclass(frozen=True, slots=True)
class AlternativeConsidered:
    """An alternative that was considered and rejected.

    Attributes
    ----------
    description: str
        What the alternative was.
    reason_rejected: str
        Why it was rejected.
    """

    description: str
    reason_rejected: str

    def as_dict(self) -> dict[str, Any]:
        """Serialise the alternative to a plain dictionary."""
        return {"description": self.description, "reason_rejected": self.reason_rejected}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AlternativeConsidered:
        """Reconstruct an alternative from :meth:`as_dict` output."""
        return cls(
            description=str(data["description"]),
            reason_rejected=str(data["reason_rejected"]),
        )


@dataclass(frozen=True, slots=True)
class RiskContext:
    """Snapshot of the risk state at the time the proposal was made.

    Attributes
    ----------
    account_equity: float
        Current account equity.
    open_exposure_pct: float
        Current open exposure as a fraction of equity.
    daily_loss_pct: float
        Loss already realised today as a fraction of equity.
    monthly_loss_pct: float
        Loss already realised this month as a fraction of equity.
    total_loss_pct: float
        Total realised loss relative to the initial deposit as a fraction.
    drawdown_pct: float
        Peak-to-current drawdown as a fraction of equity.
    position_count: int
        Number of open positions.
    """

    account_equity: float
    open_exposure_pct: float
    daily_loss_pct: float
    monthly_loss_pct: float
    total_loss_pct: float
    drawdown_pct: float
    position_count: int
    symbol_risk_used_pct: float = 0.0
    symbol_exposure_pct: float = 0.0
    portfolio_risk_used_pct: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Serialise the risk context to a plain dictionary."""
        return {
            "account_equity": self.account_equity,
            "open_exposure_pct": self.open_exposure_pct,
            "daily_loss_pct": self.daily_loss_pct,
            "monthly_loss_pct": self.monthly_loss_pct,
            "total_loss_pct": self.total_loss_pct,
            "drawdown_pct": self.drawdown_pct,
            "position_count": self.position_count,
            "symbol_risk_used_pct": self.symbol_risk_used_pct,
            "symbol_exposure_pct": self.symbol_exposure_pct,
            "portfolio_risk_used_pct": self.portfolio_risk_used_pct,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RiskContext:
        """Reconstruct a risk context from :meth:`as_dict` output."""
        return cls(
            account_equity=float(data["account_equity"]),
            open_exposure_pct=float(data["open_exposure_pct"]),
            daily_loss_pct=float(data["daily_loss_pct"]),
            monthly_loss_pct=float(data.get("monthly_loss_pct", 0.0)),
            total_loss_pct=float(data.get("total_loss_pct", 0.0)),
            drawdown_pct=float(data["drawdown_pct"]),
            position_count=int(data["position_count"]),
            symbol_risk_used_pct=float(data.get("symbol_risk_used_pct", 0.0)),
            symbol_exposure_pct=float(data.get("symbol_exposure_pct", 0.0)),
            portfolio_risk_used_pct=float(data.get("portfolio_risk_used_pct", 0.0)),
        )


@dataclass(frozen=True, slots=True)
class DecisionProposal:
    """An immutable, validated candidate decision (schema of record).

    Attributes
    ----------
    proposal_id: str
        Unique identifier of this proposal.
    correlation_id: str
        Stable correlation id tying related proposals across the pipeline.
    created_at: datetime
        Creation timestamp (aware UTC).
    symbol: str
        Market symbol the proposal concerns.
    hypothesis: Hypothesis
        What the AI believes and why.
    confidence: float
        Calibrated confidence in [0, 1], not a vibe.
    uncertainty: str
        Explicit acknowledgment of what is unknown.
    actions: tuple[ProposedAction, ...]
        Ordered candidate actions.
    risk_context: RiskContext
        Risk state at time of proposal.
    alternatives: tuple[AlternativeConsidered, ...]
        Alternatives considered and why rejected.
    rationale: str
        Human-readable explanation.
    """

    proposal_id: str
    correlation_id: str
    created_at: datetime
    symbol: str
    hypothesis: Hypothesis
    confidence: float
    uncertainty: str
    actions: tuple[ProposedAction, ...]
    risk_context: RiskContext
    alternatives: tuple[AlternativeConsidered, ...]
    rationale: str
    pre_trade_plan: PreTradePlan | None = None
    post_trade_plan: PostTradePlan | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        if not self.symbol:
            raise ValueError("symbol must be a non-empty string")

    @property
    def primary_action(self) -> ProposedAction | None:
        """Return the lowest-``order`` action, if any."""
        if not self.actions:
            return None
        return min(self.actions, key=lambda action: action.order)

    def as_dict(self) -> dict[str, Any]:
        """Serialise the proposal to a plain dictionary."""
        return {
            "proposal_id": self.proposal_id,
            "correlation_id": self.correlation_id,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "symbol": self.symbol,
            "hypothesis": self.hypothesis.as_dict(),
            "confidence": self.confidence,
            "uncertainty": self.uncertainty,
            "actions": [a.as_dict() for a in self.actions],
            "risk_context": self.risk_context.as_dict(),
            "alternatives": [a.as_dict() for a in self.alternatives],
            "rationale": self.rationale,
            "pre_trade_plan": (
                self.pre_trade_plan.as_dict() if self.pre_trade_plan is not None else None
            ),
            "post_trade_plan": (
                self.post_trade_plan.as_dict() if self.post_trade_plan is not None else None
            ),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DecisionProposal:
        """Reconstruct and validate a proposal from :meth:`as_dict` output.

        Raises
        ------
        ValueError
            If required fields are missing or produce an invalid proposal.
        """
        return cls(
            proposal_id=str(data["proposal_id"]),
            correlation_id=str(data["correlation_id"]),
            created_at=datetime.fromisoformat(data["created_at"]),
            symbol=str(data["symbol"]),
            hypothesis=Hypothesis.from_dict(data["hypothesis"]),
            confidence=float(data["confidence"]),
            uncertainty=str(data["uncertainty"]),
            actions=_action_list(data["actions"]),
            risk_context=RiskContext.from_dict(data["risk_context"]),
            alternatives=_alternatives_list(data["alternatives"]),
            rationale=str(data["rationale"]),
            pre_trade_plan=_optional_pre_trade_plan(data.get("pre_trade_plan")),
            post_trade_plan=_optional_post_trade_plan(data.get("post_trade_plan")),
        )


def _evidence_list(raw: object) -> tuple[EvidenceItem, ...]:
    if not isinstance(raw, list):
        raise ValueError("evidence must be a list")
    return tuple(EvidenceItem.from_dict(item) for item in raw if isinstance(item, dict))


def _action_list(raw: object) -> tuple[ProposedAction, ...]:
    if not isinstance(raw, list):
        raise ValueError("actions must be a list")
    return tuple(ProposedAction.from_dict(item) for item in raw if isinstance(item, dict))


def _alternatives_list(raw: object) -> tuple[AlternativeConsidered, ...]:
    if not isinstance(raw, list):
        raise ValueError("alternatives must be a list")
    return tuple(AlternativeConsidered.from_dict(item) for item in raw if isinstance(item, dict))


def _optional_pre_trade_plan(raw: object) -> PreTradePlan | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("pre_trade_plan must be a dict when present")
    return PreTradePlan.from_dict(raw)


def _optional_post_trade_plan(raw: object) -> PostTradePlan | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("post_trade_plan must be a dict when present")
    return PostTradePlan.from_dict(raw)
