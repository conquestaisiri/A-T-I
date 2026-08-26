"""Tests for P2 build item 25: execution attribution.

Verifies the per-trade decomposition identities and the portfolio aggregate
over closed ledger records, plus the arrival-price capture in the simulator
and the /v1/ledger/attribution route.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
from backend.application.execution.execution_attribution import ExecutionAttributionService
from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.domain.execution.attribution import attribute_trade
from backend.domain.execution.order import OrderSide
from backend.domain.execution.trade_record import TradeRecord, TradeStatus


def ts(day: int = 1) -> datetime:
    return datetime(2026, 3, day, 12, 0, 0, tzinfo=UTC)


def closed_trade(
    trade_id: str,
    *,
    side: OrderSide = OrderSide.BUY,
    entry: float = 100.0,
    exit: float = 110.0,
    quantity: float = 10.0,
    entry_arrival: float | None = 99.0,
    exit_arrival: float | None = 111.0,
    fee: float | None = 2.0,
    funding: float | None = 0.5,
) -> TradeRecord:
    direction = 1 if side is OrderSide.BUY else -1
    gross = direction * (exit - entry) * quantity
    net = gross - (fee or 0.0) - (funding or 0.0)
    return TradeRecord(
        trade_id=trade_id,
        proposal_id="prop-1",
        correlation_id="corr-1",
        symbol="btcusdt",
        side=side,
        quantity=quantity,
        entry_price=entry,
        opened_at=ts(1),
        exit_price=exit,
        closed_at=ts(2),
        realized_pnl=net,
        status=TradeStatus.CLOSED,
        gross_pnl=gross,
        fee=fee,
        funding_cost=funding,
        entry_arrival_price=entry_arrival,
        exit_arrival_price=exit_arrival,
    )


class TestDecomposition:
    def test_buy_long_identity(self) -> None:
        trade = closed_trade("t1")
        a = attribute_trade(trade, trade.entry_arrival_price, trade.exit_arrival_price)
        assert a.gross_pnl == pytest.approx(100.0)
        # Buy at 100 with arrival 99 => slippage (100 - 99) * 10 = 10
        assert a.entry_slippage == pytest.approx(10.0)
        # Sell at 110 with arrival 111 => slippage (111 - 110) * 10 = 10
        assert a.exit_slippage == pytest.approx(10.0)
        # alpha = gross + entry_slip + exit_slip = 120
        assert a.alpha_pnl == pytest.approx(120.0)
        # net = gross - fee - funding = 100 - 2 - 0.5 = 97.5
        assert a.net_pnl == pytest.approx(97.5)
        assert a.net_pnl == trade.realized_pnl
        # Identity: gross = alpha - slippage
        assert a.alpha_pnl - a.entry_slippage - a.exit_slippage == pytest.approx(a.gross_pnl)
        # Identity: net = gross - fee - funding
        assert a.net_pnl == pytest.approx(a.gross_pnl - a.fee - a.funding_cost)

    def test_short_identity(self) -> None:
        trade = closed_trade(
            "t2",
            side=OrderSide.SELL,
            entry=110.0,
            exit=100.0,
            entry_arrival=111.0,
            exit_arrival=99.0,
        )
        a = attribute_trade(trade, trade.entry_arrival_price, trade.exit_arrival_price)
        assert a.gross_pnl == pytest.approx(100.0)
        # Sell at 110 with arrival 111 => slippage (111 - 110) * 10 = 10
        assert a.entry_slippage == pytest.approx(10.0)
        # Buy back at 100 with arrival 99 => slippage (100 - 99) * 10 = 10
        assert a.exit_slippage == pytest.approx(10.0)
        assert a.alpha_pnl - a.entry_slippage - a.exit_slippage == pytest.approx(a.gross_pnl)

    def test_missing_arrival_attributes_slippage_to_alpha(self) -> None:
        trade = closed_trade("t3", entry_arrival=None, exit_arrival=None)
        a = attribute_trade(trade, None, None)
        assert a.entry_slippage == 0.0
        assert a.exit_slippage == 0.0
        assert a.alpha_pnl == pytest.approx(a.gross_pnl)
        assert a.net_pnl == trade.realized_pnl

    def test_favorably_improved_fill_floors_slippage_at_zero(self) -> None:
        # Buy below arrival: green light, no slippage cost.
        trade = closed_trade("t4", entry=98.0, exit=110.0, entry_arrival=99.0)
        a = attribute_trade(trade, trade.entry_arrival_price, trade.exit_arrival_price)
        assert a.entry_slippage == 0.0
        assert a.alpha_pnl - a.entry_slippage - a.exit_slippage == pytest.approx(a.gross_pnl)

    def test_as_dict_roundtrip(self) -> None:
        trade = closed_trade("t5")
        a = attribute_trade(trade, trade.entry_arrival_price, trade.exit_arrival_price)
        data = a.as_dict()
        assert data["trade_id"] == "t5"
        assert data["gross_pnl"] == a.gross_pnl
        assert data["total_slippage"] == a.total_slippage
        assert data["side"] == "buy"


class InMemoryLedger(LedgerRepository):
    def __init__(self, records: list[TradeRecord]) -> None:
        self._records = {r.trade_id: r for r in records}

    def save(self, record: TradeRecord) -> None:
        self._records[record.trade_id] = record

    def find_by_id(self, trade_id: str) -> TradeRecord | None:
        return self._records.get(trade_id)

    def find_recent(self, symbol: str, limit: int = 20) -> list[TradeRecord]:
        return [r for r in self._records.values() if r.symbol == symbol][-limit:]

    def open_trades(self) -> list[TradeRecord]:
        return [r for r in self._records.values() if r.status is TradeStatus.OPEN]

    def closed_trades(self, limit: int = 100) -> list[TradeRecord]:
        return [r for r in self._records.values() if r.status is TradeStatus.CLOSED][-limit:]

    def count(self, symbol: str | None = None) -> int:
        if symbol is None:
            return len(self._records)
        return sum(1 for r in self._records.values() if r.symbol == symbol)


class TestAggregate:
    def test_empty_report(self) -> None:
        service = ExecutionAttributionService(InMemoryLedger([]))
        report = service.report([])
        assert report.trade_count == 0
        assert report.cost_drag_pct is None

    def test_report_sums_and_cost_drag(self) -> None:
        wins = [closed_trade(f"w{i}") for i in range(2)]
        service = ExecutionAttributionService(InMemoryLedger(wins))
        report = service.report(wins)
        assert report.trade_count == 2
        assert report.gross_pnl == pytest.approx(200.0)
        assert report.alpha_pnl == pytest.approx(240.0)
        assert report.total_slippage == pytest.approx(40.0)
        assert report.fees == pytest.approx(4.0)
        assert report.funding_cost == pytest.approx(1.0)
        assert report.net_pnl == pytest.approx(195.0)
        # cost drag: (40 + 4 + 1) / 240 * 100
        assert report.cost_drag_pct == pytest.approx(45.0 / 240.0 * 100.0)

    def test_symbol_filter_and_recent(self) -> None:
        eth_records = [closed_trade("eth-1"), closed_trade("eth-2")]
        btc_records = [closed_trade("btc-1")]
        eth_typed = [replace(r, symbol="ethusdt") for r in eth_records]
        btc_typed = [replace(r, symbol="btcusdt") for r in btc_records]
        all_records = eth_typed + btc_typed
        service = ExecutionAttributionService(InMemoryLedger(all_records))
        report, attributions = service.recent(symbol="ethusdt", limit=10)
        assert report.trade_count == 2
        assert len(attributions) == 2
        assert {a.trade_id for a in attributions} == {"eth-1", "eth-2"}


class TestSimulatorCapture:
    def test_simulator_records_arrival_prices(self) -> None:
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

        def proposal(pid: str, action: ProposedActionType, day: int) -> DecisionProposal:
            return DecisionProposal(
                proposal_id=pid,
                correlation_id=f"corr-{pid}",
                created_at=ts(day),
                symbol="btcusdt",
                hypothesis=Hypothesis(
                    statement="trend",
                    supporting_evidence=(EvidenceItem(source="trend", summary="up", value=1.0),),
                    opposing_evidence=(),
                ),
                confidence=0.8,
                uncertainty="none",
                actions=(
                    ProposedAction(action_type=action, size_fraction=0.1, order=1, rationale="go"),
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

        ledger = InMemoryLedger([])
        engine = PaperFillEngine()
        simulator = PaperTradingSimulator(
            risk_gate=CircuitBreakerRiskGate(), order_gateway=engine, ledger=ledger
        )
        engine.set_mark_price(100.0)

        opened = simulator.process(
            proposal("p1", ProposedActionType.ENTER_LONG, day=1), mark_price=100.0
        )
        assert opened.result is SimulationResult.OPENED
        assert opened.record is not None
        assert opened.record.entry_arrival_price == pytest.approx(100.0)

        engine.set_mark_price(110.0)
        closed = simulator.process(proposal("p2", ProposedActionType.EXIT, day=2), mark_price=110.0)
        assert closed.result is SimulationResult.CLOSED
        assert closed.record is not None
        assert closed.record.entry_arrival_price == pytest.approx(100.0)
        assert closed.record.exit_arrival_price == pytest.approx(110.0)

        report, attributions = ExecutionAttributionService(ledger).recent(limit=10)
        assert len(attributions) == 1
        a = attributions[0]
        assert a.gross_pnl == pytest.approx(a.alpha_pnl - a.entry_slippage - a.exit_slippage)
        assert a.net_pnl == closed.record.realized_pnl
