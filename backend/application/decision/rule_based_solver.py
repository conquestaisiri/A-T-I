# backend/application/decision/rule_based_solver.py
"""Deterministic rule-based reasoner (ADR 0009).

The solver is the V1 implementation of the ``AIReasoner`` port: it consumes an
immutable MarketContext and a RiskContext and produces a DecisionProposal. It
is fully deterministic — the same context and risk snapshot always produce the
same proposal — and holds no model state. Thresholds are explicit configuration
(``SolverConfig``), not magic numbers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import (
    AlternativeConsidered,
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
class SolverConfig:
    """Thresholds for the deterministic reasoner.

    Attributes
    ----------
    momentum_entry_pct: float
        Absolute rate-of-change (percent) above which a momentum signal counts
        as a directional entry candidate.
    volatility_cap_pct: float
        Standard-deviation threshold (as a fraction) above which the solver
        refuses directional entries (avoid high-uncertainty regimes).
    base_size_fraction: float
        Default proposed position size as a fraction of account equity.
    stop_multiple: float
        Stop-loss distance as a multiple of feature volatility (2× default).
    min_stop_pct: float
        Floor on the stop distance, so brackets are never unrealistically tight.
    default_stop_pct: float
        Stop distance used when volatility is unavailable.
    risk_reward_ratio: float
        Take-profit distance as a multiple of the stop distance (2.0 default).
    risk_per_trade_pct: float
        Maximum fraction of equity the bracket may lose on this trade.
    """

    momentum_entry_pct: float = 0.005
    volatility_cap_pct: float = 0.05
    base_size_fraction: float = 0.10
    stop_multiple: float = 2.0
    min_stop_pct: float = 0.01
    default_stop_pct: float = 0.02
    risk_reward_ratio: float = 2.0
    risk_per_trade_pct: float = 0.02


class RuleBasedSolver(AIReasoner):
    """Turn market features into a directional proposal, or stand aside."""

    def __init__(self, config: SolverConfig | None = None) -> None:
        self._config = config or SolverConfig()

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        """Produce a deterministic proposal from ``context`` and ``risk_context``."""
        evidence = self._collect_evidence(context)
        trend = self._value(context, "trend")
        momentum = self._value(context, "momentum")
        volatility = self._value(context, "volatility")

        direction = self._direction(trend, momentum)
        if direction is None:
            return self._stand_aside_proposal(context, risk_context, evidence)
        if trend is None or momentum is None:
            return self._stand_aside_proposal(context, risk_context, evidence)

        # Volume participation confirmation (when a volume feature is present):
        # entries require above-average participation so signals are not driven
        # by thin, low-conviction prints. Missing volume data never blocks.
        volume = self._value(context, "volume")
        if not self._volume_confirms(volume):
            return self._stand_aside_proposal(
                context,
                risk_context,
                evidence,
                note="Volume below its recent average; refusing a low-participation entry.",
            )

        if volatility and self._volatility_above_cap(volatility):
            return self._stand_aside_proposal(
                context,
                risk_context,
                evidence,
                note="Volatility exceeds the solver cap; refusing a directional entry.",
            )

        action_type = (
            ProposedActionType.ENTER_LONG if direction == "up" else ProposedActionType.ENTER_SHORT
        )
        confidence = self._confidence(direction, trend, momentum)
        action = ProposedAction(
            action_type=action_type,
            size_fraction=self._config.base_size_fraction,
            order=1,
            rationale=f"{direction.title()} momentum signal from trend and rate-of-change.",
        )
        pre_trade_plan = bracket_plan(
            self._stop_distance(volatility),
            risk_per_trade_pct=self._config.risk_per_trade_pct,
            risk_reward_ratio=self._config.risk_reward_ratio,
        )
        alternatives = self._alternatives(direction)
        rationale = (
            f"Deterministic solver: {direction} trend with consistent momentum "
            f"(confidence {confidence:.2f})."
        )
        return self._proposal(
            context,
            risk_context,
            evidence,
            (action,),
            confidence,
            alternatives,
            rationale,
            pre_trade_plan=pre_trade_plan,
        )

    def _stop_distance(self, volatility: dict[str, Any] | None) -> float:
        """Stop distance (as a fraction) derived from feature volatility."""
        std_dev = None
        if isinstance(volatility, dict):
            raw = volatility.get("std_dev")
            if isinstance(raw, (int, float)):
                std_dev = float(raw)
        return stop_distance_from_volatility(
            std_dev,
            multiple=self._config.stop_multiple,
            floor=self._config.min_stop_pct,
            default=self._config.default_stop_pct,
        )

    def _proposal(
        self,
        context: MarketContext,
        risk_context: RiskContext,
        evidence: tuple[EvidenceItem, ...],
        actions: tuple[ProposedAction, ...],
        confidence: float,
        alternatives: tuple[AlternativeConsidered, ...],
        rationale: str,
        pre_trade_plan: PreTradePlan | None = None,
    ) -> DecisionProposal:
        symbol = context.snapshot.symbol
        created_at = context.created_at
        return DecisionProposal(
            proposal_id=f"prop-{symbol}-{created_at.isoformat(timespec='milliseconds')}",
            correlation_id=symbol,
            created_at=created_at,
            symbol=symbol,
            hypothesis=Hypothesis(
                statement="Directional bias from trend and momentum features.",
                supporting_evidence=evidence,
                opposing_evidence=(),
            ),
            confidence=confidence,
            uncertainty=self._uncertainty(),
            actions=actions,
            risk_context=risk_context,
            alternatives=alternatives,
            rationale=rationale,
            pre_trade_plan=pre_trade_plan,
            post_trade_plan=PostTradePlan() if pre_trade_plan is not None else None,
        )

    def _stand_aside_proposal(
        self,
        context: MarketContext,
        risk_context: RiskContext,
        evidence: tuple[EvidenceItem, ...],
        note: str | None = None,
    ) -> DecisionProposal:
        action = ProposedAction(
            action_type=ProposedActionType.STAND_ASIDE,
            size_fraction=self._config.base_size_fraction,
            order=1,
            rationale=note or "No consistent directional signal; standing aside.",
        )
        return self._proposal(
            context,
            risk_context,
            evidence,
            (action,),
            confidence=0.5,
            alternatives=(),
            rationale=note or "No consistent directional signal from trend and momentum.",
        )

    def _direction(
        self, trend: dict[str, Any] | None, momentum: dict[str, Any] | None
    ) -> str | None:
        if not isinstance(trend, dict) or not isinstance(momentum, dict):
            return None
        trend_direction = trend.get("direction")
        rate_of_change = momentum.get("rate_of_change_pct")
        if not isinstance(trend_direction, str) or not isinstance(rate_of_change, (int, float)):
            return None
        # Enforce the configured momentum threshold: the rate-of-change must be
        # material, not merely non-zero (a 1e-9 ROC is not an entry signal).
        if abs(float(rate_of_change)) < self._config.momentum_entry_pct:
            return None
        if trend_direction == "up" and rate_of_change > 0:
            return "up"
        if trend_direction == "down" and rate_of_change < 0:
            return "down"
        return None

    @staticmethod
    def _volume_confirms(volume: dict[str, Any] | None) -> bool:
        """Return True when volume participation supports an entry.

        Uses the feature's ``volume_ratio`` (most recent print vs window
        average). Absent/insufficient data confirms by default — the gate only
        vetoes on positive evidence of a thin, low-conviction print.
        """
        if not isinstance(volume, dict):
            return True
        ratio = volume.get("volume_ratio")
        if not isinstance(ratio, (int, float)):
            return True
        return float(ratio) >= 0.5

    def _volatility_above_cap(self, volatility: dict[str, Any]) -> bool:
        std_dev = volatility.get("std_dev")
        return isinstance(std_dev, (int, float)) and std_dev > self._config.volatility_cap_pct

    @staticmethod
    def _confidence(direction: str, trend: dict[str, Any], momentum: dict[str, Any]) -> float:
        magnitude = abs(float(momentum.get("rate_of_change_pct", 0.0)))
        change_pct = abs(float(trend.get("change_pct", 0.0)))
        strength = min(1.0, (magnitude + change_pct) / 2.0 / max(1.0, abs(magnitude) + 1.0) * 2.0)
        base = 0.5 + 0.4 * strength
        return round(min(0.95, base), 4)

    @staticmethod
    def _alternatives(direction: str) -> tuple[AlternativeConsidered, ...]:
        if direction == "up":
            return (
                AlternativeConsidered(
                    description="Short the market",
                    reason_rejected="Trend and momentum are both positive.",
                ),
            )
        return (
            AlternativeConsidered(
                description="Long the market",
                reason_rejected="Trend and momentum are both negative.",
            ),
        )

    def _uncertainty(self) -> str:
        return "Feature-based estimate only; no forward-looking news or order-flow information."

    def _collect_evidence(self, context: MarketContext) -> tuple[EvidenceItem, ...]:
        items: list[EvidenceItem] = []
        for name in ("trend", "momentum", "volatility", "volume", "liquidity"):
            value = self._value(context, name)
            if value is not None:
                items.append(
                    EvidenceItem(
                        source=name,
                        summary=f"{name} feature",
                        value=value,
                    )
                )
        return tuple(items)

    @staticmethod
    def _value(context: MarketContext, name: str) -> dict[str, Any] | None:
        try:
            feature = context.feature(name)
        except KeyError:
            return None
        value = feature.value
        return value if isinstance(value, dict) else None
