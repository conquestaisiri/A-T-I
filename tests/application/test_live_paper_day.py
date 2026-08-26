"""Tests for the live paper day-function adapter (WS2.2).

The adapter bridges the live decision path into the paper-campaign runner's
``day_fn`` contract: one decision per day, serialised under the operator lock,
with returns and order counts measured from the scoped account. It must be
safe against a missing context (flat day), never fabricate returns, and count
failed vs filled orders honestly.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from backend.application.research.live_paper_day import (
    LivePaperDecisionDayFn,
    build_live_day_fn,
)
from backend.application.simulation.paper_trading_simulator import (
    SimulationResult,
    SimulationStep,
)
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import RiskContext
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderSide, OrderStatus
from backend.domain.research.paper_campaign import PaperDayOutcome

T0 = "2026-08-13T00:00:00.000+00:00"


@dataclass
class FakeDecisionRunner:
    """Deterministic double for the pipeline's decision surface.

    The account is modelled as a simple equity counter; each ``process`` call
    either keeps it flat (no report) or applies ``day_return_pct`` and issues a
    report with the configured status. This makes return and order accounting
    fully deterministic.
    """

    equity: float = 100_000.0
    day_return_pct: float = 0.0
    report_status: OrderStatus | None = None
    lock_held: bool = False
    calls: int = 0
    _lock: threading.Lock | None = field(default=None, repr=False)

    def risk_snapshot(self) -> RiskContext:
        self.lock_held = self._lock is not None and self._lock.locked()
        return RiskContext(
            account_equity=self.equity,
            open_exposure_pct=0.0,
            daily_loss_pct=0.0,
            monthly_loss_pct=0.0,
            total_loss_pct=0.0,
            drawdown_pct=0.0,
            position_count=0,
            symbol_risk_used_pct=0.0,
            symbol_exposure_pct=0.0,
            portfolio_risk_used_pct=0.0,
        )

    def process(self, context: MarketContext, mark_price: float) -> SimulationStep:
        self.calls += 1
        self.lock_held = self._lock is not None and self._lock.locked()
        self.equity *= 1.0 + self.day_return_pct / 100.0
        report = None
        result = SimulationResult.NO_ACTION
        if self.report_status is not None:
            report = ExecutionReport(
                order_id=f"order-{self.calls}",
                symbol="BTC/USDT",
                side=OrderSide.BUY,
                quantity=1.0,
                average_fill_price=mark_price,
                status=self.report_status,
                executed_at=datetime.now(UTC),
            )
            result = SimulationResult.APPROVED
        return SimulationStep(
            proposal_id=f"prop-{self.calls}",
            result=result,
            risk_verdict="ok",
            report=report,
            position=None,
            record=None,
        )


@pytest.fixture
def runner() -> FakeDecisionRunner:
    return FakeDecisionRunner()


@pytest.fixture
def lock() -> threading.Lock:
    return threading.Lock()


def _ctx() -> MarketContext:
    from backend.domain.context.context_snapshot import ContextSnapshot

    snapshot = ContextSnapshot(
        events=(),
        start_timestamp=datetime.now(UTC),
        end_timestamp=datetime.now(UTC),
    )
    return MarketContext(snapshot=snapshot, features=(), created_at=datetime.now(UTC))


class TestFlatDay:
    def test_no_context_is_a_flat_day(self, runner: FakeDecisionRunner) -> None:
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: None,
        )
        outcome = day_fn(3)
        assert outcome == PaperDayOutcome(day=3)
        assert runner.calls == 0

    def test_zero_return_when_no_order(self, runner: FakeDecisionRunner) -> None:
        runner.day_return_pct = 0.0
        runner.report_status = None
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        outcome = day_fn(1)
        assert outcome.day == 1
        assert outcome.return_pct == pytest.approx(0.0)
        assert outcome.total_orders == 0
        assert outcome.failed_orders == 0


class TestReturnAccounting:
    def test_positive_day_return(self, runner: FakeDecisionRunner) -> None:
        runner.day_return_pct = 0.5
        runner.report_status = OrderStatus.FILLED
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        outcome = day_fn(2)
        assert outcome.return_pct == pytest.approx(0.5)
        assert outcome.total_orders == 1
        assert outcome.failed_orders == 0

    def test_negative_day_return(self, runner: FakeDecisionRunner) -> None:
        runner.day_return_pct = -1.25
        runner.report_status = OrderStatus.FILLED
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        outcome = day_fn(2)
        assert outcome.return_pct == pytest.approx(-1.25)

    def test_flat_baseline_never_fabricates(self, runner: FakeDecisionRunner) -> None:
        runner.equity = 0.0
        runner.day_return_pct = 100.0
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        assert day_fn(1).return_pct == 0.0


class TestOrderAccounting:
    def test_rejected_order_counts_failed(self, runner: FakeDecisionRunner) -> None:
        runner.report_status = OrderStatus.REJECTED
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        outcome = day_fn(4)
        assert outcome.total_orders == 1
        assert outcome.failed_orders == 1

    def test_expired_order_counts_failed(self, runner: FakeDecisionRunner) -> None:
        runner.report_status = OrderStatus.EXPIRED
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        assert day_fn(4).failed_orders == 1

    def test_cancelled_order_counts_failed(self, runner: FakeDecisionRunner) -> None:
        runner.report_status = OrderStatus.CANCELLED
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        assert day_fn(4).failed_orders == 1

    def test_filled_and_partial_do_not_count_failed(self, runner: FakeDecisionRunner) -> None:
        for status in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            runner.report_status = status
            day_fn = LivePaperDecisionDayFn(
                runner=runner,
                operator_lock=None,
                context_source=lambda day: (_ctx(), 50_000.0),
            )
            outcome = day_fn(4)
            assert outcome.failed_orders == 0
            assert outcome.total_orders == 1


class TestOperatorLock:
    def test_decision_runs_under_lock(
        self, runner: FakeDecisionRunner, lock: threading.Lock
    ) -> None:
        runner._lock = lock
        runner.day_return_pct = 0.5
        runner.report_status = OrderStatus.FILLED
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=lock,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        outcome = day_fn(1)
        assert outcome.return_pct == pytest.approx(0.5)
        assert runner.lock_held is True

    def test_no_lock_runs_without_it(self, runner: FakeDecisionRunner) -> None:
        runner.day_return_pct = 0.5
        runner.report_status = OrderStatus.FILLED
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=None,
            context_source=lambda day: (_ctx(), 50_000.0),
        )
        day_fn(1)
        assert runner.lock_held is False

    def test_lock_serialises_flat_day_too(self, runner: FakeDecisionRunner) -> None:
        day_fn = LivePaperDecisionDayFn(
            runner=runner,
            operator_lock=threading.Lock(),
            context_source=lambda day: None,
        )
        assert day_fn(1) == PaperDayOutcome(day=1)


class TestBuildLiveDayFn:
    def test_builds_scoped_day_fn(self, tmp_path: Path) -> None:
        """Compose the adapter from real live components end to end."""
        from datetime import UTC, datetime

        from backend.application.decision.rule_based_solver import RuleBasedSolver
        from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
        from backend.application.simulation.paper_fill_engine import PaperFillEngine
        from backend.domain.context.context_snapshot import ContextSnapshot
        from backend.domain.observation.event import (
            ObservationEvent,
            ObservationEventType,
        )
        from backend.infrastructure.sqlite.database import Database
        from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
        from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository

        db = Database(tmp_path / "live_day.db")
        gate = CircuitBreakerRiskGate()
        fill_engine = PaperFillEngine()
        ledger = SqliteLedgerRepository(db)
        proposals = SqliteProposalRepository(db)
        solver = RuleBasedSolver()

        event = ObservationEvent(
            source_id="test",
            source_name="test",
            event_type=ObservationEventType.TICKER,
            timestamp=datetime.now(UTC),
            payload={"symbol": "BTC/USDT", "last": 50_000.0},
        )
        snapshot = ContextSnapshot.from_events((event,))
        context = MarketContext(snapshot=snapshot, features=(), created_at=event.timestamp)

        day_fn = build_live_day_fn(
            reasoner=solver,
            proposal_repository=proposals,
            risk_gate=gate,
            order_gateway=fill_engine,
            ledger=ledger,
            supervisor=None,
            operator_lock=threading.Lock(),
            context_source=lambda day: (context, 50_000.0),
        )
        outcome = day_fn(1)
        assert outcome.day == 1
        # A cold account with no tradeable context features yields a flat day
        # or a decision depending on the solver's reading; either way the
        # adapter returns a well-formed outcome and the ledger survived it.
        assert isinstance(outcome, PaperDayOutcome)
        assert outcome.total_orders in (0, 1)
        assert outcome.failed_orders <= outcome.total_orders
        db.close()
