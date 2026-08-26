"""Tests for the backtest replay layer (deterministic campaigns)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.application.backtest.backtest_runner import BacktestRunner
from backend.application.backtest.report import BacktestReport, ReplayStep
from backend.application.context.bootstrap import build_backtest_runner, build_replay_steps
from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.interfaces.proposal_repository import ProposalRepository
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
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def ts() -> datetime:
    return datetime(2026, 2, 10, 8, 0, 0, tzinfo=UTC)


def make_context(symbol: str = "btcusdt", price: float = 100.0) -> MarketContext:
    event = ObservationEvent(
        source_id="backtest",
        source_name="Backtest",
        event_type=ObservationEventType.TRADE,
        timestamp=ts(),
        payload={"symbol": symbol, "trade_id": 1, "price": price, "quantity": 1.0},
    )
    snapshot = ContextSnapshot.from_events((event,))
    features = (
        (
            "trend",
            ContextFeature(
                name="trend",
                value={"direction": "up", "change_pct": 1.0},
                computation_timestamp=ts(),
                execution_time=0.0,
            ),
        ),
        (
            "momentum",
            ContextFeature(
                name="momentum",
                value={"rate_of_change_pct": 1.0},
                computation_timestamp=ts(),
                execution_time=0.0,
            ),
        ),
    )
    return MarketContext(snapshot=snapshot, features=features, created_at=ts())


class StubReasoner(AIReasoner):
    """Reasoner that replays a fixed script of actions (enter, then exit)."""

    def __init__(self, symbol: str = "btcusdt") -> None:
        self._symbol = symbol
        self._step = 0

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        self._step += 1
        action_type = ProposedActionType.ENTER_LONG if self._step == 1 else ProposedActionType.EXIT
        pre_trade_plan = (
            PreTradePlan(
                stop_loss=StopLevel(distance_pct=0.10),
                take_profit=StopLevel(distance_pct=0.20),
                risk_per_trade_pct=0.02,
                risk_reward_ratio=2.0,
            )
            if action_type is ProposedActionType.ENTER_LONG
            else None
        )
        return DecisionProposal(
            proposal_id=f"stub-{self._step}",
            correlation_id=self._symbol,
            created_at=context.created_at,
            symbol=self._symbol,
            hypothesis=Hypothesis(
                statement="stub",
                supporting_evidence=(EvidenceItem(source="stub", summary="s", value=1.0),),
                opposing_evidence=(),
            ),
            confidence=0.8,
            uncertainty="none",
            actions=(
                ProposedAction(
                    action_type=action_type,
                    size_fraction=0.10,
                    order=1,
                    rationale="stub",
                ),
            ),
            risk_context=risk_context,
            alternatives=(),
            rationale="stub",
            pre_trade_plan=pre_trade_plan,
        )


class TestBacktestRunner:
    def test_replays_enter_then_exit_into_a_report(self):
        pipeline, simulator, fill_engine = _build_pipeline()
        runner = BacktestRunner(pipeline, simulator, fill_engine, symbol="btcusdt")

        report = runner.run(
            [
                ReplayStep(make_context(price=100.0), 100.0),
                ReplayStep(make_context(price=110.0), 110.0),
            ]
        )

        assert isinstance(report, BacktestReport)
        assert report.steps == 2
        assert report.trades_opened == 1
        assert report.trades_closed == 1
        assert report.wins == 1
        assert report.losses == 0
        assert report.flats == 0
        assert report.approved == 2
        assert report.rejected == 0
        assert report.final_equity > report.starting_equity
        assert report.win_rate == 1.0

    def test_counts_losses_and_drawdown(self):
        pipeline, simulator, fill_engine = _build_pipeline()
        runner = BacktestRunner(pipeline, simulator, fill_engine, symbol="btcusdt")

        report = runner.run(
            [
                ReplayStep(make_context(price=100.0), 100.0),
                ReplayStep(make_context(price=90.0), 90.0),
            ]
        )

        assert report.trades_closed == 1
        assert report.losses == 1
        assert report.wins == 0
        assert report.final_equity < report.starting_equity
        assert report.win_rate == 0.0
        assert report.max_drawdown_pct > 0.0

    def test_rejects_mismatched_symbol_steps(self):
        pipeline, simulator, fill_engine = _build_pipeline()
        runner = BacktestRunner(pipeline, simulator, fill_engine, symbol="btcusdt")

        try:
            runner.run([ReplayStep(make_context(symbol="ethusdt"), 100.0)])
        except ValueError:
            return
        raise AssertionError("expected ValueError for symbol mismatch")

    def test_requires_at_least_one_step(self):
        pipeline, simulator, fill_engine = _build_pipeline()
        runner = BacktestRunner(pipeline, simulator, fill_engine, symbol="btcusdt")

        try:
            runner.run([])
        except ValueError:
            return
        raise AssertionError("expected ValueError for empty steps")


class TestBootstrapBacktest:
    def test_builds_rule_solver_runner(self, tmp_path):
        runner = build_backtest_runner(tmp_path / "bt.db", symbol="btcusdt")
        assert isinstance(runner, BacktestRunner)

    def test_replay_steps_from_events(self, tmp_path):
        events = [
            ObservationEvent(
                source_id="backtest",
                source_name="Backtest",
                event_type=ObservationEventType.TRADE,
                timestamp=ts(),
                payload={"symbol": "btcusdt", "trade_id": 1, "price": 100.0, "quantity": 1.0},
            ),
            ObservationEvent(
                source_id="backtest",
                source_name="Backtest",
                event_type=ObservationEventType.TRADE,
                timestamp=ts(),
                payload={"symbol": "btcusdt", "trade_id": 2, "price": 101.0, "quantity": 1.0},
            ),
        ]
        steps, symbol = build_replay_steps(events)
        assert symbol == "btcusdt"
        assert len(steps) == 2
        assert steps[0].mark_price == 100.0
        assert steps[1].mark_price == 101.0
        assert steps[0].context.snapshot.symbol == "btcusdt"

    def test_replay_determinism_identical_equity_curve(self, tmp_path):
        """T1-2-1: ADR 0007 regression guard.

        The same event feed replayed through two completely fresh decision
        pipelines (separate databases) must produce byte-identical equity
        curves. Replay is a pure function of the events; nothing about the
        clock or the machine may leak in.
        """
        events = [
            ObservationEvent(
                source_id="backtest",
                source_name="Backtest",
                event_type=ObservationEventType.TRADE,
                timestamp=ts() + timedelta(seconds=i),
                payload={"symbol": "btcusdt", "trade_id": i, "price": price, "quantity": 1.0},
            )
            for i, price in enumerate(
                (100.0 + 0.5 * (i + 1) for i in range(40)),
                start=1,
            )
        ]
        steps, symbol = build_replay_steps(events)
        assert symbol == "btcusdt"

        runner_a = build_backtest_runner(tmp_path / "a.db", symbol="btcusdt")
        runner_b = build_backtest_runner(tmp_path / "b.db", symbol="btcusdt")
        report_a = runner_a.run(steps)
        report_b = runner_b.run(steps)

        assert report_a.equity_curve == report_b.equity_curve
        assert len(report_a.equity_curve) == len(steps) + 1  # starting value + one per step
        assert report_a.final_equity == report_b.final_equity
        assert report_a.total_pnl == report_b.total_pnl
        assert report_a.returns_pct == report_b.returns_pct
        assert report_a.trades_opened == report_b.trades_opened
        assert report_a.trades_closed == report_b.trades_closed
        # The curve must actually move (the guard proves something real).
        assert len(set(report_a.equity_curve)) > 1


def _build_ledger():
    from backend.application.interfaces.ledger_repository import LedgerRepository
    from backend.domain.execution.trade_record import TradeRecord, TradeStatus

    class InMemoryLedger(LedgerRepository):
        def __init__(self) -> None:
            self._records: dict[str, TradeRecord] = {}
            self.by_symbol: dict[str, list[TradeRecord]] = {}

        def save(self, record: TradeRecord) -> None:
            self._records[record.trade_id] = record
            self.by_symbol.setdefault(record.symbol, []).append(record)

        def find_by_id(self, trade_id: str) -> TradeRecord | None:
            return self._records.get(trade_id)

        def find_recent(self, symbol: str, limit: int = 20) -> list[TradeRecord]:
            return self.by_symbol.get(symbol, [])[-limit:]

        def open_trades(self) -> list[TradeRecord]:
            return [r for r in self._records.values() if r.status is TradeStatus.OPEN]

        def closed_trades(self, limit: int = 100) -> list[TradeRecord]:
            if limit <= 0:
                raise ValueError("limit must be a positive integer")
            return [r for r in self._records.values() if r.status is TradeStatus.CLOSED][-limit:]

        def count(self, symbol: str | None = None) -> int:
            if symbol is None:
                return len(self._records)
            return len(self.by_symbol.get(symbol, []))

    return InMemoryLedger()


def _build_pipeline():
    from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
    from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
    from backend.application.simulation.paper_fill_engine import PaperFillEngine
    from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator

    ledger = _build_ledger()
    gate = CircuitBreakerRiskGate()
    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(risk_gate=gate, order_gateway=fill_engine, ledger=ledger)
    pipeline = DecisionPipelineService(
        reasoner=StubReasoner(),
        proposal_repository=_ProposalRepo(),
        simulator=simulator,
    )
    return pipeline, simulator, fill_engine


class _ProposalRepo(ProposalRepository):
    """Minimal proposal repository supporting the pipeline's at-least-once save."""

    def __init__(self) -> None:
        self._proposals: dict[str, DecisionProposal] = {}

    def save(self, proposal: DecisionProposal) -> None:
        self._proposals[proposal.proposal_id] = proposal

    def find_by_id(self, proposal_id: str) -> DecisionProposal | None:
        return self._proposals.get(proposal_id)

    def find_recent(self, symbol: str, limit: int = 20) -> list[DecisionProposal]:
        return [p for p in self._proposals.values() if p.symbol == symbol][-limit:]

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            return len(self._proposals)
        return sum(1 for p in self._proposals.values() if p.symbol == symbol)
