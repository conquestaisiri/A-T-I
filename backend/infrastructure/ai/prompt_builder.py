# backend/infrastructure/ai/prompt_builder.py
"""Deterministic prompt construction — the Omega continuity invariant.

The input to *any* model is a pure, versioned function of durable state
(``MarketContext`` + ``RiskContext`` + bounded ``MemoryStore`` recall).
This module is the single source of truth for that function. Both
``AiOmniRouteReasoner`` and ``SmartFallbackReasoner`` import it so a
provider switch never changes what the AI knows — only how fast it answers.

Continuity rule (``docs/ATI_OmniRoute_Context_Continuity.md`` R2): the prompt
must never depend on conversational window, model name, or provider id.
Version the system prompt when the persona changes and keep the user payload
shape stable so the determinism test stays green.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

SYSTEM_PROMPT: str = (
    "You are the reasoning component of an autonomous trading "
    "intelligence. You consume a market context, a risk snapshot, "
    "and recent episodic market memory. You emit ONE candidate "
    "decision as strict JSON with the exact schema requested. "
    "You are disciplined and risk-aware: when evidence is weak or "
    "risk is elevated you stand aside. You never emit orders — "
    "only proposals, which a downstream risk gate may veto."
)

# Increment when persona changes — determinism test pins this.
PROMPT_VERSION = "v1"
DEFAULT_RECALL_LIMIT = 6


def build_messages(
    context: Any,
    risk_context: Any,
    *,
    memory_store: Any | None = None,
    recall_limit: int = DEFAULT_RECALL_LIMIT,
) -> list[dict[str, str]]:
    """Return the OpenAI-compatible ``messages`` array (pure, deterministic)."""
    episodic = _recall_for_prompt(memory_store, context.snapshot.symbol, recall_limit)
    user_payload = {
        "task": "produce_decision_proposal",
        "symbol": context.snapshot.symbol,
        "features": {k: v.as_dict() for k, v in context.features},
        "risk": risk_context.as_dict(),
        "episodic_memory": episodic,
        "output_schema": {
            "confidence": "float 0..1",
            "uncertainty": "str",
            "hypothesis_statement": "str",
            "action_type": (
                "one of enter_long|enter_short|stand_aside|exit|scale_in|scale_out|reduce_risk"
            ),
            "size_fraction": "float in (0,1]",
            "rationale": "str",
            "alternatives": [{"description": "str", "reason_rejected": "str"}],
        },
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(user_payload, default=_canonical_json_default, sort_keys=True),
        },
    ]


def _canonical_json_default(obj: Any) -> str:
    if isinstance(obj, _dt.datetime):
        return obj.isoformat()
    if isinstance(obj, _dt.date):
        return obj.isoformat()
    raise TypeError(f"Non-JSON-serializable type {type(obj).__name__}: {obj!r}")


def build_payload(
    context: Any,
    risk_context: Any,
    *,
    memory_store: Any | None = None,
    recall_limit: int = DEFAULT_RECALL_LIMIT,
    temperature: float = 0.2,
    max_tokens: int = 600,
    model: str | None = None,
) -> dict[str, Any]:
    """Build the full ``/chat/completions`` JSON body (still pure)."""
    payload: dict[str, Any] = {
        "messages": build_messages(
            context, risk_context, memory_store=memory_store, recall_limit=recall_limit
        ),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if model:
        payload["model"] = model
    return payload


def _recall_for_prompt(memory_store: Any | None, symbol: str, limit: int) -> list[dict[str, Any]]:
    if memory_store is None:
        return []
    try:
        episodes = memory_store.recall(symbol, limit=limit)
    except Exception:  # noqa: BLE001 - memory must never break reasoning
        logger.exception("Memory recall failed; proceeding without episodic memory")
        return []
    return [
        {
            "outcome": ep.outcome.value,
            "action_type": ep.action_type,
            "confidence": ep.confidence,
            "realized_pnl": ep.realized_pnl,
            "created_at": ep.created_at.isoformat(timespec="seconds"),
            "summary": ep.summary,
        }
        for ep in episodes
    ]
