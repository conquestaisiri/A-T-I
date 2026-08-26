# backend/infrastructure/ai/pydantic_ai_reasoner.py
"""PydanticAI-backed reasoner behind the ``AIReasoner`` port (ADR 0011).

This adapter replaces the raw OmniRoute HTTP client with PydanticAI's typed,
validated, retry-bounded structured output. It preserves the exact same
``AIReasoner`` interface and ``DecisionProposal`` schema, degrading to
``STAND_ASIDE`` on any failure (ADR 0005).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.interfaces.memory_store import MemoryStore
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
from backend.infrastructure.ai.prompt_builder import (
    DEFAULT_RECALL_LIMIT as _PROMPT_DEFAULT_RECALL_LIMIT,
)
from backend.infrastructure.ai.prompt_builder import SYSTEM_PROMPT as _PROMPT_SYSTEM_PROMPT
from backend.infrastructure.ai.prompt_builder import (
    build_messages as _build_deterministic_messages,
)
from pydantic import BaseModel, Field, field_validator
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = "ai_unavailable"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Pydantic output model — mirrors DecisionProposal schema exactly
# ---------------------------------------------------------------------------


class _ActionOutput(BaseModel):
    action_type: ProposedActionType
    size_fraction: float = Field(gt=0.0, le=1.0)
    order: int = Field(ge=1)
    rationale: str = ""


class _AlternativeOutput(BaseModel):
    description: str = ""
    reason_rejected: str = ""


class _EvidenceOutput(BaseModel):
    source: str
    summary: str
    value: Any = None


class _HypothesisOutput(BaseModel):
    statement: str = ""
    supporting_evidence: list[_EvidenceOutput] = []
    opposing_evidence: list[_EvidenceOutput] = []


class _RiskContextOutput(BaseModel):
    account_equity: float
    open_exposure_pct: float
    daily_loss_pct: float
    monthly_loss_pct: float
    total_loss_pct: float
    drawdown_pct: float
    position_count: int


class _DecisionProposalOutput(BaseModel):
    """Pydantic-validated output matching DecisionProposal schema."""

    confidence: float = Field(ge=0.0, le=1.0)
    uncertainty: str = ""
    hypothesis_statement: str = ""
    action_type: ProposedActionType
    size_fraction: float = Field(gt=0.0, le=1.0)
    rationale: str = ""
    alternatives: list[_AlternativeOutput] = []

    @field_validator("confidence", mode="before")
    @classmethod
    def _coerce_confidence(cls, v: Any) -> float:
        return float(v)

    @field_validator("size_fraction", mode="before")
    @classmethod
    def _coerce_size_fraction(cls, v: Any) -> float:
        return float(v)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PydanticAIConfig:
    """Configuration for the PydanticAI reasoner.

    Attributes
    ----------
    base_url: str
        OpenAI-compatible endpoint (OmniRoute router at ``localhost:20128/v1``).
    model: str | None
        Explicit model name; ``None`` lets the router pick.
    timeout_seconds: float
        Per-request timeout (includes retries).
    temperature: float
        Sampling temperature (low for disciplined output).
    max_tokens: int
        Response token cap.
    max_retries: int
        Maximum validation retries (PydanticAI output retries).
    tool_limit: int
        Maximum tool calls per request (not used by this reasoner, but bounded).
    recall_limit: int
        Max episodic memory episodes to include in context per symbol.
    """

    base_url: str = "http://localhost:20128/v1"
    model: str | None = None
    timeout_seconds: float = 30.0
    temperature: float = 0.2
    max_tokens: int = 600
    max_retries: int = 2
    tool_limit: int = 0
    recall_limit: int = _PROMPT_DEFAULT_RECALL_LIMIT


# ---------------------------------------------------------------------------
# Reasoner implementation
# ---------------------------------------------------------------------------


class PydanticAIReasoner(AIReasoner):
    """PydanticAI-backed AIReasoner with structured output, validation, retries."""

    def __init__(
        self,
        config: PydanticAIConfig | None = None,
        *,
        memory_store: MemoryStore | None = None,
        clock: Any | None = None,
    ) -> None:
        self._config = config or PydanticAIConfig()
        self._memory_store = memory_store
        self._clock = clock or _utcnow

        # Degradation observability (ADR 0005).
        self._failures: int = 0
        self._last_failure_reason: str | None = None
        self._last_failure_at: datetime | None = None
        self._last_failure_duration_ms: float | None = None

        # Build the PydanticAI Agent
        provider = OpenAIProvider(base_url=self._config.base_url.rstrip("/"))
        model = OpenAIChatModel(
            self._config.model or "auto",
            provider=provider,
        )

        self._agent = Agent(
            model=model,
            output_type=_DecisionProposalOutput,
            system_prompt=self._system_prompt(),
            retries=self._config.max_retries,
        )

        # Usage tracking (PydanticAI provides this via result.usage())
        self._total_tokens: int = 0
        self._total_requests: int = 0

    @property
    def failure_count(self) -> int:
        """Number of degraded (failed) reasoning attempts."""
        return self._failures

    @property
    def last_failure_reason(self) -> str | None:
        """Reason for the most recent failure, if any."""
        return self._last_failure_reason

    @property
    def total_tokens_used(self) -> int:
        return self._total_tokens

    @property
    def total_requests(self) -> int:
        return self._total_requests

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        """Produce a proposal, degrading to STAND_ASIDE on any failure."""
        started = time.perf_counter()
        try:
            user_prompt = self._build_user_prompt(context, risk_context)

            result = self._agent.run_sync(user_prompt)

            # Track usage
            usage = result.usage
            self._total_tokens += usage.total_tokens
            self._total_requests += 1

            proposal = self._proposal_from_output(result.output, context, risk_context)

            duration = (time.perf_counter() - started) * 1000.0
            logger.info(
                "PydanticAI reason ok symbol=%s proposal=%s latency_ms=%.1f tokens=%d",
                context.snapshot.symbol,
                proposal.proposal_id,
                duration,
                usage.total_tokens,
            )
            return proposal

        except Exception as exc:  # noqa: BLE001 - every failure degrades safely
            duration = (time.perf_counter() - started) * 1000.0
            self._record_failure(exc, duration)
            return self._stand_aside(context, risk_context, reason=str(exc))

    def close(self) -> None:
        """No persistent resources to release (PydanticAI manages its own client)."""
        pass

    # -----------------------------------------------------------------------
    # Prompt construction
    # -----------------------------------------------------------------------

    def _system_prompt(self) -> str:
        return _PROMPT_SYSTEM_PROMPT

    def _build_user_prompt(self, context: MarketContext, risk_context: RiskContext) -> str:
        messages = _build_deterministic_messages(
            context,
            risk_context,
            memory_store=self._memory_store,
            recall_limit=self._config.recall_limit,
        )
        return messages[1]["content"]

    def _recall_for_prompt(self, symbol: str) -> list[dict[str, Any]]:
        from backend.infrastructure.ai.prompt_builder import _recall_for_prompt as _shared_recall

        return _shared_recall(self._memory_store, symbol, self._config.recall_limit)

    # -----------------------------------------------------------------------
    # Response conversion
    # -----------------------------------------------------------------------

    def _proposal_from_output(
        self,
        output: _DecisionProposalOutput,
        context: MarketContext,
        risk_context: RiskContext,
    ) -> DecisionProposal:
        created_at = self._clock()
        symbol = context.snapshot.symbol

        action = ProposedAction(
            action_type=ProposedActionType(output.action_type),
            size_fraction=output.size_fraction,
            order=1,
            rationale=output.rationale,
        )

        pre_trade_plan = self._plan(context, action.action_type)

        alternatives = tuple(
            AlternativeConsidered(
                description=alt.description,
                reason_rejected=alt.reason_rejected,
            )
            for alt in output.alternatives
        )

        hypothesis = Hypothesis(
            statement=output.hypothesis_statement,
            supporting_evidence=_evidence(context),
            opposing_evidence=(),
        )

        return DecisionProposal(
            proposal_id=f"prop-{symbol}-{created_at.isoformat(timespec='milliseconds')}",
            correlation_id=symbol,
            created_at=created_at,
            symbol=symbol,
            hypothesis=hypothesis,
            confidence=output.confidence,
            uncertainty=output.uncertainty,
            actions=(action,),
            risk_context=risk_context,
            alternatives=alternatives,
            rationale=output.rationale,
            pre_trade_plan=pre_trade_plan,
            post_trade_plan=PostTradePlan() if pre_trade_plan is not None else None,
        )

    def _plan(self, context: MarketContext, action_type: ProposedActionType) -> PreTradePlan | None:
        """Volatility-anchored protective bracket for risk-increasing actions.

        The model does not emit a bracket; this adapter supplies a deterministic
        1:2 plan so the mandatory-bracket invariant holds. Refusal/stand-aside
        and pure exits carry no plan.
        """
        if action_type not in (
            ProposedActionType.ENTER_LONG,
            ProposedActionType.ENTER_SHORT,
            ProposedActionType.SCALE_IN,
        ):
            return None
        std_dev: float | None = None
        try:
            value = context.feature("volatility").value
            raw = value.get("std_dev") if isinstance(value, dict) else None
            if isinstance(raw, (int, float)):
                std_dev = float(raw)
        except KeyError:
            std_dev = None
        return bracket_plan(stop_distance_from_volatility(std_dev))

    # -----------------------------------------------------------------------
    # Safe degradation (ADR 0005)
    # -----------------------------------------------------------------------

    def _record_failure(self, exc: Exception, duration_ms: float) -> None:
        self._failures += 1
        self._last_failure_reason = str(exc)
        self._last_failure_at = self._clock()
        self._last_failure_duration_ms = duration_ms
        logger.warning(
            "%s: PydanticAI reasoning failed: %s (latency_ms=%.1f, failures=%d)",
            AI_UNAVAILABLE,
            exc,
            duration_ms,
            self._failures,
        )

    def _stand_aside(
        self,
        context: MarketContext,
        risk_context: RiskContext,
        reason: str,
    ) -> DecisionProposal:
        symbol = context.snapshot.symbol
        created_at = self._clock()
        action = ProposedAction(
            action_type=ProposedActionType.STAND_ASIDE,
            size_fraction=0.10,
            order=1,
            rationale="AI unavailable; refusing to act. See ai_unavailable.",
        )
        return DecisionProposal(
            proposal_id=f"prop-{symbol}-{created_at.isoformat(timespec='milliseconds')}",
            correlation_id=symbol,
            created_at=created_at,
            symbol=symbol,
            hypothesis=Hypothesis(
                statement="No hypothesis; the reasoner failed to produce one.",
                supporting_evidence=_evidence(context),
                opposing_evidence=(),
            ),
            confidence=0.5,
            uncertainty="Reasoner was unavailable for this decision.",
            actions=(action,),
            risk_context=risk_context,
            alternatives=(),
            rationale=f"Standing aside: {reason}",
        )


# ---------------------------------------------------------------------------
# Shared helpers (mirrored from omni_route_reasoner for parity)
# ---------------------------------------------------------------------------


def _evidence(context: MarketContext) -> tuple[EvidenceItem, ...]:
    items: list[EvidenceItem] = []
    for name, feature in context.features:
        items.append(
            EvidenceItem(
                source=name,
                summary=f"{name} feature",
                value=feature.value,
            )
        )
    return tuple(items)
