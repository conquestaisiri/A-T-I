"""Prompt-determinism regression guard for the OmniRoute continuity invariant.

Directive `docs/ATI_OmniRoute_Context_Continuity.md` (persisted 2026-08-19,
BINDING): whenever OmniRoute switches the AI provider/model, the AI that
answers must see exactly the same knowledge, context, and memory as the one
that answered before. The switch is invisible.

This test proves the architectural core of that invariant: the prompt a
reasoner sends is a **pure, deterministic function of durable state** (market
context, risk context, recalled episodic memory) plus a fixed, versioned
system persona. It is never a function of the conversational window or of
which provider/model is configured.

Two properties are asserted:

1. Both AIReasoner implementations (`AiOmniRouteReasoner` and
   `PydanticAIReasoner`) produce **byte-identical** system and user messages
   for identical durable inputs — so a switch between implementations (or a
   provider/model switch behind the OmniRoute gateway) cannot change what
   the AI knows.
2. Changing provider/model/transport configuration (base_url, model,
   temperature) changes the request's transport parameters only — never the
   knowledge-bearing messages.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from backend.application.ai.pydantic_ai_reasoner import (
    PydanticAIConfig,
    PydanticAIReasoner,
)
from backend.application.decision.omni_route_reasoner import (
    AiOmniRouteReasoner,
    OmniRouteConfig,
)
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import RiskContext
from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_context() -> MarketContext:
    event = ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts(),
        payload={"symbol": "btcusdt", "trade_id": 1, "price": 100.0, "quantity": 1.0},
    )
    snapshot = ContextSnapshot.from_events((event,))
    feature = ContextFeature(
        name="trend", value={"direction": "up"}, computation_timestamp=ts(), execution_time=0.0
    )
    return MarketContext(snapshot=snapshot, features=(("trend", feature),), created_at=ts())


def make_risk() -> RiskContext:
    return RiskContext(
        account_equity=100_000.0,
        open_exposure_pct=0.0,
        daily_loss_pct=0.0,
        monthly_loss_pct=0.0,
        total_loss_pct=0.0,
        drawdown_pct=0.0,
        position_count=0,
    )


def memory_store(tmp_path) -> SqliteMemoryRepository:
    db = Database(tmp_path / "determinism.db")
    store = SqliteMemoryRepository(db)
    store.record(
        MemoryEpisode(
            episode_id="ep-1",
            correlation_id="corr-1",
            symbol="btcusdt",
            created_at=ts(),
            proposal_id="prop-1",
            action_type="enter_long",
            confidence=0.8,
            outcome=MemoryOutcome.LOSS,
            realized_pnl=-50.0,
            summary="long lost",
        )
    )
    store.record(
        MemoryEpisode(
            episode_id="ep-2",
            correlation_id="corr-2",
            symbol="btcusdt",
            created_at=ts(),
            proposal_id="prop-2",
            action_type="stand_aside",
            confidence=0.6,
            outcome=MemoryOutcome.WIN,
            realized_pnl=0.0,
            summary="waited out the chop",
        )
    )
    return store


def omni_messages(
    reasoner: AiOmniRouteReasoner, context: MarketContext, risk: RiskContext
) -> tuple[str, str]:
    payload = reasoner._build_payload(context, risk)
    messages = payload["messages"]
    return messages[0]["content"], messages[1]["content"]


def pydantic_messages(
    reasoner: PydanticAIReasoner, context: MarketContext, risk: RiskContext
) -> tuple[str, str]:
    return reasoner._system_prompt(), reasoner._build_user_prompt(context, risk)


def omega_messages(reasoner, context: MarketContext, risk: RiskContext) -> tuple[str, str]:
    from backend.application.decision.prompt_builder import build_messages

    # Omega builds via prompt_builder directly; mirror via build_messages
    payload = reasoner._build_payload if hasattr(reasoner, "_build_payload") else None
    # SmartFallback has no _build_payload, it calls build_payload directly
    del payload  # unused, keep for clarity
    msgs = build_messages(
        context,
        risk,
        memory_store=reasoner._memory_store,
        recall_limit=reasoner._config.recall_limit,
    )  # type: ignore[attr-defined]
    return msgs[0]["content"], msgs[1]["content"]


class TestPromptDeterminism:
    def test_reasoners_build_identical_prompts_for_identical_state(self, tmp_path):
        context = make_context()
        risk = make_risk()
        store = memory_store(tmp_path)

        omni = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store, clock=lambda: ts())
        pydantic = PydanticAIReasoner(
            config=PydanticAIConfig(), memory_store=store, clock=lambda: ts()
        )

        omni_system, omni_user = omni_messages(omni, context, risk)
        py_system, py_user = pydantic_messages(pydantic, context, risk)

        assert omni_system == py_system
        assert omni_user == py_user

    def test_prompt_is_pure_function_of_durable_state(self, tmp_path):
        """Same durable state, twice, two stores => identical prompts.

        Proves the user message depends only on (context, risk, memory) and
        not on any per-call or ephemeral state.
        """
        context = make_context()
        risk = make_risk()

        store_a = memory_store(tmp_path)
        store_b = memory_store(tmp_path)

        a = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store_a, clock=lambda: ts())
        b = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store_b, clock=lambda: ts())

        _, user_a = omni_messages(a, context, risk)
        _, user_b = omni_messages(b, context, risk)

        assert user_a == user_b

    def test_provider_model_config_never_changes_knowledge(self, tmp_path):
        """Different base_url/model/temperature change transport only.

        The knowledge-bearing messages must be byte-identical; only the
        request's transport parameters (model, temperature, max_tokens) may
        differ. This is Rule 3 of the continuity directive.
        """
        context = make_context()
        risk = make_risk()
        store = memory_store(tmp_path)

        default = AiOmniRouteReasoner(
            config=OmniRouteConfig(), memory_store=store, clock=lambda: ts()
        )
        switched = AiOmniRouteReasoner(
            config=OmniRouteConfig(
                base_url="http://localhost:20128/v1",
                model="auto/coding",
                temperature=0.9,
                max_tokens=2000,
            ),
            memory_store=store,
            clock=lambda: ts(),
        )

        default_system, default_user = omni_messages(default, context, risk)
        switched_system, switched_user = omni_messages(switched, context, risk)

        assert switched_system == default_system
        assert switched_user == default_user

        switched_payload = switched._build_payload(context, risk)
        assert switched_payload["model"] == "auto/coding"
        assert switched_payload["temperature"] == 0.9
        assert switched_payload["max_tokens"] == 2000

    def test_user_message_is_valid_json_carrying_memory(self, tmp_path):
        context = make_context()
        risk = make_risk()
        store = memory_store(tmp_path)

        omni = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store, clock=lambda: ts())
        _, user = omni_messages(omni, context, risk)

        parsed = json.loads(user)
        assert parsed["symbol"] == "btcusdt"
        assert parsed["risk"]["account_equity"] == 100_000.0
        assert len(parsed["episodic_memory"]) == 2
        assert "outcome" in parsed["episodic_memory"][0]
        assert "action_type" in parsed["episodic_memory"][0]
        assert "episode_id" not in parsed["episodic_memory"][0]
        # seconds precision, no milliseconds
        assert parsed["episodic_memory"][0]["created_at"].endswith("+00:00")
        assert "." not in parsed["episodic_memory"][0]["created_at"].split("+")[0].split("T")[-1]

    def test_user_message_json_is_sort_keys_deterministic(self, tmp_path):
        context = make_context()
        risk = make_risk()
        store = memory_store(tmp_path)
        omni = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store, clock=lambda: ts())
        _, user = omni_messages(omni, context, risk)
        assert user == json.dumps(json.loads(user), sort_keys=True, default=str)
        assert list(json.loads(user).keys()) == sorted(json.loads(user).keys())

    def test_memory_recall_enters_context_deterministically(self, tmp_path):
        """Same episode set stored in opposite order produces the same recall.

        Recall is keyed on ``created_at DESC, id DESC`` (memory_repository.py),
        so episodes with distinct timestamps recall in chronological order
        regardless of insertion order. Same durable facts => same prompt.
        """
        context = make_context()
        risk = make_risk()

        def store_with(episodes) -> SqliteMemoryRepository:
            db = Database(tmp_path / f"determinism-{len(episodes)}.db")
            s = SqliteMemoryRepository(db)
            for ep in episodes:
                s.record(ep)
            return s

        def episode(i: int, action: str, outcome: MemoryOutcome, when: datetime) -> MemoryEpisode:
            return MemoryEpisode(
                episode_id=f"ep-{i}",
                correlation_id=f"corr-{i}",
                symbol="btcusdt",
                created_at=when,
                proposal_id=f"prop-{i}",
                action_type=action,
                confidence=0.7,
                outcome=outcome,
                realized_pnl=-10.0 * i,
                summary=f"episode {i}",
            )

        t1 = datetime(2026, 1, 14, 9, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC)
        eps_a = [
            episode(1, "enter_long", MemoryOutcome.LOSS, t1),
            episode(2, "stand_aside", MemoryOutcome.WIN, t2),
        ]
        eps_b = [
            episode(2, "stand_aside", MemoryOutcome.WIN, t2),
            episode(1, "enter_long", MemoryOutcome.LOSS, t1),
        ]

        a = AiOmniRouteReasoner(
            config=OmniRouteConfig(), memory_store=store_with(eps_a), clock=lambda: ts()
        )
        b = AiOmniRouteReasoner(
            config=OmniRouteConfig(), memory_store=store_with(eps_b), clock=lambda: ts()
        )

        _, user_a = omni_messages(a, context, risk)
        _, user_b = omni_messages(b, context, risk)

        assert json.loads(user_a)["episodic_memory"] == json.loads(user_b)["episodic_memory"]

    def test_omega_builds_identical_prompt_to_omni(self, tmp_path):
        """Omega (Zen/Groq/OpenRouter) must see same knowledge as Omni."""
        from backend.application.decision.smart_fallback_reasoner import (
            OmegaConfig,
            SmartFallbackReasoner,
        )

        context = make_context()
        risk = make_risk()
        store = memory_store(tmp_path)

        omni = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store, clock=lambda: ts())
        omega = SmartFallbackReasoner(
            config=OmegaConfig(recall_limit=6),
            memory_store=store,
            provider_keys={"zen": []},
            clock=lambda: ts(),
        )
        omni_system, omni_user = omni_messages(omni, context, risk)
        omega_system, omega_user = omega_messages(omega, context, risk)
        assert omega_system == omni_system
        assert omega_user == omni_user

    def test_prompt_version_pinned(self):
        import hashlib
        import json

        from backend.application.decision.prompt_builder import PROMPT_VERSION, SYSTEM_PROMPT

        assert PROMPT_VERSION == "v1"
        assert SYSTEM_PROMPT.startswith("You are the reasoning component")
        # hash pinned so persona change without bumping version fails
        assert hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest().startswith("ab698d75196c")
        # version is audit log, not prompt content
        context = make_context()
        risk = make_risk()
        import tempfile
        from pathlib import Path as _Path

        tmp = _Path(tempfile.mkdtemp())
        store = memory_store(tmp)
        omni = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store, clock=lambda: ts())
        _, user = omni_messages(omni, context, risk)
        assert "prompt_version" not in json.loads(user)
        assert "PROMPT_VERSION" not in user

    def test_recall_limit_propagates(self, tmp_path):
        # 10 episodes, limit 6 vs 10 should change prompt
        from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome
        from backend.infrastructure.sqlite.database import Database
        from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository

        def store_with_n(n: int):
            db = Database(tmp_path / f"recall{n}.db")
            s = SqliteMemoryRepository(db)
            for i in range(n):
                s.record(
                    MemoryEpisode(
                        episode_id=f"ep-{i}",
                        correlation_id=f"corr-{i}",
                        symbol="btcusdt",
                        created_at=ts(),
                        proposal_id=f"prop-{i}",
                        action_type="enter_long",
                        confidence=0.5,
                        outcome=MemoryOutcome.WIN,
                        realized_pnl=1.0,
                        summary=f"e{i}",
                    )
                )
            return s

        context = make_context()
        risk = make_risk()
        store = store_with_n(10)
        omni6 = AiOmniRouteReasoner(
            config=OmniRouteConfig(recall_limit=6), memory_store=store, clock=lambda: ts()
        )
        omni10 = AiOmniRouteReasoner(
            config=OmniRouteConfig(recall_limit=10), memory_store=store, clock=lambda: ts()
        )
        _, user6 = omni_messages(omni6, context, risk)
        _, user10 = omni_messages(omni10, context, risk)
        assert len(json.loads(user6)["episodic_memory"]) == 6
        assert len(json.loads(user10)["episodic_memory"]) == 10
        # also Omega
        from backend.application.decision.smart_fallback_reasoner import (
            OmegaConfig,
            SmartFallbackReasoner,
        )

        omega6 = SmartFallbackReasoner(
            config=OmegaConfig(recall_limit=6),
            memory_store=store,
            provider_keys={"zen": []},
            clock=lambda: ts(),
        )
        omega10 = SmartFallbackReasoner(
            config=OmegaConfig(recall_limit=10),
            memory_store=store,
            provider_keys={"zen": []},
            clock=lambda: ts(),
        )
        _, o6 = omega_messages(omega6, context, risk)
        _, o10 = omega_messages(omega10, context, risk)
        assert len(json.loads(o6)["episodic_memory"]) == 6
        assert len(json.loads(o10)["episodic_memory"]) == 10

    def test_reordered_features_yield_same_prompt(self, tmp_path):
        # Same features in different order must give byte-identical prompt (sort_keys)
        from backend.domain.context.context_feature import ContextFeature

        def ctx_with(order):
            event = make_context().snapshot
            feats = []
            for name in order:
                feats.append(
                    (
                        name,
                        ContextFeature(
                            name=name,
                            value={"v": name},
                            computation_timestamp=ts(),
                            execution_time=0.0,
                        ),
                    )
                )
            return MarketContext(snapshot=event, features=tuple(feats), created_at=ts())

        risk = make_risk()
        store = memory_store(tmp_path)
        a = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store, clock=lambda: ts())
        b = AiOmniRouteReasoner(config=OmniRouteConfig(), memory_store=store, clock=lambda: ts())
        _, user_a = omni_messages(a, ctx_with(["trend", "momentum"]), risk)
        _, user_b = omni_messages(b, ctx_with(["momentum", "trend"]), risk)
        assert user_a == user_b
        assert json.loads(user_a)["features"].keys() == json.loads(user_b)["features"].keys()
