"""Unit tests for the AiOmniRouteReasoner (ADR 0005)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.application.decision.omni_route_reasoner import (
    AiOmniRouteReasoner,
    OmniRouteConfig,
)
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import ProposedActionType, RiskContext
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


def reasoner_with_handler(handler) -> AiOmniRouteReasoner:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return AiOmniRouteReasoner(client=client, clock=lambda: ts())


class TestAiOmniRouteReasoner:
    def test_valid_reply_produces_proposal(self):
        reasoner = reasoner_with_handler(lambda request: httpx.Response(200, json=valid_reply()))
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG
        assert proposal.symbol == "btcusdt"
        assert proposal.confidence == pytest.approx(0.72)

    def test_stand_aside_action_honored(self):
        reasoner = reasoner_with_handler(
            lambda request: httpx.Response(200, json=valid_reply("stand_aside"))
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE

    def test_http_error_degrades_to_stand_aside(self):
        reasoner = reasoner_with_handler(lambda request: httpx.Response(503, text="unavailable"))
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
        assert reasoner.failure_count == 1
        assert reasoner.last_failure_reason is not None

    def test_timeout_degrades_to_stand_aside(self):
        def handler(request) -> httpx.Response:
            raise httpx.ConnectTimeout("outage")

        reasoner = reasoner_with_handler(handler)
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
        assert reasoner.failure_count == 1

    def test_malformed_json_degrades(self):
        reasoner = reasoner_with_handler(
            lambda request: httpx.Response(
                200,
                json={"choices": [{"message": {"content": "not json"}}]},
            )
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
        assert reasoner.failure_count == 1

    def test_fenced_json_accepted(self):
        content = (
            '```json\n{"confidence": 0.5, "uncertainty": "u", '
            '"hypothesis_statement": "h", "action_type": "stand_aside", '
            '"size_fraction": 0.1, "rationale": "r", "alternatives": []}\n```'
        )
        fence = {"choices": [{"message": {"content": content}}]}
        reasoner = reasoner_with_handler(lambda request: httpx.Response(200, json=fence))
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE

    def test_invalid_action_type_degrades(self):
        reasoner = reasoner_with_handler(
            lambda request: httpx.Response(200, json=valid_reply("buy_now"))
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
        assert reasoner.failure_count == 1

    def test_risk_context_is_carried_through(self):
        reasoner = reasoner_with_handler(lambda request: httpx.Response(200, json=valid_reply()))
        ctx = risk_context()
        proposal = reasoner.reason(make_context(), ctx)
        assert proposal.risk_context == ctx

    def test_recalls_memory_only_for_symbol(self, tmp_path):
        db = Database(tmp_path / "test.db")
        memory = SqliteMemoryRepository(db)
        memory.record(
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
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read()
            return httpx.Response(200, json=valid_reply())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        reasoner = AiOmniRouteReasoner(
            config=OmniRouteConfig(), memory_store=memory, client=client, clock=lambda: ts()
        )
        proposal = reasoner.reason(make_context(), risk_context())
        assert proposal.primary_action is not None
        body = captured["body"].decode()
        import json as _json

        payload = _json.loads(body)
        user = payload["messages"][1]["content"]
        assert "LOSS" in user or "loss" in user

    def test_no_memory_when_store_absent(self):
        captured: dict[str, Any] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read()
            return httpx.Response(200, json=valid_reply())

        client = httpx.Client(transport=httpx.MockTransport(handler))
        reasoner = AiOmniRouteReasoner(client=client, clock=lambda: ts())
        reasoner.reason(make_context(), risk_context())
        import json as _json

        payload = _json.loads(captured["body"].decode())
        user = payload["messages"][1]["content"]
        assert "episodic_memory" not in user or "[]" in user
