# backend/infrastructure/ai/smart_fallback_reasoner.py
"""Omega / God-like multi-provider fallback reasoner — speed of light.

This is the backup-to-the-backup the operator asked for: Zen (anonymous +
authenticated) -> Groq (key pool) -> OpenRouter (key pool) -> Cerebras /
Gemini / AgentRouter (extensible), same deterministic prompt for every
provider, instant key rotation on 429/401, circuit-breaker, hedged parallel
race, and zero-downtime degradation to ``STAND_ASIDE`` only when *all*
providers are exhausted.

Continuity invariant (``ATI_OmniRoute_Context_Continuity.md``): the prompt is
a pure function of durable state via ``prompt_builder`` — every provider
receives byte-identical knowledge. Only ``base_url``/``model``/auth changes.

Design goals (operator directive: GOD MODE):
* No single delay: fallback is instant (round-robin key, next provider without
  waiting for full timeout when the error is fast like 429).
* No single downtime: hedged/parallel race takes the fastest success.
* No secret in repo: keys loaded at runtime via ``sagax_loader.load_provider_keys``.
* Observable: per-provider failure/latency, global ``failure_count`` like
  ``AiOmniRouteReasoner``.

Research-only? No — this *is* the live reasoner when wired in
``backend/main.py``. It never touches orders; the risk gate still vetoes.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from dataclasses import dataclass, field
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
from backend.infrastructure.ai.prompt_builder import build_payload
from backend.infrastructure.secrets.sagax_loader import load_provider_keys, redact_key

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = "ai_unavailable"


def _utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Provider specs
# ---------------------------------------------------------------------------

_PROVIDER_DEFAULTS: dict[str, dict[str, Any]] = {
    "zen": {
        "base_url": "https://opencode.ai/zen/v1",
        "model": None,  # router picks
        "timeout": 12.0,
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "timeout": 10.0,
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "model": "openai/gpt-4o-mini",
        "timeout": 15.0,
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "model": "llama3.1-8b",
        "timeout": 10.0,
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-2.0-flash",
        "timeout": 12.0,
    },
    "agentrouter": {
        "base_url": "https://agentrouter.ai/v1",
        "model": None,
        "timeout": 12.0,
    },
    # Keyless free providers (verified 2026-08: tool-calling + streaming work;
    # LLM7 500K tok/day, Kilo fair-use, OVH 2 req/min per IP/model)
    "llm7": {
        "base_url": "https://api.llm7.io/v1",
        "model": "DeepSeek-V4-Flash-0731",
        "timeout": 15.0,
    },
    "kilo": {
        "base_url": "https://api.kilo.ai/api/gateway/v1",
        "model": "tencent/hy3:free",
        "timeout": 90.0,
        # hy3 emits 1600+ chars of reasoning BEFORE the JSON on production
        # prompts; 2000 still truncated content mid-JSON on live runs.
        # 4000 covers worst-case reasoning + strict-JSON object.
        "max_tokens": 4000,
    },
    "ovh": {
        "base_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
        "model": "Qwen3-Coder-30B-A3B-Instruct",
        "timeout": 20.0,
    },
}

# Priority order for God-mode: Zen first (free, same cloud), then fastest inference.
# llm7/kilo/ovh are verified keyless free providers (2026-08 provider audit) —
# they sit at the tail so keyed providers win when healthy, but the chain never
# dead-ends into STAND_ASIDE just because every key expired.
# Priority order for God-mode. Groq/OpenRouter/etc revive automatically if
# keys ever appear (sagax/env); zen dropped 2026-08-23 - permanent 401 without
# a paid key made it one wasted round-trip per decision.
# Keyless tail, benchmark-ranked 2026-08-23 against the production prompt:
# ovh best quality-per-success; kilo full JSON after max_tokens fix; llm7
# systematically answers 'hold' on trading prompts -> last resort.
_DEFAULT_PRIORITY: list[str] = [
    "groq",
    "openrouter",
    "cerebras",
    "gemini",
    "agentrouter",
    "ovh",
    "kilo",
    "llm7",
]

# Providers that accept anonymous (no-key) requests: a spec with zero keys
# is valid and participates in the fallback chain.
_KEYLESS_PROVIDERS: frozenset[str] = frozenset({"llm7", "kilo", "ovh"})


@dataclass(frozen=True, slots=True)
class OmegaConfig:
    """Tuning for the Omega router.

    All timeouts are per-provider, per-key. Hedged race starts the next
    provider after ``hedged_delay_ms`` even if the prior hasn't failed yet —
    the fastest success wins, cancelling the rest.
    """

    temperature: float = 0.2
    max_tokens: int = 600
    recall_limit: int = _DEFAULT_RECALL_LIMIT
    timeout_seconds: float = 12.0  # default if provider has no override
    race_mode: str = "sequential"  # "sequential" | "parallel" | "hedged"
    hedged_delay_ms: float = 250.0
    adaptive_hedge: bool = True  # when True, hedged delay = 0.35*latency_ema (clamped 150-800ms)
    circuit_threshold: int = 5  # consecutive failures before open
    circuit_cooldown_seconds: float = 60.0
    priority: tuple[str, ...] = tuple(_DEFAULT_PRIORITY)
    sagax_path: str | None = None


@dataclass
class _ProviderHealth:
    consecutive_failures: int = 0
    success_count: int = 0
    failure_count: int = 0
    latency_ema_ms: float | None = None
    circuit_open_until: float | None = None
    last_error: str | None = None


@dataclass
class _ProviderSpec:
    provider: str
    base_url: str
    model: str | None
    timeout: float
    max_tokens: int | None = None
    keys: list[str] = field(default_factory=list)
    key_index: int = 0
    health: _ProviderHealth = field(default_factory=_ProviderHealth)


# ---------------------------------------------------------------------------
# Key-pool + health helpers
# ---------------------------------------------------------------------------


def _next_key(spec: _ProviderSpec) -> str | None:
    if not spec.keys:
        return None  # anonymous (Zen)
    key = spec.keys[spec.key_index % len(spec.keys)]
    spec.key_index = (spec.key_index + 1) % len(spec.keys)
    return key


def _record_success(spec: _ProviderSpec, latency_ms: float) -> None:
    h = spec.health
    h.consecutive_failures = 0
    h.success_count += 1
    h.circuit_open_until = None
    # EMA alpha 0.3
    if h.latency_ema_ms is None:
        h.latency_ema_ms = latency_ms
    else:
        h.latency_ema_ms = 0.3 * latency_ms + 0.7 * h.latency_ema_ms


def _record_failure(spec: _ProviderSpec, error: str, config: OmegaConfig | None = None) -> None:
    h = spec.health
    h.consecutive_failures += 1
    h.failure_count += 1
    h.last_error = error
    threshold = config.circuit_threshold if config is not None else 5
    if h.consecutive_failures >= threshold:
        h.circuit_open_until = time.monotonic() + (
            config.circuit_cooldown_seconds if config else 60.0
        )


def _circuit_open(spec: _ProviderSpec, config: OmegaConfig) -> bool:
    h = spec.health
    if h.circuit_open_until is None:
        return False
    if time.monotonic() >= h.circuit_open_until:
        h.circuit_open_until = None
        h.consecutive_failures = 0
        return False
    # Threshold check — only open if failures >= threshold
    return h.consecutive_failures >= config.circuit_threshold


def _ranked_providers(specs: dict[str, _ProviderSpec], config: OmegaConfig) -> list[_ProviderSpec]:
    # Priority first, then healthy before open-circuit, then lower latency
    def score(s: _ProviderSpec) -> tuple[int, int, float]:
        try:
            pri = config.priority.index(s.provider)
        except ValueError:
            pri = 999
        is_open = 1 if _circuit_open(s, config) else 0
        latency = s.health.latency_ema_ms if s.health.latency_ema_ms is not None else 9_999.0
        return (is_open, pri, latency)

    return sorted(specs.values(), key=score)


# ---------------------------------------------------------------------------
# The God reasoner
# ---------------------------------------------------------------------------


class SmartFallbackReasoner(AIReasoner):
    """Zero-downtime, multi-provider ``AIReasoner`` with instant key rotation.

    Parameters
    ----------
    config:
        Omega tuning.
    memory_store:
        Episodic memory recall (same contract as ``AiOmniRouteReasoner``).
    provider_keys:
        Injected ``{provider: [key,...]}`` for tests. When ``None`` the loader
        reads ``SAGAX_KEYS_PATH`` / ``DEFAULT_SAGAX_PATH`` + env at construction.
    clients:
        Injected ``{provider: httpx.Client}`` for tests (MockTransport). When
        ``None`` a pooled ``httpx.Client`` per provider is created.
    clock:
        Override for ``created_at``.
    """

    def __init__(
        self,
        config: OmegaConfig | None = None,
        *,
        memory_store: MemoryStore | None = None,
        provider_keys: dict[str, list[str]] | None = None,
        clients: dict[str, httpx.Client] | None = None,
        clock: Any | None = None,
    ) -> None:
        self._config = config or OmegaConfig()
        self._memory_store = memory_store
        self._clock = clock or _utcnow
        self._provider_keys = (
            provider_keys
            if provider_keys is not None
            else load_provider_keys(self._config.sagax_path)
        )
        self._specs: dict[str, _ProviderSpec] = {}
        for provider in self._config.priority:
            defaults = _PROVIDER_DEFAULTS.get(provider, {})
            base_url = defaults.get("base_url", f"https://{provider}/v1")
            model = defaults.get("model")
            timeout = float(defaults.get("timeout", self._config.timeout_seconds))
            keys = list(self._provider_keys.get(provider, []))
            # Keyless providers (Zen anonymous, llm7, kilo, ovh) are valid
            # with 0 keys - keep them in the chain.
            if provider not in _KEYLESS_PROVIDERS and not keys:
                continue
            spec_max_tokens = defaults.get("max_tokens")
            self._specs[provider] = _ProviderSpec(
                provider=provider,
                base_url=base_url,
                model=model,
                timeout=timeout,
                max_tokens=int(spec_max_tokens) if spec_max_tokens else None,
                keys=keys,
            )
        # If nothing resolved (no keys anywhere), the keyless free tail
        # stands: guarantee at least one spec so the chain is never empty.
        if not self._specs:
            self._specs["ovh"] = _ProviderSpec(
                provider="ovh",
                base_url=_PROVIDER_DEFAULTS["ovh"]["base_url"],
                model=_PROVIDER_DEFAULTS["ovh"]["model"],
                timeout=_PROVIDER_DEFAULTS["ovh"]["timeout"],
            )
            logger.warning(
                "No provider keys found - Omega running on the free keyless pool (ovh/kilo/llm7)"
            )

        self._clients: dict[str, httpx.Client] = {}
        for provider, spec in self._specs.items():
            if clients and provider in clients:
                self._clients[provider] = clients[provider]
            else:
                self._clients[provider] = httpx.Client(
                    timeout=httpx.Timeout(spec.timeout),
                    headers={"Content-Type": "application/json"},
                )
        self._lock = threading.Lock()
        self._failures = 0
        self._last_failure_reason: str | None = None
        self._last_failure_at: datetime | None = None
        self._last_failure_duration_ms: float | None = None
        self._last_success_provider: str | None = None

    # -- AIReasoner port -----------------------------------------------------

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        started = time.perf_counter()
        payload_template = build_payload(
            context,
            risk_context,
            memory_store=self._memory_store,
            recall_limit=self._config.recall_limit,
            temperature=self._config.temperature,
            max_tokens=self._config.max_tokens,
            model=None,  # injected per-provider below
        )
        # Strip model if template injected None — each provider injects its own
        payload_template.pop("model", None)

        if self._config.race_mode == "parallel":
            proposal = self._reason_parallel(payload_template, context, risk_context, started)
            if proposal is not None:
                return proposal
        elif self._config.race_mode == "hedged":
            proposal = self._reason_hedged(payload_template, context, risk_context, started)
            if proposal is not None:
                return proposal
        else:
            proposal = self._reason_sequential(payload_template, context, risk_context, started)
            if proposal is not None:
                return proposal

        duration = (time.perf_counter() - started) * 1000.0
        reason = self._last_failure_reason or "all providers exhausted"
        self._record_global_failure(reason, duration)
        return self._stand_aside(context, risk_context, reason=reason)

    def close(self) -> None:
        import contextlib

        for client in self._clients.values():
            with contextlib.suppress(Exception):
                client.close()

    @property
    def failure_count(self) -> int:
        return self._failures

    @property
    def last_failure_reason(self) -> str | None:
        return self._last_failure_reason

    @property
    def last_success_provider(self) -> str | None:
        return self._last_success_provider

    def provider_stats(self) -> dict[str, dict[str, Any]]:
        return {
            name: {
                "success": spec.health.success_count,
                "failures": spec.health.failure_count,
                "consecutive_failures": spec.health.consecutive_failures,
                "latency_ema_ms": spec.health.latency_ema_ms,
                "circuit_open": _circuit_open(spec, self._config),
                "keys": len(spec.keys),
            }
            for name, spec in self._specs.items()
        }

    def _current_hedged_delay_ms(self) -> float:
        if not self._config.adaptive_hedge:
            return self._config.hedged_delay_ms
        with self._lock:
            emas = [
                s.health.latency_ema_ms
                for s in self._specs.values()
                if s.health.latency_ema_ms is not None
            ]
        if not emas:
            return self._config.hedged_delay_ms
        # Use fastest healthy provider's EMA as p50 proxy; 0.35× is Google hedged-request optimal
        p50 = min(emas)
        return max(150.0, min(800.0, p50 * 0.35))

    # -- sequential (instant key rotation) -----------------------------------

    def _reason_sequential(
        self,
        payload_template: dict[str, Any],
        context: MarketContext,
        risk_context: RiskContext,
        started: float,
    ) -> DecisionProposal | None:
        for spec in _ranked_providers(self._specs, self._config):
            if _circuit_open(spec, self._config):
                logger.debug("Skipping %s — circuit open", spec.provider)
                continue
            num_keys = len(spec.keys) if spec.keys else 1
            for attempt_idx in range(num_keys):
                with self._lock:
                    key = _next_key(spec) if spec.keys else None
                try:
                    t0 = time.perf_counter()
                    import copy as _copy

                    payload = _copy.deepcopy(payload_template)
                    if spec.model:
                        payload["model"] = spec.model
                    if spec.max_tokens:
                        # Transport param only - prompt messages stay
                        # byte-identical (continuity Rule 3).
                        payload["max_tokens"] = spec.max_tokens
                    response = self._post(spec, key, payload)
                    response.raise_for_status()
                    proposal = self._parse(response.json(), context, risk_context)
                    dur = (time.perf_counter() - t0) * 1000.0
                    with self._lock:
                        _record_success(spec, dur)
                        self._last_success_provider = spec.provider
                    logger.info(
                        "Omega success provider=%s model=%s latency_ms=%.1f symbol=%s",
                        spec.provider,
                        spec.model or "auto",
                        dur,
                        context.snapshot.symbol,
                    )
                    return proposal
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code if exc.response is not None else 0
                    msg = f"{spec.provider} {status}: {exc}"
                    if status in (429, 401, 403) and attempt_idx < num_keys - 1:
                        logger.warning(
                            "Omega key %s failed %s — rotating", redact_key(key or ""), msg
                        )
                        continue
                    with self._lock:
                        _record_failure(spec, msg, self._config)
                    logger.warning("Omega provider %s failed: %s", spec.provider, msg)
                    break
                except Exception as exc:  # noqa: BLE001
                    msg = f"{spec.provider}: {exc}"
                    with self._lock:
                        _record_failure(spec, msg, self._config)
                    logger.warning("Omega provider %s error: %s", spec.provider, msg)
                    break
        return None

    # -- parallel race (speed of light) --------------------------------------

    def _reason_parallel(
        self,
        payload_template: dict[str, Any],
        context: MarketContext,
        risk_context: RiskContext,
        started: float,
    ) -> DecisionProposal | None:
        specs = [
            s
            for s in _ranked_providers(self._specs, self._config)
            if not _circuit_open(s, self._config)
        ]
        if not specs:
            return None

        def _call(spec: _ProviderSpec) -> DecisionProposal | None:
            num_keys = len(spec.keys) if spec.keys else 1
            for attempt_idx in range(num_keys):
                with self._lock:
                    key = _next_key(spec) if spec.keys else None
                import copy as _copy

                payload = _copy.deepcopy(payload_template)
                if spec.model:
                    payload["model"] = spec.model
                if spec.max_tokens:
                    # Transport param only - prompt messages stay
                    # byte-identical (continuity Rule 3).
                    payload["max_tokens"] = spec.max_tokens
                t0 = time.perf_counter()
                try:
                    resp = self._post(spec, key, payload)
                    resp.raise_for_status()
                    proposal = self._parse(resp.json(), context, risk_context)
                    with self._lock:
                        _record_success(spec, (time.perf_counter() - t0) * 1000.0)
                        self._last_success_provider = spec.provider
                    return proposal
                except httpx.HTTPStatusError as exc:
                    status = exc.response.status_code if exc.response is not None else 0
                    if status in (429, 401, 403) and attempt_idx < num_keys - 1:
                        continue
                    with self._lock:
                        _record_failure(spec, f"{spec.provider} {status}: {exc}", self._config)
                    return None
                except Exception as exc:  # noqa: BLE001
                    with self._lock:
                        _record_failure(spec, f"{spec.provider}: {exc}", self._config)
                    return None
            return None

        with ThreadPoolExecutor(max_workers=len(specs)) as pool:
            futures = {pool.submit(_call, s): s for s in specs}
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                except Exception:
                    continue
                if result is not None:
                    # Cancel remaining (best-effort)
                    for f in futures:
                        f.cancel()
                    return result
        return None

    def _reason_hedged(
        self,
        payload_template: dict[str, Any],
        context: MarketContext,
        risk_context: RiskContext,
        started: float,
    ) -> DecisionProposal | None:
        # Staggered parallel: start providers with hedged_delay_ms gaps,
        # return first success.
        import copy as _copy

        specs = [
            s
            for s in _ranked_providers(self._specs, self._config)
            if not _circuit_open(s, self._config)
        ]
        if not specs:
            return None

        with ThreadPoolExecutor(max_workers=len(specs)) as pool:
            futures: list[Any] = []
            for spec in specs:
                if futures:
                    done, _ = wait(futures, timeout=self._current_hedged_delay_ms() / 1000.0)
                    for f in done:
                        try:
                            r = f.result()
                            if r is not None:
                                for ff in futures:
                                    ff.cancel()
                                return r  # type: ignore[no-any-return]
                        except Exception:
                            pass
                    # If any future already succeeded, return it without launching more
                    for f in futures:
                        if f.done():
                            try:
                                r2 = f.result()
                                if r2 is not None:
                                    for ff in futures:
                                        ff.cancel()
                                    return r2  # type: ignore[no-any-return]
                            except Exception:
                                pass

                def _call_one(s: _ProviderSpec = spec) -> DecisionProposal | None:
                    num_keys = len(s.keys) if s.keys else 1
                    for attempt_idx in range(num_keys):
                        with self._lock:
                            k = _next_key(s) if s.keys else None
                        p = _copy.deepcopy(payload_template)
                        if s.model:
                            p["model"] = s.model
                        t0 = time.perf_counter()
                        try:
                            resp = self._post(s, k, p)
                            resp.raise_for_status()
                            prop = self._parse(resp.json(), context, risk_context)
                            with self._lock:
                                _record_success(s, (time.perf_counter() - t0) * 1000.0)
                                self._last_success_provider = s.provider
                            return prop
                        except httpx.HTTPStatusError as exc:
                            status = exc.response.status_code if exc.response is not None else 0
                            if status in (429, 401, 403) and attempt_idx < num_keys - 1:
                                continue
                            with self._lock:
                                _record_failure(s, f"{s.provider} {status}: {exc}", self._config)
                            return None
                        except Exception as exc:  # noqa: BLE001
                            with self._lock:
                                _record_failure(s, f"{s.provider}: {exc}", self._config)
                            return None
                    return None

                futures.append(pool.submit(_call_one))

            for fut in as_completed(futures):
                try:
                    res = fut.result()
                    if res is not None:
                        for f in futures:
                            f.cancel()
                        return res  # type: ignore[no-any-return]
                except Exception:
                    continue
        return None

    # -- HTTP ----------------------------------------------------------------

    def _post(
        self, spec: _ProviderSpec, key: str | None, payload: dict[str, Any]
    ) -> httpx.Response:
        client = self._clients[spec.provider]
        url = f"{spec.base_url.rstrip('/')}/chat/completions"
        headers: dict[str, str] = {}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        # OpenRouter requires attribution headers
        if spec.provider == "openrouter":
            headers["HTTP-Referer"] = "https://ati.local"
            headers["X-Title"] = "ATI-Omega"
        return client.post(url, json=payload, headers=headers or None)

    # -- parsing (mirrors AiOmniRouteReasoner, zero drift) -------------------

    def _parse(
        self, data: Any, context: MarketContext, risk_context: RiskContext
    ) -> DecisionProposal:
        content = _extract_content(data)
        parsed = _parse_json(content)
        return self._proposal_from_dict(parsed, context, risk_context)

    def _proposal_from_dict(
        self, parsed: dict[str, Any], context: MarketContext, risk_context: RiskContext
    ) -> DecisionProposal:
        raw_action = str(parsed["action_type"]).strip().lower()
        # Free-tier models frequently answer "hold"/"wait"/"pass" instead of a
        # valid action type; treat those as an intentional stand-aside rather
        # than a provider parse failure (which would burn the fallback chain).
        if raw_action in {"hold", "wait", "pass", "no_action", "noaction", "stay_out"}:
            parsed = {**parsed, "action_type": ProposedActionType.STAND_ASIDE.value}
            parsed.setdefault(
                "rationale",
                "Model advised holding (no directional edge); mapped to stand_aside.",
            )
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
        return DecisionProposal(
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

    def _plan_from_dict(
        self, parsed: dict[str, Any], context: MarketContext
    ) -> PreTradePlan | None:
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

    def _record_global_failure(self, reason: str, duration_ms: float) -> None:
        self._failures += 1
        self._last_failure_reason = reason
        self._last_failure_at = self._clock()
        self._last_failure_duration_ms = duration_ms
        logger.warning(
            "%s: Omega all providers failed: %s (latency_ms=%.1f, failures=%d)",
            AI_UNAVAILABLE,
            reason,
            duration_ms,
            self._failures,
        )

    def _stand_aside(
        self, context: MarketContext, risk_context: RiskContext, reason: str
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
# Shared JSON helpers (byte-identical to AiOmniRouteReasoner)
# ---------------------------------------------------------------------------


def _extract_content(data: Any) -> str:
    try:
        message = data["choices"][0]["message"]
        content = message["content"]
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            import json as _json

            return _json.dumps(content)
        return str(content)
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"Unexpected response shape: {exc}") from exc


def _parse_json(content: str) -> dict[str, Any]:
    import json as _json

    text = content.strip()
    try:
        parsed = _json.loads(text)
    except _json.JSONDecodeError:
        if not text.startswith("```"):
            raise
        body = text.split("```", 2)[1].lstrip("json").strip()
        parsed = _json.loads(body)
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
        items.append(EvidenceItem(source=name, summary=f"{name} feature", value=feature.value))
    return tuple(items)
