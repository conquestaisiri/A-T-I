"""Tests for the deterministic perpetual-swaps funding model.

STAGE 3 item #26 acceptance:
- Funding is a distinct cost stream from execution fees.
- Charged periodically on position notional at UTC boundaries (default 8h).
- Longs pay a positive rate, shorts receive (signed cost).
- Deterministic: a function only of timestamps, quantity and rate.
- Flows into equity and the daily/monthly loss windows.
- Unmodeled until a FundingConfig is supplied (cost stays None).
- Attribution identity net = gross - fees - funding holds exactly.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.decision.trade_plan import PreTradePlan, StopLevel
from backend.domain.execution.funding import (
    FundingConfig,
    funding_cost_for,
    funding_intervals,
)
from backend.domain.execution.order import OrderSide
from backend.domain.execution.trade_record import TradeRecord, TradeStatus

START = datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)
EPOCH = datetime(2026, 1, 15, 0, 0, 0, tzinfo=UTC)
RATE = 0.0001


def hours(n: float) -> timedelta:
    return timedelta(hours=n)


def make_proposal(
    proposal_id: str,
    created_at: datetime,
    actions: tuple[ProposedAction, ...],
) -> DecisionProposal:
    return DecisionProposal(
        proposal_id=proposal_id,
        correlation_id=proposal_id,
        created_at=created_at,
        symbol="btcusdt",
        hypothesis=Hypothesis(
            statement="trend",
            supporting_evidence=(EvidenceItem(source="trend", summary="up", value=1.0),),
            opposing_evidence=(),
        ),
        confidence=0.8,
        uncertainty="none",
        actions=actions,
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


def path(action: ProposedActionType, size_fraction: float = 1.0) -> tuple[ProposedAction, ...]:
    return (
        ProposedAction(
            action_type=action,
            size_fraction=size_fraction,
            order=1,
            rationale="step",
        ),
    )


class InMemoryLedger(LedgerRepository):
    def __init__(self) -> None:
        self._records: dict[str, TradeRecord] = {}

    def save(self, record: TradeRecord) -> None:
        self._records[record.trade_id] = record

    def find_by_id(self, trade_id: str) -> TradeRecord | None:
        return self._records.get(trade_id)

    def find_recent(self, symbol: str, limit: int = 20) -> list[TradeRecord]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        return list(self._records.values())[-limit:]

    def open_trades(self) -> list[TradeRecord]:
        return [r for r in self._records.values() if r.status is TradeStatus.OPEN]

    def closed_trades(self, limit: int = 100) -> list[TradeRecord]:
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        return [r for r in self._records.values() if r.status is TradeStatus.CLOSED][-limit:]

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            return len(self._records)
        return sum(1 for r in self._records.values() if r.symbol == symbol)


def build_simulator(
    funding_config: FundingConfig | None,
) -> tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]:
    ledger = InMemoryLedger()
    engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=CircuitBreakerRiskGate(),
        order_gateway=engine,
        ledger=ledger,
        funding_config=funding_config,
    )
    return simulator, ledger, engine


def step(
    simulator: PaperTradingSimulator,
    engine: PaperFillEngine,
    proposal: DecisionProposal,
    mark: float,
):
    engine.set_mark_price(mark)
    return simulator.process(proposal, mark_price=mark)


@pytest.fixture
def config() -> FundingConfig:
    return FundingConfig(rate=RATE, interval_hours=8.0, epoch=EPOCH)


class TestFundingConfig:
    def test_default_epoch_is_utc_midnight_grid(self) -> None:
        config = FundingConfig(rate=RATE)
        assert config.interval_hours == 8.0
        assert config.epoch.tzinfo is not None

    def test_rejects_non_positive_interval(self) -> None:
        with pytest.raises(ValueError):
            FundingConfig(rate=RATE, interval_hours=0.0)
        with pytest.raises(ValueError):
            FundingConfig(rate=RATE, interval_hours=-1.0)

    def test_rejects_non_finite_rate(self) -> None:
        with pytest.raises(ValueError):
            FundingConfig(rate=float("nan"))
        with pytest.raises(ValueError):
            FundingConfig(rate=float("inf"))

    def test_rejects_naive_epoch(self) -> None:
        with pytest.raises(ValueError):
            FundingConfig(rate=RATE, epoch=datetime(2026, 1, 15))

    def test_negative_rate_is_legal(self) -> None:
        FundingConfig(rate=-RATE)


class TestFundingIntervals:
    def test_no_interval_crossed_within_one_period(self, config: FundingConfig) -> None:
        # 12:00 -> 15:00 crosses no boundary on the 00/08/16 grid.
        opened = START
        closed = START + timedelta(hours=3)
        assert funding_intervals(opened, closed, config) == 0

    def test_boundary_at_close_is_charged(self, config: FundingConfig) -> None:
        # 12:00 -> 20:00 crosses the 16:00 boundary.
        assert funding_intervals(START, START + hours(8), config) == 1

    def test_boundary_at_open_is_not_charged(self, config: FundingConfig) -> None:
        # Open exactly on a boundary (16:00 UTC grid): the boundary at open is
        # not held over, so one full period later charges one payment.
        opened = EPOCH + hours(16)
        closed = opened + hours(8)
        assert funding_intervals(opened, closed, config) == 1

    def test_multiple_boundaries_crossed(self, config: FundingConfig) -> None:
        # 12:00 -> next-day 13:00 crosses 16:00, 00:00 and 08:00.
        assert funding_intervals(START, START + hours(25), config) == 3

    def test_backwards_window_yields_zero(self, config: FundingConfig) -> None:
        assert funding_intervals(START + hours(4), START, config) == 0


class TestFundingCost:
    def test_long_pays_positive_rate(self, config: FundingConfig) -> None:
        cost = funding_cost_for(OrderSide.BUY, 10.0, 100.0, START, START + hours(8), config)
        assert cost == pytest.approx(0.0001 * 100.0 * 10.0)

    def test_short_receives_positive_rate(self, config: FundingConfig) -> None:
        cost = funding_cost_for(OrderSide.SELL, 10.0, 100.0, START, START + hours(8), config)
        assert cost == pytest.approx(-0.0001 * 100.0 * 10.0)

    def test_negative_rate_flips_the_payer(self, config: FundingConfig) -> None:
        config = FundingConfig(rate=-RATE, interval_hours=8.0, epoch=EPOCH)
        assert funding_cost_for(OrderSide.BUY, 10.0, 100.0, START, START + hours(8), config) < 0
        assert funding_cost_for(OrderSide.SELL, 10.0, 100.0, START, START + hours(8), config) > 0

    def test_accumulates_over_intervals(self, config: FundingConfig) -> None:
        cost = funding_cost_for(OrderSide.BUY, 10.0, 100.0, START, START + hours(25), config)
        assert cost == pytest.approx(3 * 0.0001 * 100.0 * 10.0)

    def test_zero_quantity_charges_nothing(self, config: FundingConfig) -> None:
        assert funding_cost_for(OrderSide.BUY, 0.0, 100.0, START, START + hours(8), config) == 0.0

    def test_naive_timestamps_rejected(self, config: FundingConfig) -> None:
        with pytest.raises(ValueError):
            funding_cost_for(
                OrderSide.BUY,
                10.0,
                100.0,
                datetime(2026, 1, 15, 12),
                datetime(2026, 1, 15, 20),
                config,
            )


class TestSimulatorFunding:
    def test_unmodeled_funding_stays_none(self) -> None:
        simulator, ledger, engine = build_simulator(funding_config=None)
        opened = step(
            simulator,
            engine,
            make_proposal("prop-1", START, path(ProposedActionType.ENTER_LONG)),
            100.0,
        )
        assert opened.record is not None
        assert opened.record.funding_cost is None

        step(
            simulator,
            engine,
            make_proposal("prop-2", START + hours(16), path(ProposedActionType.EXIT)),
            110.0,
        )
        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.funding_cost is None

    def test_long_round_trip_charges_funding(self, config: FundingConfig) -> None:
        simulator, ledger, engine = build_simulator(funding_config=config)
        opened = step(
            simulator,
            engine,
            make_proposal("prop-1", START, path(ProposedActionType.ENTER_LONG)),
            100.0,
        )
        assert opened.record is not None
        # Held across 2 funding boundaries (16:00, next-day 00:00).
        step(
            simulator,
            engine,
            make_proposal("prop-2", START + hours(16), path(ProposedActionType.EXIT)),
            110.0,
        )
        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.funding_cost is not None
        assert closed.gross_pnl is not None
        assert closed.realized_pnl is not None

        entry = opened.record.entry_price
        quantity = opened.record.quantity
        funding = RATE * entry * quantity * 2
        assert closed.funding_cost == pytest.approx(funding)
        # No fees (default config): net = gross - funding exactly.
        assert closed.realized_pnl == pytest.approx(closed.gross_pnl - funding)
        assert closed.realized_pnl == pytest.approx(
            closed.gross_pnl - (closed.fee or 0.0) - closed.funding_cost
        )
        assert simulator.equity == pytest.approx(100_000.0 + closed.realized_pnl)

    def test_short_receives_funding_credit(self, config: FundingConfig) -> None:
        simulator, ledger, engine = build_simulator(funding_config=config)
        opened = step(
            simulator,
            engine,
            make_proposal("prop-1", START, path(ProposedActionType.ENTER_SHORT)),
            100.0,
        )
        assert opened.record is not None
        # Down move: short profits; positive funding is a credit on top.
        step(
            simulator,
            engine,
            make_proposal("prop-2", START + hours(8), path(ProposedActionType.EXIT)),
            90.0,
        )
        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.funding_cost is not None
        assert closed.funding_cost == pytest.approx(
            -RATE * opened.record.entry_price * opened.record.quantity
        )
        assert closed.funding_cost < 0.0

    def test_no_funding_when_held_under_one_interval(self, config: FundingConfig) -> None:
        simulator, ledger, engine = build_simulator(funding_config=config)
        opened = step(
            simulator,
            engine,
            make_proposal("prop-1", START, path(ProposedActionType.ENTER_LONG)),
            100.0,
        )
        assert opened.record is not None
        step(
            simulator,
            engine,
            make_proposal("prop-2", START + hours(2), path(ProposedActionType.EXIT)),
            110.0,
        )
        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.funding_cost == pytest.approx(0.0)

    def test_bracket_exit_charges_funding(self, config: FundingConfig) -> None:
        simulator, ledger, engine = build_simulator(funding_config=config)
        # Open a long with a bracket plan whose stop lies 90% below entry, so
        # a mark collapse to near-zero triggers the protective stop.
        proposal = make_proposal(
            "prop-1",
            START,
            path(ProposedActionType.ENTER_LONG),
        )
        proposal = replace(
            proposal,
            pre_trade_plan=PreTradePlan(
                stop_loss=StopLevel(distance_pct=0.90),
                take_profit=StopLevel(distance_pct=0.90),
                risk_per_trade_pct=0.02,
                risk_reward_ratio=1.0,
            ),
        )
        opened = step(simulator, engine, proposal, 100.0)
        assert opened.record is not None

        # Held past one funding boundary (>= 8h), then the stop triggers.
        step(
            simulator,
            engine,
            make_proposal("prop-2", START + hours(9), path(ProposedActionType.STAND_ASIDE)),
            5.0,
        )
        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.funding_cost is not None
        assert closed.gross_pnl is not None
        assert closed.realized_pnl is not None
        assert closed.funding_cost == pytest.approx(
            RATE * opened.record.entry_price * opened.record.quantity
        )
        assert closed.realized_pnl == pytest.approx(
            closed.gross_pnl - (closed.fee or 0.0) - closed.funding_cost
        )

    def test_partial_close_funding_books_per_slice(self, config: FundingConfig) -> None:
        simulator, ledger, engine = build_simulator(funding_config=config)
        opened = step(
            simulator,
            engine,
            make_proposal("prop-1", START, path(ProposedActionType.ENTER_LONG)),
            100.0,
        )
        assert opened.record is not None
        # Scale out half after one boundary; the slice pays its own funding.
        partial = step(
            simulator,
            engine,
            make_proposal(
                "prop-2",
                START + hours(8),
                path(ProposedActionType.SCALE_OUT, size_fraction=0.5),
            ),
            110.0,
        )
        assert partial.record is not None
        quantity = opened.record.quantity
        entry = opened.record.entry_price
        assert partial.record.funding_cost == pytest.approx(0.5 * RATE * entry * quantity)
        assert partial.record.quantity == pytest.approx(0.5 * quantity)

        # Final close crosses a second boundary; remaining half pays its own.
        final = step(
            simulator,
            engine,
            make_proposal("prop-3", START + hours(17), path(ProposedActionType.EXIT)),
            110.0,
        )
        assert final.record is not None
        assert final.record.funding_cost == pytest.approx(0.5 * RATE * entry * quantity * 2)

    def test_equity_windows_include_funding(self, config: FundingConfig) -> None:
        simulator, ledger, engine = build_simulator(funding_config=config)
        opened = step(
            simulator,
            engine,
            make_proposal("prop-1", START, path(ProposedActionType.ENTER_LONG)),
            100.0,
        )
        assert opened.record is not None
        step(
            simulator,
            engine,
            make_proposal("prop-2", START + hours(8), path(ProposedActionType.EXIT)),
            110.0,
        )
        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.realized_pnl is not None
        # Equity change equals realized PnL (net of funding), not gross.
        assert simulator.equity == pytest.approx(100_000.0 + closed.realized_pnl)
        assert simulator.risk_snapshot(mark_price=110.0).daily_loss_pct >= 0.0
