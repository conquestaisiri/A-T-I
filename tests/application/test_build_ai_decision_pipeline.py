"""Tests for the LLM-backed decision pipeline wiring (bootstrap)."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
from backend.application.context.bootstrap import build_ai_decision_pipeline, build_memory_pipeline
from backend.application.decision.omni_route_reasoner import AiOmniRouteReasoner, OmniRouteConfig
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import RiskContext
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


def valid_reply() -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": (
                        '{"confidence": 0.65, "uncertainty": "low", '
                        '"hypothesis_statement": "trend up", "action_type": "enter_long", '
                        '"size_fraction": 0.1, "rationale": "momentum", "alternatives": []}'
                    )
                }
            }
        ]
    }


class TestBuildAiDecisionPipeline:
    def test_returns_wired_ai_pipeline(self, tmp_path):
        db = tmp_path / "ai.db"
        client = httpx.Client(
            transport=httpx.MockTransport(lambda req: httpx.Response(200, json=valid_reply()))
        )
        reasoner = AiOmniRouteReasoner(OmniRouteConfig(), client=client, clock=lambda: ts())
        pipeline, simulator, fill_engine, _ = build_ai_decision_pipeline(
            db,
            omni_config=OmniRouteConfig(),
            memory_store=SqliteMemoryRepository(Database(db)),
        )
        pipeline._reasoner = reasoner  # noqa: SLF001 - inject test client into wiring
        fill_engine.set_mark_price(100.0)
        step = pipeline.process(make_context(), 100.0)
        assert isinstance(pipeline, DecisionPipelineService)
        assert isinstance(simulator, PaperTradingSimulator)
        assert isinstance(fill_engine, PaperFillEngine)
        assert step is not None
        assert step.risk_verdict is not None

    def test_build_memory_pipeline_returns_store(self, tmp_path):
        memory = build_memory_pipeline(tmp_path / "m.db")
        assert isinstance(memory, SqliteMemoryRepository)
        assert memory.count() == 0
