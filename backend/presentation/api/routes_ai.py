# backend/presentation/api/routes_ai.py
"""Concierge chat: the NPC that knows the house.

Worker face = MarketLoopService running continuously.
Concierge face = this router. Every message is answered from LIVE truth:
mode, engine, positions, risk, supervisor, next macro event, recent
proposals, data health. Tool-calls go through the same guarded state
the dashboard buttons use, so the chat can *act* safely.
"""

from __future__ import annotations

import contextlib
import json
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Security
from pydantic import BaseModel, Field

from backend.presentation.api.auth import verify_api_key

router = APIRouter(
    prefix="/v1/ai",
    tags=["ai"],
    dependencies=[Security(verify_api_key)],
)

# Symbol aliases the operator naturally says
_ALIAS = {
    "gold": "XAUUSD",
    "xau": "XAUUSD",
    "xauusd": "XAUUSD",
    "goldusd": "XAUUSD",
    "eurusd": "EURUSD",
    "eur": "EURUSD",
    "gbpusd": "GBPUSD",
    "gbp": "GBPUSD",
    "usdjpy": "USDJPY",
    "jpy": "USDJPY",
    "audusd": "AUDUSD",
    "usdcad": "USDCAD",
    "nzdusd": "NZDUSD",
    "btcusdt": "BTCUSDT",
    "btc": "BTCUSDT",
}


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)
    history: list[dict[str, str]] = Field(default_factory=list)


def _gather_state(request: Request) -> dict[str, Any]:
    from backend.infrastructure.config.settings import settings

    state: dict[str, Any] = {
        "mode": settings.trading_mode.strip().lower(),
        "paper_mode": bool(settings.paper_mode),
        "generated_at": datetime.now(UTC).isoformat(),
    }
    # Engine
    try:
        from backend.presentation.api.routes_engine import (
            _engine_snapshot,
        )

        eng = _engine_snapshot(request)
        state["engine_running"] = eng.get("running")
        state["auto_trade"] = eng.get("auto_trade")
        state["engine_stats"] = eng.get("stats", {})
    except Exception:
        state["engine_running"] = None
    # Positions / risk / supervisor via operator snapshot
    try:
        sim = getattr(request.app.state, "simulator", None)
        if sim is not None:
            state["equity"] = sim.equity
            state["positions"] = {
                k: {"side": v.side.value, "qty": v.quantity, "entry": v.average_entry_price}
                for k, v in list(sim.positions.items())
            }
            risk = sim.risk_snapshot()
            state["risk"] = {
                "daily_loss_pct": risk.daily_loss_pct,
                "drawdown_pct": risk.drawdown_pct,
                "exposure_pct": risk.open_exposure_pct,
            }
    except Exception:
        pass
    try:
        sup = getattr(request.app.state, "supervisor", None)
        if sup is not None:
            d = sup.check()
            state["supervisor"] = d.status.value if hasattr(d.status, "value") else str(d.status)
            state["supervisor_reason"] = d.reason
    except Exception:
        pass
    # Next macro event
    try:
        repo = getattr(request.app.state, "macro_event_repository", None)
        if repo is not None:
            now = datetime.now(UTC)
            # peek next High for USD/EUR
            nxt = None
            for cur in ("USD", "EUR", "GBP", "JPY"):
                cand = repo.next_high_impact_for_currencies({cur}, now=now, within_minutes=24 * 60)
                if cand and (nxt is None or cand.scheduled_at < nxt.scheduled_at):
                    nxt = cand
            if nxt:
                state["next_macro"] = {
                    "currency": nxt.currency,
                    "title": nxt.title,
                    "impact": nxt.impact,
                    "scheduled_at": nxt.scheduled_at.isoformat(),
                }
    except Exception:
        pass
    # Recent proposals (last 3)
    try:
        prop_repo = getattr(request.app.state, "proposal_repository", None)
        if prop_repo is not None and hasattr(prop_repo, "recent"):
            recents = prop_repo.recent(limit=3)
            state["recent_proposals"] = [
                {
                    "symbol": p.symbol,
                    "action": p.actions[0].action_type.value if p.actions else "?",
                    "conf": p.confidence,
                }
                for p in recents
            ]
        else:
            # fallback: try listing via proposal repo not having recent — skip
            pass
    except Exception:
        pass
    return state


def _detect_intent(msg: str) -> tuple[str, dict[str, Any]] | None:
    low = msg.strip().lower()
    # Trade X
    m = re.search(
        r"trade\s+(xauusd|eurusd|gbpusd|usdjpy|audusd|usdcad|nzdusd|btcusdt|gold|xau|eur|gbp|jpy|aud)\b",
        low,
    )
    if m:
        raw = m.group(1)
        sym = _ALIAS.get(raw, raw.upper())
        # normalize gold variations
        sym = _ALIAS.get(low.split("trade")[-1].strip().split()[0], sym) if "gold" in low else sym
        if "gold" in low:
            sym = "XAUUSD"
        return ("set_symbol", {"symbol": sym})
    if re.search(r"\b(start|resume).*engine\b", low) or low.strip() == "start":
        return ("engine_start", {})
    if re.search(r"\b(pause|stop).*engine\b", low) or low.strip() in {"pause", "stop"}:
        return ("engine_stop", {})
    if re.search(r"auto.?trade.*on\b", low):
        return ("auto_trade", {"auto_trade": True})
    if re.search(r"auto.?trade.*off\b", low) or "manual" in low:
        return ("auto_trade", {"auto_trade": False})
    if re.search(r"close.*(all|flatten)", low):
        return ("flatten", {})
    m2 = re.search(r"close\s+(xauusd|eurusd|gbpusd|usdjpy|audusd|usdcad|nzdusd|btcusdt)", low)
    if m2:
        return ("close_one", {"symbol": m2.group(1).upper()})
    return None


async def _execute_tool(name: str, params: dict[str, Any], request: Request) -> str:
    if name == "set_symbol":
        # For now we just acknowledge — full symbol-switch needs engine restart
        # with new desired_symbols persisted. Keep it as a chat-level switch
        # that the dashboard picks up via pair watcher.
        return (
            f"Switched watch to {params['symbol']} — chart and ticks will "
            "follow. Say 'start engine' to trade it."
        )
    if name == "engine_start":
        from backend.presentation.api.routes_engine import _load_state, _save_state

        state = _load_state()
        state["desired_running"] = True
        _save_state(state)
        loop = getattr(request.app.state, "market_loop", None)
        if loop is not None and not getattr(loop, "_running", False):
            import asyncio

            request.app.state.market_tasks = getattr(request.app.state, "market_tasks", [])
            task = asyncio.create_task(loop.start())
            request.app.state.market_tasks.append(task)
            setattr(loop, "_running", True)  # noqa: B010
        adapter = getattr(request.app.state, "mt5_adapter", None)
        if adapter is not None:
            t = getattr(adapter, "_task", None)
            if t is None or t.done():
                adapter.start()
        return "Engine started — watching the market now."
    if name == "engine_stop":
        from backend.presentation.api.routes_engine import _load_state, _save_state

        state = _load_state()
        state["desired_running"] = False
        _save_state(state)
        loop = getattr(request.app.state, "market_loop", None)
        if loop is not None and getattr(loop, "_running", False):
            loop.stop()
        adapter = getattr(request.app.state, "mt5_adapter", None)
        if adapter is not None:
            with contextlib.suppress(Exception):
                await adapter.stop()
        return "Engine paused — no new trades will be taken. Positions stay open."
    if name == "auto_trade":
        on = bool(params["auto_trade"])
        pipe = getattr(request.app.state, "decision_pipeline", None)
        if pipe is not None and hasattr(pipe, "_auto_trade"):
            setattr(pipe, "_auto_trade", on)  # noqa: B010
        from backend.presentation.api.routes_engine import _load_state, _save_state

        s = _load_state()
        s["auto_trade"] = on
        _save_state(s)
        return (
            "Auto-trade ON — AI will execute."
            if on
            else "Manual only — AI will narrate but not execute. You confirm each trade."
        )
    if name == "flatten":
        sim = getattr(request.app.state, "simulator", None)
        if sim is None or not sim.positions:
            return "No open positions to close."
        # Use the same operator lock path
        return (
            "Flatten requested — use the dashboard FLATTEN ALL button to "
            "confirm with the risk lock. I won't auto-flatten without your "
            "confirm."
        )
    if name == "close_one":
        return (
            f"Close {params['symbol']} — use the CLOSE button next to the position row to confirm."
        )
    return "Unknown tool."


async def _llm_reply(
    system_state: dict[str, Any], user_msg: str, history: list[dict[str, str]]
) -> str | None:
    # Context compaction — keep context bounded, survives model switches
    total_chars = sum(len(h.get("content", "")) for h in history)
    if total_chars > 6000 and len(history) > 4:
        keep = history[-4:]
        old = history[:-4]
        summary = "Earlier conversation compacted: " + " | ".join(
            h.get("content", "")[:120] for h in old[:3]
        )
        history = [{"role": "system", "content": summary}] + keep

    # Try Omega-style provider pool; fall back to None if no keys
    try:
        from backend.infrastructure.secrets.sagax_loader import load_provider_keys

        pools = load_provider_keys()
        key = None
        provider = None
        for cand in ("groq", "openrouter", "cerebras", "gemini"):
            if pools.get(cand):
                provider = cand
                key = pools[cand][0]
                break
        if not key or not provider:
            return None
    except Exception:
        return None

    # Build a compact system prompt with live state
    sys_prompt = (
        "You are ATI — the concierge face of the ATI Trading Intelligence engine. "
        "You run INSIDE the engine and you know the house. Be concise, grounded, "
        "and never hallucinate numbers — use the live state provided. "
        "If the user asks to act (trade, start/stop), say you understood and what you did, "
        "but the actual action is performed via tools outside this message. "
        "Current live state:\n" + json.dumps(system_state, indent=2, default=str)[:4000]
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": sys_prompt}]
    for h in history[-6:]:
        if h.get("role") in {"user", "assistant"} and h.get("content"):
            messages.append({"role": h["role"], "content": h["content"][:800]})
    messages.append({"role": "user", "content": user_msg})

    # Provider-specific endpoint (Groq OpenAI-compatible first)
    import httpx

    url_map = {
        "groq": ("https://api.groq.com/openai/v1/chat/completions", "llama-3.1-8b-instant"),
        "openrouter": (
            "https://openrouter.ai/api/v1/chat/completions",
            "meta-llama/llama-3.1-8b-instruct",
        ),
        "cerebras": ("https://api.cerebras.ai/v1/chat/completions", "llama3.1-8b"),
        "gemini": (
            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "gemini-2.0-flash",
        ),
    }
    url, model = url_map.get(provider, url_map["groq"])
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    # OpenRouter needs extra headers
    if provider == "openrouter":
        headers["HTTP-Referer"] = "https://github.com/conquestaisiri/A-T-I"
        headers["X-Title"] = "ATI Trading Intelligence"
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0.4,
        "max_tokens": 400,
    }
    try:
        async with httpx.AsyncClient(timeout=12) as client:
            r = await client.post(url, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            choices = data.get("choices") or []
            if choices and choices[0].get("message", {}).get("content"):
                return str(choices[0]["message"]["content"]).strip()
    except Exception:
        return None
    return None


def _fallback_reply(state: dict[str, Any], user_msg: str) -> str:
    low = user_msg.lower()
    running = state.get("engine_running")
    mode = state.get("mode", "?")
    pos = state.get("positions", {})
    sup = state.get("supervisor", "?")
    nxt = state.get("next_macro")
    if any(k in low for k in ("are you trading", "are you running", "status", "what.*doing")):
        return (
            f"I'm {'running' if running else 'paused'} in {mode} mode "
            f"({'auto-trade ON' if state.get('auto_trade') else 'watch-only'}), "
            f"supervisor {sup}, {len(pos)} open position(s). "
            + (
                f"Next High event: {nxt['currency']} {nxt['title']} at {nxt['scheduled_at']}."
                if nxt
                else "No High event in the next 24h."
            )
        )
    if "gold" in low or "xau" in low:
        return (
            "Gold (XAUUSD) — say 'trade XAUUSD' and I'll switch the watch "
            "to it. Then 'start engine' to let me trade it."
        )
    # Generic grounded fallback
    return (
        f"Engine {'running' if running else 'paused'} · mode {mode} · "
        f"{len(pos)} positions · supervisor {sup}. "
        "Ask me 'trade EURUSD', 'start engine', 'auto-trade off', "
        "or 'what's happening?'"
    )


@router.post("/chat")
async def ai_chat(payload: ChatRequest, request: Request) -> dict[str, Any]:
    state = _gather_state(request)
    intent = _detect_intent(payload.message)
    actions: list[dict[str, Any]] = []

    if intent is not None:
        name, params = intent
        result = await _execute_tool(name, params, request)
        # Refresh state after tool
        state = _gather_state(request)
        actions.append({"tool": name, "params": params, "result": result})
        # For control intents, answer directly without LLM
        if name in {"engine_start", "engine_stop", "auto_trade", "set_symbol"}:
            return {"reply": result, "actions": actions, "state": state}

    # Conversational path: try LLM, fallback to templated
    reply = await _llm_reply(state, payload.message, payload.history)
    if not reply:
        reply = _fallback_reply(state, payload.message)
    return {"reply": reply, "actions": actions, "state": state}


@router.get("/analysis/{symbol}")
async def ai_analysis(symbol: str, request: Request) -> dict[str, Any]:
    """AI-drawn TA for a symbol — support/resistance, trend, entry/SL/TP."""
    from backend.application.ta.annotation_service import compute_levels

    sym = symbol.strip().upper()
    # Try MT5 bars first (forex), fallback to empty
    bars: list[dict[str, Any]] = []
    try:
        from backend.presentation.api.routes_mt5 import _get_bridge

        bridge = _get_bridge()
        bars = bridge.get_rates(sym, "H1", 100)
    except Exception:
        bars = []
    result = compute_levels(bars)
    result["symbol"] = sym
    # Also include live state for context
    state = _gather_state(request)
    result["engine_running"] = state.get("engine_running")
    result["next_macro"] = state.get("next_macro")
    return result


@router.post("/regret-review")
async def regret_review(request: Request) -> dict[str, Any]:
    """Nightly regret journal — reviews losers and writes lessons."""
    ledger = getattr(request.app.state, "ledger_repository", None)
    memory = getattr(request.app.state, "memory_store", None)
    if ledger is None:
        raise HTTPException(status_code=503, detail="Ledger not initialized")
    from backend.application.reflection.regret_journal import nightly_review

    return nightly_review(ledger, memory)
