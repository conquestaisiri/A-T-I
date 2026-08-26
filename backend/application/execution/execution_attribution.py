# backend/application/execution/execution_attribution.py
"""Execution attribution service: decompose a book of closed trades.

Builds per-trade :class:`TradeAttribution` decompositions and a portfolio
aggregate that answers the questions an operator actually has:

- How much PnL came from the market move (alpha) vs how much leaked to
  execution (slippage, fees, funding)?
- What fraction of gross PnL was consumed by costs (the "cost drag")?

Everything is derived deterministically from closed :class:`TradeRecord`
values and the arrival prices the gateways captured, so attribution replays
exactly like the ledger it reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.domain.execution.attribution import TradeAttribution, attribute_trade
from backend.domain.execution.trade_record import TradeRecord, TradeStatus


@dataclass(frozen=True, slots=True)
class AttributionReport:
    """Aggregate execution attribution across a set of closed trades.

    Attributes
    ----------
    trade_count: int
        Number of closed trades analysed.
    gross_pnl: float
        Sum of gross PnL across the book (before costs).
    alpha_pnl: float
        Sum of alpha PnL (returns at arrival prices).
    entry_slippage: float
        Total entry slippage cost.
    exit_slippage: float
        Total exit slippage cost.
    total_slippage: float
        entry + exit slippage.
    fees: float
        Total execution fees.
    funding_cost: float
        Total funding/carry cost.
    net_pnl: float
        Realised net PnL (equals gross - fees - funding).
    cost_drag_pct: float | None
        Total costs as a fraction of gross alpha, when gross is positive and
        non-trivial. ``None`` when there is no positive gross to drag.
    """

    trade_count: int
    gross_pnl: float
    alpha_pnl: float
    entry_slippage: float
    exit_slippage: float
    total_slippage: float
    fees: float
    funding_cost: float
    net_pnl: float
    cost_drag_pct: float | None

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for observability."""
        return {
            "trade_count": self.trade_count,
            "gross_pnl": self.gross_pnl,
            "alpha_pnl": self.alpha_pnl,
            "entry_slippage": self.entry_slippage,
            "exit_slippage": self.exit_slippage,
            "total_slippage": self.total_slippage,
            "fees": self.fees,
            "funding_cost": self.funding_cost,
            "net_pnl": self.net_pnl,
            "cost_drag_pct": self.cost_drag_pct,
        }


class ExecutionAttributionService:
    """Decompose closed trades from a ledger into attributable PnL components."""

    def __init__(self, ledger: LedgerRepository) -> None:
        self._ledger = ledger

    def attribute(self, trade: TradeRecord) -> TradeAttribution:
        """Decompose one closed trade using its captured arrival prices."""
        return attribute_trade(
            trade,
            entry_arrival=trade.entry_arrival_price,
            exit_arrival=trade.exit_arrival_price,
        )

    def report_all(self, symbol: str | None = None, limit: int = 500) -> AttributionReport:
        """Aggregate attribution over the most recent closed trades.

        ``symbol`` filters to one market; ``limit`` bounds how many recent
        records are scanned (the ledger keeps its full history).
        """
        records = self._recent_closed(symbol=symbol, limit=limit)
        return self.report(records)

    def recent(
        self, symbol: str | None = None, limit: int = 500
    ) -> tuple[AttributionReport, list[TradeAttribution]]:
        """Return the aggregate report plus per-trade attributions.

        Convenience for API routes: one query, both the portfolio aggregate
        and the fully decomposed list behind it.
        """
        records = self._recent_closed(symbol=symbol, limit=limit)
        attributions = [self.attribute(t) for t in records if t.realized_pnl is not None]
        return self.report(records), attributions

    def report(self, trades: list[TradeRecord]) -> AttributionReport:
        """Aggregate attribution over the given closed trades (empty-safe)."""
        closed = [
            t for t in trades if t.status is TradeStatus.CLOSED and t.realized_pnl is not None
        ]
        if not closed:
            return AttributionReport(
                trade_count=0,
                gross_pnl=0.0,
                alpha_pnl=0.0,
                entry_slippage=0.0,
                exit_slippage=0.0,
                total_slippage=0.0,
                fees=0.0,
                funding_cost=0.0,
                net_pnl=0.0,
                cost_drag_pct=None,
            )

        attributions = [self.attribute(t) for t in closed]
        gross = sum(a.gross_pnl for a in attributions)
        alpha = sum(a.alpha_pnl for a in attributions)
        entry_slip = sum(a.entry_slippage for a in attributions)
        exit_slip = sum(a.exit_slippage for a in attributions)
        fees = sum(a.fee for a in attributions)
        funding = sum(a.funding_cost for a in attributions)
        net = sum(a.net_pnl for a in attributions)

        cost_drag: float | None = None
        if alpha > 0:
            cost_drag = (entry_slip + exit_slip + fees + funding) / alpha * 100.0

        return AttributionReport(
            trade_count=len(attributions),
            gross_pnl=gross,
            alpha_pnl=alpha,
            entry_slippage=entry_slip,
            exit_slippage=exit_slip,
            total_slippage=entry_slip + exit_slip,
            fees=fees,
            funding_cost=funding,
            net_pnl=net,
            cost_drag_pct=cost_drag,
        )

    def _recent_closed(self, *, symbol: str | None, limit: int) -> list[TradeRecord]:
        """Fetch recent trades; symbol filter when given, else scan symbols."""
        if symbol is not None:
            return self._ledger.find_recent(symbol, limit)
        # No cross-symbol interface yet: walk a bounded set of open symbols is
        # impossible without knowing them, so fetch recent from a default sweep
        # is delegated to the ledger's closed view when available. The SQLite
        # ledger stores everything; here we fall back to an explicit query.
        return self._ledger.closed_trades(limit)
