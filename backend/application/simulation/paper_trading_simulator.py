# backend/application/simulation/paper_trading_simulator.py
"""Deterministic, replay-driven paper-trading simulator.

The simulator is the Phase 2 harness: it consumes Decision Proposals, subjects
them to the risk gate (veto authority), fills approved actions via the paper
order gateway, and records outcomes into the ledger. It is fully deterministic
given the same input sequence — a replay of the same proposals and prices
produces the identical ledger.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, replace
from datetime import datetime

from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.interfaces.order_gateway import OrderGateway
from backend.application.interfaces.risk_gate import RiskGate
from backend.application.simulation.paper_fill_engine import PaperFeeConfig
from backend.domain.decision.proposal import (
    DecisionProposal,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.decision.trade_plan import PreTradePlan
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.funding import FundingConfig, funding_cost_for
from backend.domain.execution.order import OrderRequest, OrderSide, OrderType
from backend.domain.execution.pnl import realized_pnl, unrealized_pnl
from backend.domain.execution.position import Position
from backend.domain.execution.trade_record import TradeRecord, TradeStatus


class SimulationResult(enum.StrEnum):
    """Outcome of processing a single proposal in the simulator."""

    APPROVED = "approved"
    REJECTED = "rejected"
    NO_ACTION = "no_action"
    OPENED = "opened"
    CLOSED = "closed"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class SimulationStep:
    """Result of one proposal through the simulator.

    Attributes
    ----------
    proposal_id: str
        The proposal that was processed.
    result: SimulationResult
        What the simulator did with it.
    risk_verdict: str
        Verdict produced by the risk gate.
    report: ExecutionReport | None
        Fill report when an order was placed.
    position: Position | None
        Position snapshot after this step, if any.
    record: TradeRecord | None
        Ledger record produced by this step, if any.
    """

    proposal_id: str
    result: SimulationResult
    risk_verdict: str
    report: ExecutionReport | None
    position: Position | None
    record: TradeRecord | None
    exit_reason: str | None = None


@dataclass
class SimulatorState:
    """Mutable internal state of the simulator (not persisted as truth)."""

    positions: dict[str, Position] = field(default_factory=dict)
    open_records: dict[str, TradeRecord] = field(default_factory=dict)
    equity: float = 100_000.0
    daily_pnl: float = 0.0
    monthly_pnl: float = 0.0
    peak_equity: float = 100_000.0
    starting_equity: float = 100_000.0
    last_daily_reset: str = ""  # ISO date string
    last_monthly_reset: str = ""  # ISO month string
    current_time: datetime | None = None  # latest event time from proposals


class PaperTradingSimulator:
    """Replay-driven paper execution of decision proposals."""

    def __init__(
        self,
        risk_gate: RiskGate,
        order_gateway: OrderGateway,
        ledger: LedgerRepository,
        starting_equity: float = 100_000.0,
        fee_config: PaperFeeConfig | None = None,
        funding_config: FundingConfig | None = None,
    ) -> None:
        if starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        self._risk_gate = risk_gate
        self._order_gateway = order_gateway
        self._ledger = ledger
        self._fee_config = fee_config or PaperFeeConfig()
        # None keeps funding unmodeled (funding_cost stays None on records);
        # a config enables the deterministic funding schedule.
        self._funding_config = funding_config
        self._state = SimulatorState(
            equity=starting_equity,
            peak_equity=starting_equity,
            starting_equity=starting_equity,
        )
        self._sequence = 0

    @property
    def equity(self) -> float:
        return self._state.equity

    @property
    def positions(self) -> dict[str, Position]:
        return dict(self._state.positions)

    def risk_snapshot(
        self,
        mark_price: float | None = None,
        symbol: str | None = None,
        *,
        now: datetime | None = None,
    ) -> RiskContext:
        """Return the current portfolio state as a RiskContext.

        This is the deterministic feed the reasoner uses for sizing. Exposure
        is the sum of open position notionals relative to current equity; loss
        metrics are computed against the simulator's starting equity. When
        ``symbol`` is given, per-symbol usage (bracket at-risk) is returned in
        ``symbol_risk_used_pct``; ``portfolio_risk_used_pct`` is always the sum
        of every open position's bracket at-risk.

        Daily/monthly PnL resets are driven by event time (the latest proposal
        timestamp seen by :meth:`process`) or by the explicitly injected
        ``now``. The wall clock is never consulted, so the daily/monthly loss
        windows are a pure function of the replayed sequence.

        Parameters
        ----------
        mark_price: float | None
            Current mark price for unrealized PnL calculation. If provided,
            includes unrealized PnL in equity.
        symbol: str | None
            Symbol for which to compute per-symbol risk usage.
        now: datetime | None
            Event time to use for the daily/monthly reset boundary. When
            omitted, the latest proposal timestamp is used.
        """
        # Reset daily/monthly PnL at event-time boundaries.
        current = now if now is not None else self._state.current_time
        if current is not None:
            today = current.date().isoformat()
            this_month = current.strftime("%Y-%m")

            if self._state.last_daily_reset and self._state.last_daily_reset != today:
                self._state.daily_pnl = 0.0
            self._state.last_daily_reset = today
            if self._state.last_monthly_reset and self._state.last_monthly_reset != this_month:
                self._state.monthly_pnl = 0.0
            self._state.last_monthly_reset = this_month

        equity = self._state.equity
        # Include unrealized PnL if mark price provided. Per-symbol to avoid
        # cross-symbol mispricing: a single mark must not be applied to all
        # positions. When symbol is given, only that position's unrealized is
        # counted; multi-symbol callers should pass a dict or call per symbol.
        if mark_price is not None and mark_price > 0:
            if symbol is not None:
                pos = self._state.positions.get(symbol)
                unrealized = (
                    unrealized_pnl(pos.side, pos.average_entry_price, mark_price, pos.quantity)
                    if pos is not None
                    else 0.0
                )
            elif len(self._state.positions) <= 1:
                unrealized = sum(
                    unrealized_pnl(pos.side, pos.average_entry_price, mark_price, pos.quantity)
                    for pos in self._state.positions.values()
                )
            else:
                # Multi-position without per-symbol price: do not fabricate marks
                unrealized = 0.0
            total_equity = equity + unrealized
        else:
            total_equity = equity

        open_exposure = sum(
            position.quantity * position.average_entry_price
            for position in self._state.positions.values()
        )
        benchmark = total_equity if total_equity > 0 else 1.0
        portfolio_risk_used = (
            sum(self._bracket_at_risk(position) for position in self._state.positions.values())
            / benchmark
        )
        symbol_risk_used = 0.0
        symbol_exposure = 0.0
        if symbol is not None:
            position = self._state.positions.get(symbol)
            if position is not None:
                symbol_risk_used = self._bracket_at_risk(position) / benchmark
                symbol_exposure = (position.quantity * position.average_entry_price) / benchmark
        peak = max(self._state.peak_equity, total_equity)
        self._state.peak_equity = peak
        starting = self._state.starting_equity
        return RiskContext(
            account_equity=total_equity,
            open_exposure_pct=open_exposure / total_equity if total_equity > 0 else 0.0,
            daily_loss_pct=max(0.0, -self._state.daily_pnl / starting),
            monthly_loss_pct=max(0.0, -self._state.monthly_pnl / starting),
            total_loss_pct=max(0.0, (starting - total_equity) / starting),
            drawdown_pct=max(0.0, (peak - total_equity) / peak),
            position_count=len(self._state.positions),
            symbol_risk_used_pct=round(symbol_risk_used, 8),
            symbol_exposure_pct=round(symbol_exposure, 8),
            portfolio_risk_used_pct=round(portfolio_risk_used, 8),
        )

    def process(self, proposal: DecisionProposal, mark_price: float) -> SimulationStep:
        """Process one proposal at a given mark price.

        The price is supplied by the replay driver, keeping the simulator
        deterministic and free of any clock/network dependence.
        """
        self._sequence += 1

        # Track the latest event time from the proposal sequence. Advancing
        # monotonically keeps an out-of-order replay from rewinding the
        # daily/monthly reset boundary.
        if self._state.current_time is None or proposal.created_at > self._state.current_time:
            self._state.current_time = proposal.created_at

        # Protective brackets fire first and unconditionally: an open position
        # is always protected by its OCO bracket regardless of what this
        # proposal wants to do (a protective exit is never gated/vetoed).
        bracket = self._check_bracket(proposal.symbol, mark_price, proposal.created_at)
        if bracket is not None:
            exit_price, reason, closed = bracket
            return SimulationStep(
                proposal_id=proposal.proposal_id,
                result=SimulationResult.CLOSED,
                risk_verdict=f"protective_bracket:{reason}",
                report=None,
                position=None,
                record=closed,
                exit_reason=reason,
            )

        decision = self._risk_gate.evaluate(proposal, mark_price=mark_price)
        if not decision.approved:
            return SimulationStep(
                proposal_id=proposal.proposal_id,
                result=SimulationResult.REJECTED,
                risk_verdict=decision.verdict.value,
                report=None,
                position=None,
                record=None,
            )

        report: ExecutionReport | None = None
        record: TradeRecord | None = None
        result = SimulationResult.NO_ACTION

        for action in sorted(proposal.actions, key=lambda a: a.order):
            action_result = self._apply_action(
                proposal, decision.approved_size_fraction, action, mark_price
            )
            if action_result is not None:
                step_result, action_report, action_record = action_result
                result = step_result
                report = action_report or report
                record = action_record or record

        position = self._state.positions.get(proposal.symbol)
        return SimulationStep(
            proposal_id=proposal.proposal_id,
            result=result,
            risk_verdict=decision.verdict.value,
            report=report,
            position=position,
            record=record,
        )

    def _apply_action(
        self,
        proposal: DecisionProposal,
        approved_size_fraction: float | None,
        action: ProposedAction,
        mark_price: float,
    ) -> tuple[SimulationResult, ExecutionReport | None, TradeRecord | None] | None:
        if action.action_type in (ProposedActionType.STAND_ASIDE, ProposedActionType.REDUCE_RISK):
            return SimulationResult.NO_ACTION, None, None

        # approved_size_fraction=0.0 means full veto (gate rejected)
        if approved_size_fraction is not None and approved_size_fraction <= 0.0:
            return SimulationResult.NO_ACTION, None, None

        size = (
            action.size_fraction
            if approved_size_fraction is None
            else min(action.size_fraction, approved_size_fraction)
        )
        base_quantity = self._quantity_for(self._state.equity, size, mark_price)

        if action.action_type in (ProposedActionType.ENTER_LONG, ProposedActionType.ENTER_SHORT):
            return self._open(proposal, base_quantity, action)
        if action.action_type in (ProposedActionType.EXIT, ProposedActionType.SCALE_OUT):
            return self._close(proposal, action, mark_price)
        return None

    def _open(
        self,
        proposal: DecisionProposal,
        quantity: float,
        action: ProposedAction,
    ) -> tuple[SimulationResult, ExecutionReport | None, TradeRecord | None]:
        if proposal.symbol in self._state.positions:
            return SimulationResult.NO_ACTION, None, None

        side = (
            OrderSide.BUY if action.action_type is ProposedActionType.ENTER_LONG else OrderSide.SELL
        )
        order = OrderRequest(
            order_id=f"sim-{self._sequence}-{action.order}",
            proposal_id=proposal.proposal_id,
            symbol=proposal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=quantity,
            limit_price=None,
            created_at=proposal.created_at,
        )
        report = self._order_gateway.submit(order)
        if not report.is_filled:
            return SimulationResult.NO_ACTION, report, None

        now = proposal.created_at
        stop_loss_price, take_profit_price = self._resolve_bracket(
            proposal.pre_trade_plan, report.average_fill_price, side
        )
        position = Position(
            symbol=proposal.symbol,
            side=side,
            quantity=report.quantity,
            average_entry_price=report.average_fill_price,
            opened_at=now,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
        )
        self._state.positions[proposal.symbol] = position

        entry_fee = report.fee or 0.0
        if entry_fee:
            self._debit_fee(entry_fee)

        record = TradeRecord.open(
            trade_id=f"trade-{self._sequence}-{action.order}",
            proposal_id=proposal.proposal_id,
            correlation_id=proposal.correlation_id,
            symbol=proposal.symbol,
            side=side,
            quantity=report.quantity,
            entry_price=report.average_fill_price,
            opened_at=now,
            fee=entry_fee or None,
            entry_arrival_price=report.arrival_price,
        )
        self._state.open_records[proposal.symbol] = record
        self._ledger.save(record)
        return SimulationResult.OPENED, report, record

    def _close(
        self,
        proposal: DecisionProposal,
        action: ProposedAction,
        mark_price: float,
    ) -> tuple[SimulationResult, ExecutionReport | None, TradeRecord | None]:
        open_record = self._state.open_records.get(proposal.symbol)
        if open_record is None:
            return SimulationResult.NO_ACTION, None, None

        # EXIT always closes the full position; SCALE_OUT closes a fraction of
        # the open quantity (never an equity-derived quantity at the exit price,
        # which would silently turn a full exit into a partial close).
        if action.action_type is ProposedActionType.EXIT:
            close_quantity = open_record.quantity
        else:
            close_quantity = max(1e-9, min(action.size_fraction, 1.0) * open_record.quantity)
        # Support partial close: clamp to position size
        close_quantity = min(close_quantity, open_record.quantity)
        is_partial = close_quantity < open_record.quantity

        close_side = OrderSide.SELL if open_record.side is OrderSide.BUY else OrderSide.BUY
        order = OrderRequest(
            order_id=f"sim-{self._sequence}-{action.order}",
            proposal_id=proposal.proposal_id,
            symbol=proposal.symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            quantity=close_quantity,
            limit_price=None,
            created_at=proposal.created_at,
        )
        report = self._order_gateway.submit(order)
        if not report.is_filled:
            return SimulationResult.NO_ACTION, report, None

        entry = open_record.entry_price
        exit_price = report.average_fill_price
        gross = realized_pnl(open_record.side, entry, exit_price, close_quantity)
        exit_fee = report.fee or 0.0
        entry_share = self._entry_fee_share(open_record, close_quantity)
        funding = self._funding_for_slice(open_record, close_quantity, proposal.created_at)
        net = gross - entry_share - exit_fee - (funding or 0.0)
        self._state.daily_pnl += gross - exit_fee - (funding or 0.0)
        self._state.monthly_pnl += gross - exit_fee - (funding or 0.0)
        self._state.equity += gross - exit_fee - (funding or 0.0)
        self._state.peak_equity = max(self._state.peak_equity, self._state.equity)

        if is_partial:
            # Partial close: reduce position, keep trade open
            remaining = open_record.quantity - close_quantity
            existing = self._state.positions.get(proposal.symbol)
            self._state.positions[proposal.symbol] = Position(
                symbol=open_record.symbol,
                side=open_record.side,
                quantity=remaining,
                average_entry_price=entry,
                opened_at=open_record.opened_at,
                stop_loss_price=existing.stop_loss_price if existing else None,
                take_profit_price=existing.take_profit_price if existing else None,
            )
            remaining_fee = (open_record.fee or 0.0) - entry_share
            remaining_record = replace(open_record, quantity=remaining, fee=remaining_fee or None)
            self._state.open_records[proposal.symbol] = remaining_record
            self._ledger.save(remaining_record)
            closed = TradeRecord(
                trade_id=f"trade-{self._sequence}-{action.order}",
                proposal_id=open_record.proposal_id,
                correlation_id=open_record.correlation_id,
                symbol=open_record.symbol,
                side=open_record.side,
                quantity=close_quantity,
                entry_price=entry,
                opened_at=open_record.opened_at,
                exit_price=exit_price,
                closed_at=proposal.created_at,
                realized_pnl=net,
                status=TradeStatus.CLOSED,
                gross_pnl=gross,
                fee=entry_share + exit_fee if (entry_share + exit_fee) else None,
                funding_cost=funding,
                entry_arrival_price=open_record.entry_arrival_price,
                exit_arrival_price=report.arrival_price,
            )
            self._ledger.save(closed)
            return SimulationResult.PARTIAL, report, closed
        else:
            # Full close
            closed = TradeRecord(
                trade_id=open_record.trade_id,
                proposal_id=open_record.proposal_id,
                correlation_id=open_record.correlation_id,
                symbol=open_record.symbol,
                side=open_record.side,
                quantity=open_record.quantity,
                entry_price=entry,
                opened_at=open_record.opened_at,
                exit_price=exit_price,
                closed_at=proposal.created_at,
                realized_pnl=net,
                status=TradeStatus.CLOSED,
                gross_pnl=gross,
                fee=entry_share + exit_fee if (entry_share + exit_fee) else None,
                funding_cost=funding,
                entry_arrival_price=open_record.entry_arrival_price,
                exit_arrival_price=report.arrival_price,
            )
            self._ledger.save(closed)
            self._state.positions.pop(proposal.symbol, None)
            self._state.open_records.pop(proposal.symbol, None)
            return SimulationResult.CLOSED, report, closed

    @staticmethod
    def _quantity_for(equity: float, size_fraction: float, mark_price: float) -> float:
        """Deterministic quantity for a size fraction of equity at given price."""
        if mark_price <= 0:
            raise ValueError("mark_price must be positive for sizing")
        return max(equity * size_fraction / mark_price, 1e-9)

    def _resolve_bracket(
        self,
        plan: PreTradePlan | None,
        fill_price: float,
        side: OrderSide,
    ) -> tuple[float | None, float | None]:
        """Resolve the OCO bracket levels (stop, take-profit) from the plan.

        Distance-based levels are anchored to the actual fill price; absolute
        prices pass through unchanged. Returns ``(stop_loss, take_profit)``.
        """
        if plan is None or not plan.has_bracket:
            return None, None
        sl_price = plan.stop_loss.price
        tp_price = plan.take_profit.price
        sl_dist = plan.stop_loss.distance_pct
        tp_dist = plan.take_profit.distance_pct
        if side is OrderSide.BUY:
            stop = (
                sl_price
                if sl_price is not None
                else (fill_price * (1.0 - sl_dist) if sl_dist is not None else None)
            )
            target = (
                tp_price
                if tp_price is not None
                else (fill_price * (1.0 + tp_dist) if tp_dist is not None else None)
            )
        else:
            stop = (
                sl_price
                if sl_price is not None
                else (fill_price * (1.0 + sl_dist) if sl_dist is not None else None)
            )
            target = (
                tp_price
                if tp_price is not None
                else (fill_price * (1.0 - tp_dist) if tp_dist is not None else None)
            )
        return stop, target

    def _check_bracket(
        self, symbol: str, mark_price: float, closed_at: datetime
    ) -> tuple[float, str, TradeRecord] | None:
        """Close a position whose OCO bracket is touched at ``mark_price``.

        A stop-loss triggers first (protective); OCO then cancels the
        take-profit. The fill is at the bracket level, not the mark price,
        keeping the result fully deterministic.
        """
        position = self._state.positions.get(symbol)
        if position is None:
            return None
        stop = position.stop_loss_price
        target = position.take_profit_price
        if stop is None and target is None:
            return None

        trigger: float | None = None
        reason = ""
        if stop is not None:
            hit_stop = mark_price <= stop if position.side is OrderSide.BUY else mark_price >= stop
            if hit_stop:
                trigger, reason = stop, "bracket_stop_loss"
        if trigger is None and target is not None:
            hit_target = (
                mark_price >= target if position.side is OrderSide.BUY else mark_price <= target
            )
            if hit_target:
                trigger, reason = target, "bracket_take_profit"
        if trigger is None:
            return None

        closed = self._close_position(
            symbol, exit_price=trigger, closed_at=closed_at, arrival_price=mark_price
        )
        return trigger, reason, closed

    def _close_position(
        self,
        symbol: str,
        exit_price: float,
        closed_at: datetime,
        arrival_price: float | None = None,
    ) -> TradeRecord:
        """Close a full position at an explicit price and write the ledger.

        Bracket exits are market withdrawals (taker) executed at the bracket
        level; the exit fee is charged using the simulator's own fee
        assumptions because no gateway fill report exists for them.
        ``arrival_price`` is the mark at trigger time, captured as the exit
        arrival for execution attribution.
        """
        open_record = self._state.open_records[symbol]
        entry = open_record.entry_price
        gross = realized_pnl(open_record.side, entry, exit_price, open_record.quantity)
        exit_fee = self._fee_config.taker_fee_rate * exit_price * open_record.quantity
        entry_fee = open_record.fee or 0.0
        funding = self._funding_for_slice(open_record, open_record.quantity, closed_at)
        net = gross - entry_fee - exit_fee - (funding or 0.0)
        self._state.daily_pnl += gross - exit_fee - (funding or 0.0)
        self._state.monthly_pnl += gross - exit_fee - (funding or 0.0)
        self._state.equity += gross - exit_fee - (funding or 0.0)
        self._state.peak_equity = max(self._state.peak_equity, self._state.equity)

        closed = TradeRecord(
            trade_id=open_record.trade_id,
            proposal_id=open_record.proposal_id,
            correlation_id=open_record.correlation_id,
            symbol=open_record.symbol,
            side=open_record.side,
            quantity=open_record.quantity,
            entry_price=entry,
            opened_at=open_record.opened_at,
            exit_price=exit_price,
            closed_at=closed_at,
            realized_pnl=net,
            status=TradeStatus.CLOSED,
            gross_pnl=gross,
            fee=entry_fee + exit_fee if (entry_fee + exit_fee) else None,
            funding_cost=funding,
            entry_arrival_price=open_record.entry_arrival_price,
            exit_arrival_price=arrival_price,
        )
        self._ledger.save(closed)
        self._state.positions.pop(symbol, None)
        self._state.open_records.pop(symbol, None)
        return closed

    def _debit_fee(self, fee: float) -> None:
        """Debit an execution fee from equity and the loss windows."""
        self._state.daily_pnl -= fee
        self._state.monthly_pnl -= fee
        self._state.equity -= fee
        self._state.peak_equity = max(self._state.peak_equity, self._state.equity)

    @staticmethod
    def _entry_fee_share(open_record: TradeRecord, close_quantity: float) -> float:
        """Proportional share of the already-charged entry fee for a close.

        When a trade is closed in slices, each closed slice carries its
        pro-rated portion of the entry fee; the remainder stays on the open
        record so the total across all slices equals the true entry fee.
        """
        if open_record.fee is None or open_record.quantity == 0:
            return 0.0
        return open_record.fee * (close_quantity / open_record.quantity)

    def _funding_for_slice(
        self, open_record: TradeRecord, close_quantity: float, closed_at: datetime
    ) -> float | None:
        """Signed funding cost for a closed slice, or None when unmodeled.

        Each closed slice is charged the funding that its notional accrued
        over the payment boundaries crossed from open to its close. A partial
        close therefore books funding for the fraction it released; the
        remaining quantity keeps its own (undetermined until the final close).
        """
        if self._funding_config is None:
            return None
        return funding_cost_for(
            side=open_record.side,
            quantity=close_quantity,
            entry_price=open_record.entry_price,
            opened_at=open_record.opened_at,
            closed_at=closed_at,
            config=self._funding_config,
        )

    @staticmethod
    def _bracket_at_risk(position: Position) -> float:
        """Absolute loss (in currency) if a position's stop-loss triggers.

        Positions opened without a bracket (legacy) contribute zero risk, so
        budget-based sizing never blocks on stale plans.
        """
        if position.stop_loss_price is None:
            return 0.0
        return abs(position.average_entry_price - position.stop_loss_price) * position.quantity
