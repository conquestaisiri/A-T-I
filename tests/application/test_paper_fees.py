"""Tests for configurable paper execution fees and gross/net PnL accounting.

P0-010 acceptance:
- Gross PnL and net PnL are distinct.
- Fees are recorded.
- Paper fee assumptions are configurable.
- Execution report and ledger agree.
- Funding is a separate cost field (explicitly deferred in the paper sim).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.risk.circuit_breaker_risk_gate import CircuitBreakerRiskGate
from backend.application.simulation.paper_fill_engine import (
    PaperFeeConfig,
    PaperFillEngine,
)
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
from backend.domain.execution.order import OrderRequest, OrderSide, OrderType
from backend.domain.execution.trade_record import TradeRecord, TradeStatus


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_proposal(
    proposal_id: str = "prop-1",
    created_at: datetime | None = None,
    actions: tuple[ProposedAction, ...] | None = None,
) -> DecisionProposal:
    return DecisionProposal(
        proposal_id=proposal_id,
        correlation_id=proposal_id,
        created_at=created_at or ts(),
        symbol="btcusdt",
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


def parse_path(
    action: ProposedActionType,
    size_fraction: float = 1.0,
) -> tuple[ProposedAction, ...]:
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


FEE_RATE = 0.001


@pytest.fixture
def rig() -> tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]:
    ledger = InMemoryLedger()
    fee_config = PaperFeeConfig(taker_fee_rate=FEE_RATE, maker_fee_rate=FEE_RATE)
    engine = PaperFillEngine(fee_config=fee_config)
    simulator = PaperTradingSimulator(
        risk_gate=CircuitBreakerRiskGate(),
        order_gateway=engine,
        ledger=ledger,
        fee_config=fee_config,
    )
    return simulator, ledger, engine


class TestPaperFeeConfig:
    def test_default_is_fee_free(self) -> None:
        config = PaperFeeConfig()
        assert config.taker_fee_rate == 0.0
        assert config.maker_fee_rate == 0.0

    def test_rejects_negative_rates(self) -> None:
        with pytest.raises(ValueError):
            PaperFeeConfig(taker_fee_rate=-0.001)
        with pytest.raises(ValueError):
            PaperFeeConfig(maker_fee_rate=-0.001)

    def test_maximum_floating_rates_accepted(self) -> None:
        PaperFeeConfig(taker_fee_rate=1e-9, maker_fee_rate=1e-9)


class TestFillEngineFees:
    def test_market_buy_fill_charges_taker_fee_on_notional(
        self, rig: tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]
    ) -> None:
        _, _, engine = rig
        engine.set_mark_price(100.0)
        report = engine.submit(_market_order(side=OrderSide.BUY, quantity=100.0, created_at=ts()))
        # Buy fills at ask 100.01; fee = 0.001 * 100.01 * 100
        assert report.is_filled
        assert report.fee == pytest.approx(0.001 * 100.01 * 100.0, rel=1e-9)

    def test_market_sell_fill_charges_taker_fee_on_notional(
        self, rig: tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]
    ) -> None:
        _, _, engine = rig
        engine.set_mark_price(100.0)
        report = engine.submit(_market_order(side=OrderSide.SELL, quantity=100.0, created_at=ts()))
        # Sell fills at bid 99.99; fee = 0.001 * 99.99 * 100
        assert report.is_filled
        assert report.fee == pytest.approx(0.001 * 99.99 * 100.0, rel=1e-9)

    def test_zero_fee_config_charges_nothing(self) -> None:
        engine = PaperFillEngine()
        engine.set_mark_price(100.0)
        report = engine.submit(_market_order(side=OrderSide.BUY, quantity=100.0, created_at=ts()))
        assert report.fee == 0.0

    def test_funding_cost_is_deferred_none(self) -> None:
        engine = PaperFillEngine(fee_config=PaperFeeConfig(taker_fee_rate=FEE_RATE))
        engine.set_mark_price(100.0)
        report = engine.submit(_market_order(side=OrderSide.BUY, quantity=100.0, created_at=ts()))
        assert report.funding_cost is None


class TestGrossAndNetPnL:
    def test_accounting_gross_net_and_fee_on_round_trip(
        self, rig: tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]
    ) -> None:
        simulator, ledger, engine = rig
        engine.set_mark_price(100.0)

        opened = simulator.process(make_proposal(), mark_price=100.0)
        assert opened.result is SimulationResult.OPENED
        assert opened.report is not None
        assert opened.report.fee == pytest.approx(0.001 * 100.01 * 100.0, rel=1e-9)
        # Entry fee debited immediately from equity
        assert simulator.equity == pytest.approx(100_000.0 - 0.001 * 100.01 * 100.0, rel=1e-9)

        engine.set_mark_price(110.0)
        closed = simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=parse_path(ProposedActionType.EXIT),
            ),
            mark_price=110.0,
        )
        assert closed.result is SimulationResult.CLOSED
        assert closed.record is not None
        assert closed.record.status is TradeStatus.CLOSED

        entry_fee = 0.001 * 100.01 * 100.0
        exit_fee = 0.001 * 109.989 * 100.0
        gross = (109.989 - 100.01) * 100.0
        net = gross - entry_fee - exit_fee

        record_fee = closed.record.fee
        record_gross = closed.record.gross_pnl
        assert record_fee is not None
        assert record_gross is not None

        # Gross and net are distinct, and fee bridges them
        assert closed.record.gross_pnl == pytest.approx(gross, rel=1e-9)
        assert closed.record.realized_pnl == pytest.approx(net, rel=1e-9)
        assert closed.record.realized_pnl == pytest.approx(record_gross - record_fee, rel=1e-9)
        assert closed.record.fee == pytest.approx(entry_fee + exit_fee, rel=1e-9)

        # Net flows into equity/day/month windows
        assert simulator.equity == pytest.approx(100_000.0 + net, rel=1e-9)

        # Open record carries the entry fee; the ledger was updated in place
        assert opened.record is not None
        assert opened.record.fee == pytest.approx(entry_fee, rel=1e-9)
        ledger_record = ledger.find_by_id(opened.record.trade_id)
        assert ledger_record is not None
        assert ledger_record.fee == pytest.approx(entry_fee + exit_fee, rel=1e-9)

    def test_funding_deferred_none_on_records(
        self, rig: tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]
    ) -> None:
        simulator, ledger, engine = rig
        engine.set_mark_price(100.0)
        opened = simulator.process(make_proposal(), mark_price=100.0)
        assert opened.record is not None
        assert opened.record.funding_cost is None
        assert opened.record.gross_pnl is None

        engine.set_mark_price(90.0)
        simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=parse_path(ProposedActionType.EXIT),
            ),
            mark_price=90.0,
        )
        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.funding_cost is None
        assert closed.gross_pnl is not None

    def test_bracket_exit_applies_taker_fee(
        self, rig: tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]
    ) -> None:
        simulator, ledger, engine = rig
        engine.set_mark_price(100.0)
        opened = simulator.process(make_proposal(), mark_price=100.0)
        assert opened.result is SimulationResult.OPENED
        assert opened.record is not None

        # Stop at fill*(1-0.05) = 95.0095 fires when mark drops to 90
        engine.set_mark_price(90.0)
        step = simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=parse_path(ProposedActionType.STAND_ASIDE),
            ),
            mark_price=90.0,
        )
        assert step.result is SimulationResult.CLOSED
        exit_price = 100.01 * (1.0 - 0.05)

        entry_fee = 0.001 * 100.01 * 100.0
        exit_fee = 0.001 * exit_price * 100.0
        gross = (exit_price - 100.01) * 100.0
        net = gross - entry_fee - exit_fee

        closed = ledger.find_by_id(opened.record.trade_id)
        assert closed is not None
        assert closed.realized_pnl == pytest.approx(net, rel=1e-9)
        assert closed.gross_pnl == pytest.approx(gross, rel=1e-9)
        assert closed.fee == pytest.approx(entry_fee + exit_fee, rel=1e-9)
        assert simulator.equity == pytest.approx(100_000.0 + net, rel=1e-9)

    def test_partial_close_allocates_entry_fee_proportionally(
        self, rig: tuple[PaperTradingSimulator, InMemoryLedger, PaperFillEngine]
    ) -> None:
        simulator, ledger, engine = rig
        engine.set_mark_price(100.0)
        opened = simulator.process(make_proposal(), mark_price=100.0)
        assert opened.record is not None
        open_trade_id = opened.record.trade_id

        entry_fee = 0.001 * 100.01 * 100.0

        engine.set_mark_price(110.0)
        partial = simulator.process(
            make_proposal(
                proposal_id="prop-2",
                actions=parse_path(ProposedActionType.SCALE_OUT, size_fraction=0.5),
            ),
            mark_price=110.0,
        )
        assert partial.result is SimulationResult.PARTIAL
        assert partial.record is not None
        assert partial.record.gross_pnl == pytest.approx((109.989 - 100.01) * 50.0, rel=1e-9)

        # Half the entry fee is allocated to the closed slice
        entry_share = entry_fee * 0.5
        exit_fee = 0.001 * 109.989 * 50.0
        assert partial.record.fee == pytest.approx(entry_share + exit_fee, rel=1e-9)

        # The rest stays on the open record
        open_record = ledger.find_by_id(open_trade_id)
        assert open_record is not None
        assert open_record.fee == pytest.approx(entry_fee * 0.5, rel=1e-9)

        # Final close's slice allocates the remaining entry fee
        final = simulator.process(
            make_proposal(
                proposal_id="prop-3",
                actions=parse_path(ProposedActionType.EXIT),
            ),
            mark_price=110.0,
        )
        assert final.record is not None
        exit_fee_2 = 0.001 * 109.989 * 50.0
        assert final.record.fee == pytest.approx(entry_fee * 0.5 + exit_fee_2, rel=1e-9)

        # Sum of per-slice nets equals the full-trade net
        entry_exit_gross_total = (109.989 - 100.01) * 100.0
        total_fees = entry_fee + exit_fee + exit_fee_2
        expected_equity = 100_000.0 + entry_exit_gross_total - total_fees
        assert simulator.equity == pytest.approx(expected_equity, rel=1e-9)


def _market_order(side: OrderSide, quantity: float, created_at: datetime) -> OrderRequest:
    return OrderRequest(
        order_id="ord-1",
        proposal_id="prop-1",
        symbol="btcusdt",
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        limit_price=None,
        created_at=created_at,
    )
