"""Wiring tests for the live risk-gate feeds (gap G3).

These verify the *wiring* — that the ingest path feeds the shared risk gate
with VPIN toxicity from observed trades, and the decision path feeds realized
impact fills (guarded by operator-supplied market stats) and, only when
explicitly opted in, fractional-Kelly edge estimates derived from closed
episodic memory.

The gate's own veto logic is already covered by ``test_risk_gate.py``; here we
prove the dormant layers in CircuitBreakerRiskGate become live end-to-end (or
stay dormant until the operator opts in).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.context.bootstrap import (
    build_context_pipeline,
    build_reflection_service,
)
from backend.application.decision.rule_based_solver import RuleBasedSolver
from backend.application.pipeline.context_pipeline_service import ContextPipelineService
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.risk.circuit_breaker_risk_gate import (
    CircuitBreakerRiskGate,
    KellyEdgeEstimate,
)
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import (
    PaperTradingSimulator,
    SimulationResult,
)
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_context(*, trend_direction: str, momentum_pct: float) -> MarketContext:
    """Mirror the context builder from test_decision_pipeline_service."""
    event = ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts(),
        payload={"symbol": "btcusdt", "trade_id": 1, "price": 100.0, "quantity": 1.0},
    )
    snapshot = ContextSnapshot.from_events((event,))

    def feature(name: str, value: object) -> ContextFeature:
        return ContextFeature(
            name=name, value=value, computation_timestamp=ts(), execution_time=0.0
        )

    features = (
        (
            "trend",
            feature(
                "trend",
                {
                    "direction": trend_direction,
                    "change_pct": momentum_pct,
                    "first_price": 99.0,
                    "last_price": 100.0,
                    "sample_count": 5,
                },
            ),
        ),
        (
            "momentum",
            feature("momentum", {"rate_of_change_pct": momentum_pct, "sample_count": 3}),
        ),
        (
            "volatility",
            feature("volatility", {"std_dev": 0.001, "mean_return": 0.0, "return_count": 4}),
        ),
        (
            "volume",
            feature("volume", {"total_volume": 50.0, "average_volume": 10.0, "trade_count": 5}),
        ),
    )
    return MarketContext(snapshot=snapshot, features=features, created_at=ts())


def make_trade(
    *,
    side: str | None = "buy",
    quantity: float = 1.0,
    symbol: str = "btcusdt",
) -> ObservationEvent:
    return ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts(),
        payload={
            "symbol": symbol,
            "trade_id": 1,
            "price": 100.0,
            "quantity": quantity,
            "side": side,
        },
    )


@pytest.fixture
def ingest(tmp_path) -> tuple[ContextPipelineService, CircuitBreakerRiskGate]:
    database = Database(tmp_path / "ingest.db")
    settings_from_paths = build_context_pipeline_from_settings()
    builder, _, _, _ = build_context_pipeline(settings_from_paths)
    gate = CircuitBreakerRiskGate()
    pipeline = ContextPipelineService(
        bus=ObservationBus(maxsize=16),
        context_builder=builder,
        observation_repository=SqliteObservationRepository(database),
        context_repository=SqliteContextRepository(database),
        risk_feed=gate,
    )
    return pipeline, gate


def build_context_pipeline_from_settings():
    from backend.application.interfaces.context_settings import (
        ContextSettings,
        FeatureSettings,
    )

    feature = FeatureSettings(enabled=True, parameters={})
    settings = ContextSettings(
        window_duration=timedelta(seconds=60),
        features={"trend": feature, "momentum": feature, "volatility": feature, "volume": feature},
    )
    return settings


class TestToxicityFeed:
    """The ingest path converts side-bearing TRADE events into VPIN flow."""

    def test_buy_trade_feeds_positive_signed_flow(self, ingest):
        pipeline, gate = ingest
        pipeline.handle(make_trade(side="buy", quantity=5.0))
        tracker = gate._toxicity["btcusdt"]
        assert tracker.state().current_bucket_volume == 5.0

    def test_sell_trade_feeds_negative_signed_flow(self, ingest):
        pipeline, gate = ingest
        pipeline.handle(make_trade(side="sell", quantity=3.0))
        tracker = gate._toxicity["btcusdt"]
        assert tracker.state().current_bucket_volume == 3.0

    def test_side_less_trade_is_ignored(self, ingest):
        """Drive-route events carry no ``side``; they must contribute nothing."""
        pipeline, gate = ingest
        event = make_trade(side=None)
        del event.payload["side"]  # mirror the drive route's payload shape
        pipeline.handle(event)
        assert "btcusdt" not in gate._toxicity


class TestImpactFeed:
    """The decision path feeds realized fills into the impact veto."""

    def test_impact_fill_is_recorded_when_market_stats_registered(self, tmp_path):
        gate = CircuitBreakerRiskGate()
        gate.set_market_stats(
            "btcusdt", avg_daily_volume=10_000.0, volatility_bps=50.0, half_spread_bps=1.0
        )
        database = Database(tmp_path / "impact.db")
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=gate, order_gateway=engine, ledger=SqliteLedgerRepository(database)
        )
        pipeline = DecisionPipelineService(
            reasoner=RuleBasedSolver(),
            proposal_repository=SqliteProposalRepository(database),
            simulator=simulator,
            risk_feed=gate,
        )
        engine.set_mark_price(100.0)
        step = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert step.result is SimulationResult.OPENED
        assert gate._impact.observation_count("btcusdt") >= 1

    def test_impact_feed_guarded_by_market_stats(self, tmp_path):
        """Without operator market stats the feed is skipped, not fatal."""
        gate = CircuitBreakerRiskGate()
        database = Database(tmp_path / "impact.db")
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=gate, order_gateway=engine, ledger=SqliteLedgerRepository(database)
        )
        pipeline = DecisionPipelineService(
            reasoner=RuleBasedSolver(),
            proposal_repository=SqliteProposalRepository(database),
            simulator=simulator,
            risk_feed=gate,
        )
        engine.set_mark_price(100.0)
        step = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert step.result is SimulationResult.OPENED
        assert gate._impact.observation_count("btcusdt") == 0


class TestKellyFeed:
    """The Kelly edge feed is the *learning* feed: opt-in only."""

    def test_kelly_feed_off_by_default(self, tmp_path):
        gate = CircuitBreakerRiskGate()
        database = Database(tmp_path / "kelly.db")
        memory = SqliteMemoryRepository(database)
        reflection = build_reflection_service(tmp_path / "kelly.db", memory_store=memory)
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=gate, order_gateway=engine, ledger=SqliteLedgerRepository(database)
        )
        pipeline = DecisionPipelineService(
            reasoner=RuleBasedSolver(),
            proposal_repository=SqliteProposalRepository(database),
            simulator=simulator,
            reflection=reflection,
            risk_feed=gate,
            kelly_from_memory=False,
        )
        engine.set_mark_price(100.0)
        closed = self._close_trade(tmp_path, gate, pipeline)
        assert closed is True
        assert gate._edge_cache.get("btcusdt") is None

    def test_kelly_feed_enabled_after_close(self, tmp_path):
        gate = CircuitBreakerRiskGate()
        database = Database(tmp_path / "kelly.db")
        memory = SqliteMemoryRepository(database)
        # Seed enough closed episodes to clear the gate's min-evidence floor.
        for i in range(30):
            episode = _episode(i, pnl=+100.0 if i % 3 else -60.0)
            memory.record(episode)
        reflection = build_reflection_service(tmp_path / "kelly.db", memory_store=memory)
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=gate, order_gateway=engine, ledger=SqliteLedgerRepository(database)
        )
        pipeline = DecisionPipelineService(
            reasoner=RuleBasedSolver(),
            proposal_repository=SqliteProposalRepository(database),
            simulator=simulator,
            reflection=reflection,
            risk_feed=gate,
            kelly_from_memory=True,
        )
        engine.set_mark_price(100.0)
        closed = self._close_trade(tmp_path, gate, pipeline)
        assert closed is True
        edge = gate._edge_cache.get("btcusdt")
        assert isinstance(edge, KellyEdgeEstimate)
        assert edge.trade_count >= 10
        assert 0.0 <= edge.confidence <= 1.0

    def _close_trade(self, tmp_path, gate, pipeline) -> bool:
        """Drive one open then a reversal to close, returning whether it closed."""
        opened = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        if opened.result is not SimulationResult.OPENED:
            return False
        closed = pipeline.process(make_context(trend_direction="down", momentum_pct=-0.2), 90.0)
        return closed.result is SimulationResult.CLOSED


def _episode(i: int, *, pnl: float) -> MemoryEpisode:
    return MemoryEpisode(
        episode_id=f"ep-{i}",
        correlation_id=f"corr-{i}",
        symbol="btcusdt",
        created_at=ts(),
        proposal_id=f"prop-{i}",
        action_type="enter_long",
        confidence=0.7,
        outcome=MemoryOutcome.WIN if pnl > 0 else MemoryOutcome.LOSS,
        realized_pnl=pnl,
        summary=f"episode {i}",
    )
