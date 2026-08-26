"""End-to-end tests for the DecisionPipelineService wiring."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.context.bootstrap import build_reflection_service
from backend.application.decision.rule_based_solver import RuleBasedSolver
from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import (
    PaperTradingSimulator,
    SimulationResult,
)
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.decision.trade_plan import PreTradePlan, StopLevel
from backend.domain.execution.trade_record import TradeStatus
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_context(*, trend_direction: str, momentum_pct: float) -> MarketContext:
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


@pytest.fixture
def decision_pipeline(
    tmp_path,
) -> tuple[DecisionPipelineService, PaperTradingSimulator, PaperFillEngine, SqliteLedgerRepository]:
    database = Database(tmp_path / "decision.db")
    proposal_repo = SqliteProposalRepository(database)
    ledger_repo = SqliteLedgerRepository(database)
    gate = CircuitBreakerRiskGate()
    engine = PaperFillEngine()
    simulator = PaperTradingSimulator(risk_gate=gate, order_gateway=engine, ledger=ledger_repo)
    pipeline = DecisionPipelineService(
        reasoner=RuleBasedSolver(),
        proposal_repository=proposal_repo,
        simulator=simulator,
    )
    return pipeline, simulator, engine, ledger_repo


class TestDecisionPipeline:
    def test_up_context_opens_position_and_persists_proposal(self, decision_pipeline):
        pipeline, simulator, engine, _ = decision_pipeline
        engine.set_mark_price(100.0)

        step = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert step.result is SimulationResult.OPENED
        assert "btcusdt" in simulator.positions
        assert step.record is not None
        assert step.record.status is TradeStatus.OPEN

    def test_down_context_opens_short(self, decision_pipeline):
        pipeline, _, engine, _ = decision_pipeline
        engine.set_mark_price(100.0)

        step = pipeline.process(make_context(trend_direction="down", momentum_pct=-0.2), 100.0)
        assert step.result is SimulationResult.OPENED
        assert step.record is not None
        assert step.record.side.value == "sell"

    def test_flat_context_stands_aside(self, decision_pipeline):
        pipeline, _, engine, _ = decision_pipeline
        engine.set_mark_price(100.0)

        step = pipeline.process(make_context(trend_direction="flat", momentum_pct=0.0), 100.0)
        assert step.result is SimulationResult.NO_ACTION
        assert step.record is None

    def test_existing_position_blocks_second_entry(self, decision_pipeline):
        pipeline, simulator, engine, _ = decision_pipeline
        engine.set_mark_price(100.0)
        pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert "btcusdt" in simulator.positions

        engine.set_mark_price(100.0)
        second = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert second.result is SimulationResult.NO_ACTION
        assert "btcusdt" in simulator.positions

    def test_risk_snapshot_feeds_reasoner_sizing(self, decision_pipeline):
        _, simulator, engine, _ = decision_pipeline
        engine.set_mark_price(100.0)
        risk = simulator.risk_snapshot()
        assert risk.account_equity == 100_000.0
        assert risk.open_exposure_pct == 0.0
        assert risk.position_count == 0

    def test_ledger_records_are_persisted(self, decision_pipeline):
        pipeline, _, engine, ledger_repo = decision_pipeline
        engine.set_mark_price(100.0)
        pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert ledger_repo.count("btcusdt") == 1


class TestSupervisorGating:
    def test_kill_switch_refuses_proposal_and_no_position(self, tmp_path):
        database = Database(tmp_path / "gate.db")
        proposal_repo = SqliteProposalRepository(database)
        ledger_repo = SqliteLedgerRepository(database)
        supervisor = SupervisorService()
        supervisor.engage_kill_switch("manual review")

        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=CircuitBreakerRiskGate(), order_gateway=engine, ledger=ledger_repo
        )
        pipeline = DecisionPipelineService(
            reasoner=RuleBasedSolver(),
            proposal_repository=proposal_repo,
            simulator=simulator,
            supervisor=supervisor,
        )

        engine.set_mark_price(100.0)
        step = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert step.result is SimulationResult.NO_ACTION
        assert step.record is None
        assert "btcusdt" not in simulator.positions
        assert step.risk_verdict is not None
        assert "supervisor" in step.risk_verdict

    def test_released_switch_allows_trading(self, tmp_path):
        database = Database(tmp_path / "gate2.db")
        proposal_repo = SqliteProposalRepository(database)
        ledger_repo = SqliteLedgerRepository(database)
        supervisor = SupervisorService()
        supervisor.engage_kill_switch("manual review")
        supervisor.release_kill_switch()

        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=CircuitBreakerRiskGate(), order_gateway=engine, ledger=ledger_repo
        )
        pipeline = DecisionPipelineService(
            reasoner=RuleBasedSolver(),
            proposal_repository=proposal_repo,
            simulator=simulator,
            supervisor=supervisor,
        )

        engine.set_mark_price(100.0)
        step = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert step.result is SimulationResult.OPENED
        assert "btcusdt" in simulator.positions


class EnterExitReasoner(AIReasoner):
    """Replays enter (step 1) then exit (step 2) so a trade actually closes."""

    def __init__(self) -> None:
        self._step = 0

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        self._step += 1
        action_type = ProposedActionType.ENTER_LONG if self._step == 1 else ProposedActionType.EXIT
        pre_trade_plan = (
            PreTradePlan(
                stop_loss=StopLevel(distance_pct=0.05),
                take_profit=StopLevel(distance_pct=0.10),
                risk_per_trade_pct=0.02,
                risk_reward_ratio=2.0,
            )
            if action_type is ProposedActionType.ENTER_LONG
            else None
        )
        return DecisionProposal(
            proposal_id=f"hook-{self._step}",
            correlation_id="corr-hook",
            created_at=context.created_at,
            symbol="btcusdt",
            hypothesis=Hypothesis(
                statement="hook",
                supporting_evidence=(EvidenceItem(source="stub", summary="s", value=1.0),),
                opposing_evidence=(),
            ),
            confidence=0.8,
            uncertainty="none",
            actions=(
                ProposedAction(action_type=action_type, size_fraction=0.1, order=1, rationale="r"),
            ),
            risk_context=risk_context,
            alternatives=(),
            rationale="r",
            pre_trade_plan=pre_trade_plan,
        )


class TestPostCloseReflection:
    def test_closed_trade_writes_episode_to_memory(self, tmp_path):
        database = Database(tmp_path / "hook.db")
        proposal_repo = SqliteProposalRepository(database)
        ledger_repo = SqliteLedgerRepository(database)
        memory = SqliteMemoryRepository(database)
        reflection = build_reflection_service(tmp_path / "hook.db", memory_store=memory)

        gate = CircuitBreakerRiskGate()
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(risk_gate=gate, order_gateway=engine, ledger=ledger_repo)
        pipeline = DecisionPipelineService(
            reasoner=EnterExitReasoner(),
            proposal_repository=proposal_repo,
            simulator=simulator,
            reflection=reflection,
        )

        engine.set_mark_price(100.0)
        opened = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        assert opened.result is SimulationResult.OPENED

        engine.set_mark_price(110.0)
        closed = pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 110.0)
        assert closed.result is SimulationResult.CLOSED

        episodes = memory.recall("btcusdt")
        assert len(episodes) == 1
        episode = episodes[0]
        assert episode.outcome.value == "win"
        assert episode.realized_pnl is not None
        assert episode.realized_pnl > 0
        assert episode.symbol == "btcusdt"

    def test_memory_is_idempotent_across_pipeline_runs(self, tmp_path):
        database = Database(tmp_path / "t2.db")
        proposal_repo = SqliteProposalRepository(database)
        ledger_repo = SqliteLedgerRepository(database)
        memory = SqliteMemoryRepository(database)
        reflection = build_reflection_service(tmp_path / "t2.db", memory_store=memory)

        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=CircuitBreakerRiskGate(), order_gateway=engine, ledger=ledger_repo
        )
        pipeline = DecisionPipelineService(
            reasoner=EnterExitReasoner(),
            proposal_repository=proposal_repo,
            simulator=simulator,
            reflection=reflection,
        )

        engine.set_mark_price(100.0)
        pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 100.0)
        engine.set_mark_price(110.0)
        pipeline.process(make_context(trend_direction="up", momentum_pct=0.2), 110.0)

        # Re-run reflection manually: idempotent, so no duplicate row appears.
        before = memory.count("btcusdt")
        assert before == 1
        reflection.reflect("btcusdt")
        assert memory.count("btcusdt") == before
