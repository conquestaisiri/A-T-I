"""Tests for the Omega SmartFallbackReasoner — God-mode, zero downtime."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from backend.application.decision.prompt_builder import build_payload
from backend.application.decision.smart_fallback_reasoner import OmegaConfig, SmartFallbackReasoner
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import ProposedActionType, RiskContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_context(symbol: str = "btcusdt") -> MarketContext:
    event = ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts(),
        payload={"symbol": symbol, "trade_id": 1, "price": 100.0, "quantity": 1.0},
    )
    snapshot = ContextSnapshot.from_events((event,))
    feature = ContextFeature(
        name="trend", value={"direction": "up"}, computation_timestamp=ts(), execution_time=0.0
    )
    return MarketContext(snapshot=snapshot, features=(("trend", feature),), created_at=ts())


def risk_context() -> RiskContext:
    return RiskContext(
        account_equity=100_000.0,
        open_exposure_pct=0.0,
        daily_loss_pct=0.0,
        monthly_loss_pct=0.0,
        total_loss_pct=0.0,
        drawdown_pct=0.0,
        position_count=0,
    )


def valid_reply(action_type: str = "enter_long") -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"confidence": 0.72, "uncertainty": "medium", '
                        '"hypothesis_statement": "uptrend continues", '
                        f'"action_type": "{action_type}", "size_fraction": 0.1, '
                        '"rationale": "momentum confirms trend", "alternatives": []}'
                    )
                }
            }
        ]
    }


def make_reasoner(
    provider_keys: dict[str, list[str]],
    handlers: dict[str, Any],
    config: OmegaConfig | None = None,
) -> SmartFallbackReasoner:
    clients: dict[str, httpx.Client] = {}
    for provider, handler in handlers.items():
        clients[provider] = httpx.Client(transport=httpx.MockTransport(handler))
    cfg = config or OmegaConfig(race_mode="sequential")
    # Hermeticity: pin the chain to exactly the mocked providers so live
    # keyless providers (ovh/kilo/llm7) can never leak into a unit test.
    cfg = OmegaConfig(
        **{
            **{f: getattr(cfg, f) for f in OmegaConfig.__dataclass_fields__},
            "priority": tuple(handlers.keys()),
        }
    )
    return SmartFallbackReasoner(
        cfg,
        provider_keys=provider_keys,
        clients=clients,
        clock=lambda: ts(),
    )


class TestSmartFallbackSequential:
    def test_primary_success(self):
        reasoner = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"]},
            {
                "ovh": lambda r: httpx.Response(200, json=valid_reply()),
                "groq": lambda r: httpx.Response(200, json=valid_reply("stand_aside")),
            },
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG
        assert reasoner.last_success_provider == "ovh"

    def test_fallback_on_primary_failure(self):
        reasoner = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"]},
            {
                "ovh": lambda r: httpx.Response(503, text="overload"),
                "groq": lambda r: httpx.Response(200, json=valid_reply()),
            },
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG
        assert reasoner.last_success_provider == "groq"

    def test_key_rotation_on_429(self):
        calls: list[str] = []

        def groq_handler(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("Authorization", "")
            calls.append(auth)
            if "gsk_one" in auth:
                return httpx.Response(429, json={"error": "rate limit"})
            return httpx.Response(200, json=valid_reply())

        reasoner = make_reasoner(
            {"groq": ["gsk_one", "gsk_two"]},
            {"groq": groq_handler},
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert len(calls) == 2
        assert any("gsk_two" in c for c in calls)

    def test_all_providers_fail_degrades_to_stand_aside(self):
        reasoner = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"]},
            {
                "ovh": lambda r: httpx.Response(500, text="boom"),
                "groq": lambda r: httpx.Response(500, text="boom"),
            },
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
        assert reasoner.failure_count == 1

    def test_circuit_breaker_skips_sick_provider(self):
        # Fail ovh 5 times to open circuit, then groq should be tried first
        reasoner = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"]},
            {
                "ovh": lambda r: httpx.Response(500, text="sick"),
                "groq": lambda r: httpx.Response(200, json=valid_reply()),
            },
            config=OmegaConfig(race_mode="sequential", circuit_threshold=3),
        )
        for _ in range(3):
            reasoner.reason(make_context(), risk_context())
        # Next call: ovh is circuit-open, should go straight to groq
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert reasoner.last_success_provider == "groq"
        assert reasoner.provider_stats()["ovh"]["circuit_open"] is True


class TestSmartFallbackParallel:
    def test_parallel_race_picks_fastest(self):
        reasoner = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"]},
            {
                "ovh": lambda r: httpx.Response(200, json=valid_reply("enter_long")),
                "groq": lambda r: httpx.Response(200, json=valid_reply("enter_short")),
            },
            config=OmegaConfig(race_mode="parallel"),
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type in (
            ProposedActionType.ENTER_LONG,
            ProposedActionType.ENTER_SHORT,
        )

    def test_parallel_all_fail_still_stand_aside(self):
        reasoner = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"]},
            {
                "ovh": lambda r: httpx.Response(500, text="no"),
                "groq": lambda r: httpx.Response(500, text="no"),
            },
            config=OmegaConfig(race_mode="parallel"),
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE


class TestOmegaContinuity:
    def test_prompt_identical_across_providers(self):
        # The prompt must be byte-identical for ovh vs groq — only base_url/model differ.
        ctx = make_context()
        rc = risk_context()
        payload_ovh = build_payload(
            ctx, rc, memory_store=None, recall_limit=6, temperature=0.2, max_tokens=600, model=None
        )
        payload_groq = build_payload(
            ctx, rc, memory_store=None, recall_limit=6, temperature=0.2, max_tokens=600, model=None
        )
        assert payload_ovh["messages"] == payload_groq["messages"]
        assert payload_ovh["temperature"] == payload_groq["temperature"]

    def test_memory_recall_same_for_all_providers(self, tmp_path):
        from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome
        from backend.infrastructure.sqlite.database import Database
        from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository

        db = Database(tmp_path / "m.db")
        mem = SqliteMemoryRepository(db)
        mem.record(
            MemoryEpisode(
                episode_id="ep-1",
                correlation_id="btcusdt",
                symbol="btcusdt",
                proposal_id="prop-1",
                action_type="enter_long",
                confidence=0.8,
                outcome=MemoryOutcome.WIN,
                realized_pnl=10.0,
                created_at=ts(),
                summary="test win",
            )
        )
        p1 = build_payload(make_context(), risk_context(), memory_store=mem, recall_limit=6)
        p2 = build_payload(make_context(), risk_context(), memory_store=mem, recall_limit=6)
        assert p1["messages"][1]["content"] == p2["messages"][1]["content"]
        db.close()

    def test_http_requests_carry_identical_messages_across_providers(self):
        captured: dict[str, list] = {}

        def make_handler(provider):
            def handler(request: httpx.Request) -> httpx.Response:
                body = __import__("json").loads(request.content)
                captured[provider] = body["messages"]
                return httpx.Response(200, json=valid_reply())

            return handler

        reasoner = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"], "openrouter": ["sk-or-v1-test"]},
            {
                "ovh": make_handler("ovh"),
                "groq": make_handler("groq"),
                "openrouter": make_handler("openrouter"),
            },
            config=OmegaConfig(race_mode="sequential"),
        )
        # Force sequential to try ovh first, capture its request
        reasoner.reason(make_context(), risk_context())
        assert "ovh" in captured
        # Now make ovh fail, groq succeed, capture both
        captured.clear()

        def ovh_fail(request: httpx.Request) -> httpx.Response:
            body = __import__("json").loads(request.content)
            captured["ovh"] = body["messages"]
            return httpx.Response(500, text="fail")

        def groq_ok(request: httpx.Request) -> httpx.Response:
            body = __import__("json").loads(request.content)
            captured["groq"] = body["messages"]
            return httpx.Response(200, json=valid_reply())

        reasoner2 = make_reasoner(
            {"ovh": [], "groq": ["gsk_test"]},
            {"ovh": ovh_fail, "groq": groq_ok},
            config=OmegaConfig(race_mode="sequential"),
        )
        reasoner2.reason(make_context(), risk_context())
        assert captured["ovh"] == captured["groq"]

    def test_priority_order_and_unknown_scores_999(self):
        from backend.application.decision.smart_fallback_reasoner import (
            OmegaConfig,
            _ProviderSpec,
            _ranked_providers,
        )

        cfg = OmegaConfig(priority=("groq", "ovh", "openrouter"))
        specs = {
            "ovh": _ProviderSpec(
                provider="ovh", base_url="https://ovh", model=None, timeout=12, keys=[]
            ),
            "groq": _ProviderSpec(
                provider="groq", base_url="https://groq", model=None, timeout=10, keys=["gsk_test"]
            ),
            "unknown_xyz": _ProviderSpec(
                provider="unknown_xyz", base_url="https://unknown", model=None, timeout=10, keys=[]
            ),
        }
        ranked = _ranked_providers(specs, cfg)
        assert [s.provider for s in ranked] == ["groq", "ovh", "unknown_xyz"]

    def test_single_key_429_falls_to_next_provider(self):
        # Groq has single key that 429s, should fall to openrouter, not retry same key
        def groq_429(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, json={"error": "rate limit"})

        def openrouter_ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=valid_reply())

        reasoner = make_reasoner(
            {"groq": ["gsk_single"], "openrouter": ["sk-or-v1-test"]},
            {"groq": groq_429, "openrouter": openrouter_ok},
            config=OmegaConfig(race_mode="sequential", priority=("groq", "openrouter")),
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert reasoner.last_success_provider == "openrouter"

    def test_second_call_starts_at_next_key_after_rate_limit(self):
        # gsk_one 429 -> gsk_two success; next reason() should start at
        # gsk_one's successor (rotation visible with 3 keys)
        calls: list[str] = []

        def groq_mixed(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("Authorization", "")
            calls.append(auth)
            if "gsk_one" in auth and len([c for c in calls if "gsk_one" in c]) == 1:
                return httpx.Response(429, json={"error": "rate limit"})
            return httpx.Response(200, json=valid_reply())

        reasoner = make_reasoner(
            {"groq": ["gsk_one", "gsk_two", "gsk_three"]},
            {"groq": groq_mixed},
            config=OmegaConfig(race_mode="sequential", priority=("groq",)),
        )
        reasoner.reason(make_context(), risk_context())
        # First call used gsk_one (429) then gsk_two (200)
        assert "gsk_one" in calls[0]
        assert "gsk_two" in calls[1]
        calls.clear()
        reasoner.reason(make_context(), risk_context())
        # Next call should start at gsk_three (round-robin), not gsk_one
        assert "gsk_three" in calls[0]

    def test_sequential_advances_key_index_on_success(self):
        # After gsk_one succeeds, next call should start at gsk_two (no stall)
        calls: list[str] = []

        def groq_ok(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("Authorization", "")
            calls.append(auth)
            return httpx.Response(200, json=valid_reply())

        reasoner = make_reasoner(
            {"groq": ["gsk_one", "gsk_two"]},
            {"groq": groq_ok},
            config=OmegaConfig(race_mode="sequential", priority=("groq",)),
        )
        reasoner.reason(make_context(), risk_context())
        reasoner.reason(make_context(), risk_context())
        assert "gsk_one" in calls[0]
        assert "gsk_two" in calls[1]
        assert calls[0] != calls[1]

    def test_500_does_not_try_second_key(self):
        # 500 is provider sick, not key-specific, so second key must NOT be tried
        calls: list[str] = []

        def groq_500(request: httpx.Request) -> httpx.Response:
            auth = request.headers.get("Authorization", "")
            calls.append(auth)
            return httpx.Response(500, text="provider sick")

        def openrouter_ok(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=valid_reply())

        reasoner = make_reasoner(
            {"groq": ["gsk_one", "gsk_two"], "openrouter": ["sk-or-v1-test"]},
            {"groq": groq_500, "openrouter": openrouter_ok},
            config=OmegaConfig(race_mode="sequential", priority=("groq", "openrouter")),
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert reasoner.last_success_provider == "openrouter"
        # Only one groq call, second key not tried on 500
        assert len(calls) == 1

    def test_zero_keys_falls_back_to_keyless_anonymous(self):
        reasoner = SmartFallbackReasoner(
            config=OmegaConfig(priority=("groq", "ovh")),
            provider_keys={},  # no keys at all
            clients={
                "ovh": __import__("httpx").Client(
                    transport=__import__("httpx").MockTransport(
                        lambda r: __import__("httpx").Response(200, json=valid_reply())
                    )
                )
            },
            clock=lambda: ts(),
        )
        assert "ovh" in reasoner._specs
        assert reasoner._specs["ovh"].keys == []
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert reasoner.last_success_provider == "ovh"
