# backend/infrastructure/ai/omni_route_reasoner.py
"""Free-tier LLM reasoner behind the ``AIReasoner`` port (ADR 0005, 0006).

This is the V1 real-brain implementation. It sends a compact, bounded prompt —
market features, the current risk snapshot, and recent episodic memory — to the
OmniRoute router (``localhost:20128/v1``, multi-provider failover), asks for
strict JSON, and validates the reply into a :class:`DecisionProposal`.

Safety contract (ADR 0005, 0009):
* The reasoner never emits orders — only proposals, always through the risk gate.
* Any failure (timeout, HTTP error, malformed/out-of-schema JSON) produces a
  conservative ``STAND_ASIDE`` proposal, never garbage.
* Every failure is logged with reason and duration and counted; an
  ``ai_unavailable`` condition is observable so operators see degraded
  reasoning rather than guessing at it.
* Memory recall is bounded and only relevant episodes for the symbol enter
  context — ATI never dumps its whole history into a prompt.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
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
    DEFAULT_RECALL_LIMIT as _DEFAULT_RECALL_LIMIT,
)
from backend.infrastructure.ai.prompt_builder import (
    build_payload as _build_deterministic_payload,
)

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = "ai_unavailable"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class OmniRouteConfig:
    """Connection and reasoning bounds for the OmniRoute gateway.

    Attributes
    ----------
    base_url: str
        Router base (``/chat/completions`` is appended).
    model: str | None
        Optional explicit model; ``None`` lets the router pick/load-balance.
    timeout_seconds: float
        Per-request timeout.
    temperature: float
        Sampling temperature (low for more disciplined output).
    max_tokens: int
        Response token cap.
    recall_limit: int
        Max episodic memory episodes to include in context per symbol.
    """

    base_url: str = "http://localhost:20128/v1"
    model: str | None = None
    timeout_seconds: float = 30.0
    temperature: float = 0.2
    max_tokens: int = 600
    recall_limit: int = _DEFAULT_RECALL_LIMIT


class AiOmniRouteReasoner(AIReasoner):
    """Turn a MarketContext plus episodic memory into a DecisionProposal."""

    def __init__(
        self,
        config: OmniRouteConfig | None = None,
        *,
        memory_store: MemoryStore | None = None,
        client: httpx.Client | None = None,
        clock: Any | None = None,
    ) -> None:
        self._config = config or OmniRouteConfig()
        self._memory_store = memory_store
        self._client = client or httpx.Client(timeout=httpx.Timeout(self._config.timeout_seconds))
        self._clock = clock or _utcnow

        # Degradation observability (ADR 0005).
        self._failures: int = 0
        self._last_failure_reason: str | None = None
        self._last_failure_at: datetime | None = None
        self._last_failure_duration_ms: float | None = None

    @property
    def failure_count(self) -> int:
        """Number of degraded (failed) reasoning attempts."""
        return self._failures

    @property
    def last_failure_reason(self) -> str | None:
        """Reason for the most recent failure, if any."""
        return self._last_failure_reason

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        """Produce a proposal, degrading to STAND_ASIDE on any failure."""
        started = time.perf_counter()
        try:
            payload = self._build_payload(context, risk_context)
            response = self._client.post(self._endpoint(), json=payload)
            response.raise_for_status()
            proposal = self._parse(response.json(), context, risk_context)
            duration = (time.perf_counter() - started) * 1000.0
            logger.info(
                "OmniRoute reason ok symbol=%s proposal=%s latency_ms=%.1f",
                context.snapshot.symbol,
                proposal.proposal_id,
                duration,
            )
            return proposal
        except Exception as exc:  # noqa: BLE001 - every failure degrades safely
            duration = (time.perf_counter() - started) * 1000.0
            self._record_failure(exc, duration)
            return self._stand_aside(context, risk_context, reason=str(exc))

    def close(self) -> None:
        """Release the underlying HTTP client."""
        self._client.close()

    # -- request construction ------------------------------------------------

    def _endpoint(self) -> str:
        return f"{self._config.base_url.rstrip('/')}/chat/completions"

    def _build_payload(self, context: MarketContext, risk_context: RiskContext) -> dict[str, Any]:
        return _build_deterministic_payload(
            context,
            risk_context,
            memory_store=self._memory_store,
            recall_limit=self._config.recall_limit,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            model=self._config.model,
        )

    def _recall_for_prompt(self, symbol: str) -> list[dict[str, Any]]:
        """Deprecated shim — use ``prompt_builder._recall_for_prompt`` directly."""
        import warnings

        warnings.warn(
            "AiOmniRouteReasoner._recall_for_prompt is deprecated; "
            "use prompt_builder._recall_for_prompt",
            DeprecationWarning,
            stacklevel=2,
        )
        from backend.infrastructure.ai.prompt_builder import _recall_for_prompt as _shared_recall

        return _shared_recall(self._memory_store, symbol, self._config.recall_limit)

    # -- response handling -----------------------------------------------------

    def _parse(
        self,
        data: Any,
        context: MarketContext,
        risk_context: RiskContext,
    ) -> DecisionProposal:
        content = _extract_content(data)
        parsed = _parse_json(content)
        return self._proposal_from_dict(parsed, context, risk_context)

    def _proposal_from_dict(
        self,
        parsed: dict[str, Any],
        context: MarketContext,
        risk_context: RiskContext,
    ) -> DecisionProposal:
        action_type = ProposedActionType(str(parsed["action_type"]))
        size_fraction = float(parsed.get("size_fraction", 0.10))
        confidence = float(parsed["confidence"])
        if not 0.0 < confidence <= 1.0:
            raise ValueError("LLM returned confidence outside (0, 1]")
        alternatives = _alternatives(parsed.get("alternatives"))

        symbol = context.snapshot.symbol
        created_at = self._clock()
        action = ProposedAction(
            action_type=action_type,
            size_fraction=size_fraction,
            order=1,
            rationale=str(parsed.get("rationale", "")),
        )
        pre_trade_plan = self._plan_from_dict(parsed, context)
        proposal = DecisionProposal(
            proposal_id=f"prop-{symbol}-{created_at.isoformat(timespec='milliseconds')}",
            correlation_id=symbol,
            created_at=created_at,
            symbol=symbol,
            hypothesis=Hypothesis(
                statement=str(parsed.get("hypothesis_statement", "")),
                supporting_evidence=_evidence(context),
                opposing_evidence=(),
            ),
            confidence=confidence,
            uncertainty=str(parsed.get("uncertainty", "")),
            actions=(action,),
            risk_context=risk_context,
            alternatives=alternatives,
            rationale=str(parsed.get("rationale", "")),
            pre_trade_plan=pre_trade_plan,
            post_trade_plan=PostTradePlan() if pre_trade_plan is not None else None,
        )
        return proposal

    def _plan_from_dict(
        self, parsed: dict[str, Any], context: MarketContext
    ) -> PreTradePlan | None:
        """Bracket plan from the model, or a deterministic fallback.

        The model may supply a ``pre_trade_plan`` mapping; when it does not, a
        volatility-anchored 1:2 bracket is generated so the protective-plan
        invariant holds even for LLM proposals (the gate still vetoes anything
        malformed).
        """
        if _raises_risk(parsed):
            raw = parsed.get("pre_trade_plan")
            if isinstance(raw, dict):
                return PreTradePlan.from_dict(raw)
            return self._fallback_plan(context)
        return None

    @staticmethod
    def _fallback_plan(context: MarketContext) -> PreTradePlan:
        std_dev: float | None = None
        try:
            value = context.feature("volatility").value
            raw = value.get("std_dev") if isinstance(value, dict) else None
            if isinstance(raw, (int, float)):
                std_dev = float(raw)
        except KeyError:
            std_dev = None
        return bracket_plan(stop_distance_from_volatility(std_dev))

    # -- safe degradation (ADR 0005) --------------------------------------------

    def _record_failure(self, exc: Exception, duration_ms: float) -> None:
        self._failures += 1
        self._last_failure_reason = str(exc)
        self._last_failure_at = self._clock()
        self._last_failure_duration_ms = duration_ms
        logger.warning(
            "%s: OmniRoute reasoning failed: %s (latency_ms=%.1f, failures=%d)",
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


def _extract_content(data: Any) -> str:
    """Pull ``.choices[0].message.content`` from an OpenAI-compatible reply."""
    try:
        message = data["choices"][0]["message"]
        content = message["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # Some providers return structured content parts.
            return json.dumps(content)
        return str(content)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected response shape: {exc}") from exc


def _parse_json(content: str) -> dict[str, Any]:
    """Parse the model's content into a JSON object, tolerating fences."""
    text = content.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        if not text.startswith("```"):
            raise
        body = text.split("```", 2)[1].lstrip("json").strip()
        parsed = json.loads(body)
    if not isinstance(parsed, dict):
        raise ValueError("LLM reply is not a JSON object")
    return parsed


def _alternatives(raw: Any) -> tuple[AlternativeConsidered, ...]:
    if not isinstance(raw, list):
        return ()
    out: list[AlternativeConsidered] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        out.append(
            AlternativeConsidered(
                description=str(item.get("description", "")),
                reason_rejected=str(item.get("reason_rejected", "")),
            )
        )
    return tuple(out)


_RISK_INCREASING = frozenset(
    {
        ProposedActionType.ENTER_LONG,
        ProposedActionType.ENTER_SHORT,
        ProposedActionType.SCALE_IN,
    }
)


def _raises_risk(parsed: dict[str, Any]) -> bool:
    try:
        return ProposedActionType(str(parsed["action_type"])) in _RISK_INCREASING
    except (KeyError, ValueError):
        return False


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
