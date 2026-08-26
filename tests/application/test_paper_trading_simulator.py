"""Unit tests for the deterministic paper-trading simulator."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import (
    PaperTradingSimulator,
    SimulationResult,
)
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.decision.trade_plan import PreTradePlan, StopLevel
from backend.domain.execution.order import OrderSide
from backend.domain.execution.trade_record import TradeRecord, TradeStatus


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_proposal(
    proposal_id: str = "prop-1",
    correlation_id: str = "corr-1",
    symbol: str = "btcusdt",
    created_at: datetime | None = None,
    actions: tuple[ProposedAction, ...] | None = None,
    **overrides: Any,
) -> DecisionProposal:
    params: dict[str, Any] = dict(
        proposal_id=proposal_id,
        correlation_id=correlation_id,
        created_at=created_at or ts(),
        symbol=symbol,
        hypothesis=Hypothesis(
            statement="trend",
            supporting_evidence=(EvidenceItem(source="trend", summary="up", value=1.0),),
            opposing_evidence=(),
        ),
        confidence=0.8,
        uncertainty="none",
        actions=actions
        or (
            ProposedAction(
                action_type=ProposedActionType.ENTER_LONG,
                size_fraction=0.10,
                order=1,
                rationale="go",
            ),
        ),
        risk_context=RiskContext(
            account_equity=100_000.0,
            open_exposure_pct=0.0,
            daily_loss_pct=0.0,
            monthly_loss_pct=0.0,
            total_loss_pct=0.0,
            drawdown_pct=0.0,
            position_count=0,
        ),
        alternatives=(),
        rationale="go",
        pre_trade_plan=PreTradePlan(
            stop_loss=StopLevel(distance_pct=0.05),
            take_profit=StopLevel(distance_pct=0.10),
            risk_per_trade_pct=0.02,
            risk_reward_ratio=2.0,
        ),
    )
    params.update(overrides)
    return DecisionProposal(**params)


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

    def all_trades(self) -> list[TradeRecord]:
        return list(self._records.values())


SimFixture = tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]


@pytest.fixture
def sim() -> tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]:
    ledger = InMemoryLedger()
    engine = PaperFillEngine()
    gate = CircuitBreakerRiskGate()
    return (
        PaperTradingSimulator(risk_gate=gate, order_gateway=engine, ledger=ledger),
        ledger,
        engine,
    )


class TestLifecycle:
    def test_open_then_close_writes_ledger(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)

        opened = simulator.process(make_proposal(), mark_price=100.0)
        assert opened.result is SimulationResult.OPENED
        assert opened.record is not None
        assert opened.record.status is TradeStatus.OPEN
        assert "btcusdt" in simulator.positions

        engine.set_mark_price(110.0)
        close_proposal = make_proposal(
            proposal_id="prop-2",
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.EXIT,
                    size_fraction=1.0,
                    order=1,
                    rationale="take profit",
                ),
            ),
        )
        closed = simulator.process(close_proposal, mark_price=110.0)
        assert closed.result is SimulationResult.CLOSED
        assert closed.record is not None
        assert closed.record.status is TradeStatus.CLOSED
        assert "btcusdt" not in simulator.positions

        [trade] = ledger.all_trades()
        # With spread: buy at ~100.01, sell at ~109.99
        # PnL ≈ (109.99 - 100.01) * quantity ≈ 9.98 * quantity
        assert trade.realized_pnl == pytest.approx(9.98 * trade.quantity, rel=0.01)

    def test_profit_updates_equity(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)
        simulator.process(make_proposal(), mark_price=100.0)
        initial_equity = simulator.equity

        engine.set_mark_price(110.0)
        simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="exit",
                    ),
                ),
            ),
            mark_price=110.0,
        )
        assert simulator.equity > initial_equity

    def test_loss_updates_equity_down(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)
        simulator.process(make_proposal(), mark_price=100.0)
        initial_equity = simulator.equity

        engine.set_mark_price(90.0)
        simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="stop",
                    ),
                ),
            ),
            mark_price=90.0,
        )
        assert simulator.equity < initial_equity

    def test_short_profit(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)
        short_prop = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.ENTER_SHORT,
                    size_fraction=0.10,
                    order=1,
                    rationale="short",
                ),
            ),
        )
        simulator.process(short_prop, mark_price=100.0)

        engine.set_mark_price(90.0)
        simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="cover",
                    ),
                ),
            ),
            mark_price=90.0,
        )
        assert simulator.equity > 100_000.0


class TestRejection:
    def test_circuit_breaker_rejects_and_does_not_trade(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)
        blocked = make_proposal(
            proposal_id="prop-b",
            risk_context=RiskContext(
                account_equity=100_000.0,
                open_exposure_pct=0.0,
                daily_loss_pct=0.06,
                monthly_loss_pct=0.0,
                total_loss_pct=0.0,
                drawdown_pct=0.0,
                position_count=0,
            ),
        )
        step = simulator.process(blocked, mark_price=100.0)
        assert step.result is SimulationResult.REJECTED
        assert step.record is None
        assert step.report is None
        assert ledger.count() == 0

    def test_stand_aside_produces_no_action(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)
        step = simulator.process(
            make_proposal(
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.STAND_ASIDE,
                        size_fraction=0.5,
                        order=1,
                        rationale="wait",
                    ),
                ),
            ),
            mark_price=100.0,
        )
        assert step.result is SimulationResult.NO_ACTION


class TestPositionHandling:
    def test_second_open_position_is_no_action(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)
        simulator.process(make_proposal(), mark_price=100.0)

        step = simulator.process(
            make_proposal(proposal_id="prop-2"),
            mark_price=100.0,
        )
        assert step.result is SimulationResult.NO_ACTION

    def test_exit_without_position_is_no_action(self, sim: SimFixture) -> None:
        simulator, ledger, engine = sim
        engine.set_mark_price(100.0)
        step = simulator.process(
            make_proposal(
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="exit",
                    ),
                ),
            ),
            mark_price=100.0,
        )
        assert step.result is SimulationResult.NO_ACTION


class TestRiskSnapshot:
    def test_initial_risk_snapshot(self, sim: SimFixture) -> None:
        simulator, _, _ = sim
        risk = simulator.risk_snapshot()
        assert risk.account_equity == 100_000.0
        assert risk.open_exposure_pct == 0.0
        assert risk.daily_loss_pct == 0.0
        assert risk.total_loss_pct == 0.0
        assert risk.drawdown_pct == 0.0
        assert risk.position_count == 0

    def test_open_position_raises_exposure(self, sim: SimFixture) -> None:
        simulator, _, engine = sim
        engine.set_mark_price(100.0)
        simulator.process(make_proposal(), mark_price=100.0)

        risk = simulator.risk_snapshot()
        assert risk.position_count == 1
        assert risk.open_exposure_pct > 0.0

    def test_loss_after_close_shows_in_risk(self, sim: SimFixture) -> None:
        simulator, _, engine = sim
        engine.set_mark_price(100.0)
        simulator.process(make_proposal(), mark_price=100.0)
        engine.set_mark_price(90.0)
        simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="stop",
                    ),
                ),
            ),
            mark_price=90.0,
        )

        risk = simulator.risk_snapshot()
        assert risk.total_loss_pct > 0.0
        assert risk.daily_loss_pct > 0.0
        assert risk.position_count == 0

    def test_short_unrealized_pnl_profits_when_mark_falls(self, sim: SimFixture) -> None:
        """Unrealized PnL for a short is signed correctly (P0-009)."""
        simulator, _, engine = sim
        engine.set_mark_price(100.0)
        simulator.process(
            make_proposal(
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.ENTER_SHORT,
                        size_fraction=0.10,
                        order=1,
                        rationale="short",
                    ),
                ),
            ),
            mark_price=100.0,
        )
        position = simulator.positions["btcusdt"]
        assert position.side is OrderSide.SELL

        # Mark falls below entry -> equity grows: short unrealized is
        # direction * (mark - entry) * qty = -(mark - entry) * qty.
        engine.set_mark_price(98.0)
        risk = simulator.risk_snapshot(mark_price=98.0)
        assert risk.account_equity > 100_000.0
        expected = -1.0 * (98.0 - position.average_entry_price) * position.quantity
        assert risk.account_equity == pytest.approx(100_000.0 + expected)

    def test_short_unrealized_pnl_loses_when_mark_rises(self, sim: SimFixture) -> None:
        """Unrealized PnL for a short is negative when the mark rises (P0-009)."""
        simulator, _, engine = sim
        engine.set_mark_price(100.0)
        simulator.process(
            make_proposal(
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.ENTER_SHORT,
                        size_fraction=0.10,
                        order=1,
                        rationale="short",
                    ),
                ),
            ),
            mark_price=100.0,
        )
        position = simulator.positions["btcusdt"]

        engine.set_mark_price(102.0)
        risk = simulator.risk_snapshot(mark_price=102.0)
        assert risk.account_equity < 100_000.0
        expected = -1.0 * (102.0 - position.average_entry_price) * position.quantity
        assert risk.account_equity == pytest.approx(100_000.0 + expected)


class TestDeterminism:
    def test_same_replay_produces_identical_ledger(self) -> None:
        def run_replay() -> tuple[InMemoryLedger, float]:
            ledger = InMemoryLedger()
            engine = PaperFillEngine()
            gate = CircuitBreakerRiskGate()
            simulator = PaperTradingSimulator(risk_gate=gate, order_gateway=engine, ledger=ledger)

            engine.set_mark_price(100.0)
            simulator.process(make_proposal(), mark_price=100.0)
            engine.set_mark_price(110.0)
            simulator.process(
                make_proposal(
                    proposal_id="prop-2",
                    actions=(
                        ProposedAction(
                            action_type=ProposedActionType.EXIT,
                            size_fraction=1.0,
                            order=1,
                            rationale="exit",
                        ),
                    ),
                ),
                mark_price=110.0,
            )
            return ledger, simulator.equity

        first_ledger, first_equity = run_replay()
        second_ledger, second_equity = run_replay()
        assert first_equity == second_equity
        assert all(
            a == b
            for a, b in zip(first_ledger.all_trades(), second_ledger.all_trades(), strict=True)
        )

    def test_risk_reset_uses_event_time_not_wall_clock(self) -> None:
        """Daily/monthly PnL windows reset on proposal timestamps, not the clock."""
        ledger = InMemoryLedger()
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=CircuitBreakerRiskGate(),
            order_gateway=engine,
            ledger=ledger,
        )
        day1 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        day2 = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)

        # Loss on day 1.
        engine.set_mark_price(100.0)
        simulator.process(make_proposal(created_at=day1), mark_price=100.0)
        engine.set_mark_price(90.0)
        simulator.process(
            make_proposal(
                proposal_id="close-loss",
                created_at=day1,
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="stop",
                    ),
                ),
            ),
            mark_price=90.0,
        )
        risk = simulator.risk_snapshot(now=day1)
        assert risk.daily_loss_pct > 0.0
        assert risk.monthly_loss_pct > 0.0

        # A new-day event resets the daily window but not the monthly one.
        simulator.process(
            make_proposal(
                proposal_id="stand-aside-day2",
                created_at=day2,
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.STAND_ASIDE,
                        size_fraction=0.5,
                        order=1,
                        rationale="wait",
                    ),
                ),
            ),
            mark_price=90.0,
        )
        risk = simulator.risk_snapshot(now=day2)
        assert risk.daily_loss_pct == 0.0
        assert risk.monthly_loss_pct > 0.0

    def test_risk_reset_uses_event_time_for_month(self) -> None:
        ledger = InMemoryLedger()
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=CircuitBreakerRiskGate(),
            order_gateway=engine,
            ledger=ledger,
        )
        jan = datetime(2026, 1, 25, 12, 0, tzinfo=UTC)
        feb = datetime(2026, 2, 1, 12, 0, tzinfo=UTC)

        engine.set_mark_price(100.0)
        simulator.process(make_proposal(created_at=jan), mark_price=100.0)
        engine.set_mark_price(99.0)
        simulator.process(
            make_proposal(
                proposal_id="close-loss",
                created_at=jan,
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="stop",
                    ),
                ),
            ),
            mark_price=99.0,
        )
        assert simulator.risk_snapshot(now=jan).monthly_loss_pct > 0.0

        # A next-month event resets both the daily and the monthly window.
        simulator.process(
            make_proposal(
                proposal_id="stand-aside-feb",
                created_at=feb,
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.STAND_ASIDE,
                        size_fraction=0.5,
                        order=1,
                        rationale="wait",
                    ),
                ),
            ),
            mark_price=99.0,
        )
        risk = simulator.risk_snapshot(now=feb)
        assert risk.daily_loss_pct == 0.0
        assert risk.monthly_loss_pct == 0.0

    def test_injected_now_overrides_event_time_for_reset(self) -> None:
        ledger = InMemoryLedger()
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=CircuitBreakerRiskGate(),
            order_gateway=engine,
            ledger=ledger,
        )
        day1 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)

        engine.set_mark_price(100.0)
        simulator.process(make_proposal(created_at=day1), mark_price=100.0)
        engine.set_mark_price(90.0)
        simulator.process(
            make_proposal(
                proposal_id="close-loss",
                created_at=day1,
                actions=(
                    ProposedAction(
                        action_type=ProposedActionType.EXIT,
                        size_fraction=1.0,
                        order=1,
                        rationale="stop",
                    ),
                ),
            ),
            mark_price=90.0,
        )

        # Same-day injection preserves the loss.
        assert simulator.risk_snapshot(now=day1).daily_loss_pct > 0.0
        # A later injected boundary resets the daily window deterministically.
        day2 = datetime(2026, 1, 16, 12, 0, tzinfo=UTC)
        assert simulator.risk_snapshot(now=day2).daily_loss_pct == 0.0

    def test_replay_is_identical_when_event_time_is_deterministic(self) -> None:
        """Two replays with identical event timestamps produce identical risk snapshots."""

        def run_replay() -> list[dict[str, Any]]:
            ledger = InMemoryLedger()
            engine = PaperFillEngine()
            simulator = PaperTradingSimulator(
                risk_gate=CircuitBreakerRiskGate(),
                order_gateway=engine,
                ledger=ledger,
            )
            day1 = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
            day1_noon = datetime(2026, 1, 15, 12, 15, tzinfo=UTC)

            engine.set_mark_price(100.0)
            simulator.process(make_proposal(created_at=day1), mark_price=100.0)
            snapshots = [simulator.risk_snapshot(now=day1).as_dict()]
            engine.set_mark_price(105.0)
            simulator.process(
                make_proposal(
                    proposal_id="close-profit",
                    created_at=day1_noon,
                    actions=(
                        ProposedAction(
                            action_type=ProposedActionType.EXIT,
                            size_fraction=1.0,
                            order=1,
                            rationale="profit",
                        ),
                    ),
                ),
                mark_price=105.0,
            )
            snapshots.append(simulator.risk_snapshot(now=day1_noon).as_dict())
            return snapshots

        assert run_replay() == run_replay()
