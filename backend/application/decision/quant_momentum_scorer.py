# backend/application/decision/quant_momentum_scorer.py
"""Deterministic quant-only reasoner (ablation baseline for P5-007).

The Strategic Review demands the AI reasoner's incremental contribution be
measured against non-AI baselines on identical out-of-sample folds. This
scorer is the "quant-only" cell of that ablation matrix: a pure quantitative
signal consumer with no AI and no rule-bracket logic. It enters the sign of
raw price momentum (rate-of-change) and sizes by signal strength.

Why it exists as a separate cell rather than a RuleBasedSolver
---------------------------------------------------------------
The ``RuleBasedSolver`` confirms trend with momentum and refuses entries in
high-volatility regimes; it is the conservative "rules-only" cell. This
scorer deliberately drops both guards (no trend confirmation, no volatility
cap), so the ablation can measure what each guard is worth:
- rules-only vs quant-only isolates the value of the trend/volatility rules;
- AI-only vs quant+AI isolates the value of AI consuming quant signals.

It still carries a protective stop-loss/take-profit bracket: the risk gate
vetoes risk-increasing actions without one (mandatory OCO bracket invariant),
so a plan-less reasoner measures "rejected by the risk gate", not signal
quality.

Fully deterministic and stateless, like the rule-based solver: the same
context and risk snapshot always produce the same proposal (ADR 0009).
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.decision.trade_plan import (
    PostTradePlan,
    PreTradePlan,
    bracket_plan,
    stop_distance_from_volatility,
)


@dataclass(frozen=True, slots=True)
class QuantScorerConfig:
    """Tuning knobs for the quant momentum scorer.

    Attributes
    ----------
    base_size_fraction: float
        Proposed position size for a unit-strength signal (fraction of
        account equity).
    max_size_fraction: float
        Cap on the proposed size: signal strength scales size up to this
        bound, never beyond it.
    min_roc_pct: float
        Absolute rate-of-change below which the scorer stands aside
        (default 0: any non-zero momentum sign is tradable).
    """

    base_size_fraction: float = 0.10
    max_size_fraction: float = 0.25
    min_roc_pct: float = 0.0

    def __post_init__(self) -> None:
        if self.base_size_fraction <= 0.0:
            raise ValueError("base_size_fraction must be positive")
        if self.max_size_fraction < self.base_size_fraction:
            raise ValueError("max_size_fraction must be >= base_size_fraction")
        if self.min_roc_pct < 0.0:
            raise ValueError("min_roc_pct must be non-negative")


class QuantMomentumScorer(AIReasoner):
    """Enter the sign of raw price momentum, sized by signal strength."""

    def __init__(self, config: QuantScorerConfig | None = None) -> None:
        self._config = config or QuantScorerConfig()

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        """Propose a direction from momentum alone, or stand aside."""
        roc = self._rate_of_change(context)
        evidence = self._evidence(context)
        if roc is None:
            return self._stand_aside(
                context, risk_context, evidence, "Momentum feature unavailable."
            )
        if abs(roc) <= self._config.min_roc_pct:
            return self._stand_aside(
                context,
                risk_context,
                evidence,
                f"Rate of change {roc:.4f}% below the minimum entry threshold.",
            )

        direction = "up" if roc > 0.0 else "down"
        action_type = (
            ProposedActionType.ENTER_LONG if direction == "up" else ProposedActionType.ENTER_SHORT
        )
        size_fraction = min(
            self._config.max_size_fraction,
            self._config.base_size_fraction * (1.0 + abs(roc)),
        )
        action = ProposedAction(
            action_type=action_type,
            size_fraction=round(size_fraction, 6),
            order=1,
            rationale=f"Quant momentum signal: rate of change {roc:.4f}%.",
        )
        pre_trade_plan = self._bracket_plan(context)
        return DecisionProposal(
            proposal_id=f"quant-{context.snapshot.symbol}-{context.created_at.isoformat(timespec='milliseconds')}",
            correlation_id=context.snapshot.symbol,
            created_at=context.created_at,
            symbol=context.snapshot.symbol,
            hypothesis=Hypothesis(
                statement="Directional bias from raw price momentum.",
                supporting_evidence=evidence,
                opposing_evidence=(),
            ),
            confidence=0.5,
            uncertainty="Quant momentum only; no trend confirmation or volatility guard.",
            actions=(action,),
            risk_context=risk_context,
            alternatives=(),
            rationale=f"Quant-only scorer entered {direction} on rate of change {roc:.4f}%.",
            pre_trade_plan=pre_trade_plan,
            post_trade_plan=PostTradePlan() if pre_trade_plan is not None else None,
        )

    def _stand_aside(
        self,
        context: MarketContext,
        risk_context: RiskContext,
        evidence: tuple[EvidenceItem, ...],
        note: str,
    ) -> DecisionProposal:
        action = ProposedAction(
            action_type=ProposedActionType.STAND_ASIDE,
            size_fraction=self._config.base_size_fraction,
            order=1,
            rationale=note,
        )
        return DecisionProposal(
            proposal_id=f"quant-{context.snapshot.symbol}-{context.created_at.isoformat(timespec='milliseconds')}",
            correlation_id=context.snapshot.symbol,
            created_at=context.created_at,
            symbol=context.snapshot.symbol,
            hypothesis=Hypothesis(
                statement="Directional bias from raw price momentum.",
                supporting_evidence=evidence,
                opposing_evidence=(),
            ),
            confidence=0.5,
            uncertainty="Quant momentum only; no trend confirmation or volatility guard.",
            actions=(action,),
            risk_context=risk_context,
            alternatives=(),
            rationale=note,
        )

    @staticmethod
    def _rate_of_change(context: MarketContext) -> float | None:
        """The momentum feature's rate_of_change_pct, None when unavailable."""
        try:
            feature = context.feature("momentum")
        except KeyError:
            return None
        value = feature.value
        if not isinstance(value, dict):
            return None
        roc = value.get("rate_of_change_pct")
        if not isinstance(roc, (int, float)):
            return None
        return float(roc)

    @staticmethod
    def _evidence(context: MarketContext) -> tuple[EvidenceItem, ...]:
        items: list[EvidenceItem] = []
        for name in ("momentum", "trend", "volatility"):
            try:
                value = context.feature(name).value
            except KeyError:
                continue
            items.append(EvidenceItem(source=name, summary=f"{name} feature", value=value))
        return tuple(items)

    @staticmethod
    def _bracket_plan(context: MarketContext) -> PreTradePlan:
        """A protective bracket, sized to the observed volatility when available.

        The risk gate rejects risk-increasing actions without a stop-loss and
        take-profit (mandatory OCO bracket invariant), so the quant cell must
        carry one just like the rule-based solver does. ``PostTradePlan``
        accepts a bracket-derived plan only.
        """
        std_dev: float | None = None
        try:
            feature = context.feature("volatility")
            if isinstance(feature.value, dict):
                std_dev = feature.value.get("std_dev_pct")
        except KeyError:
            std_dev = None
        stop = stop_distance_from_volatility(std_dev if isinstance(std_dev, (int, float)) else None)
        return bracket_plan(stop)
