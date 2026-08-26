# backend/application/backtest/report.py
"""Backtest report: the quantified result of a deterministic replay.

The report is computed purely from the simulation path — the same inputs
always produce the same report (ADR 0007 replay determinism). It is the
objective measure the operator uses to compare reasoners (rule solver vs
LLM) and to decide whether learned behaviour is improving.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.context.market_context import MarketContext


@dataclass(frozen=True, slots=True)
class ReplayStep:
    """One replay step: a market context plus the mark price to trade at.

    Attributes
    ----------
    context: MarketContext
        Immutable features + snapshot metadata for the step.
    mark_price: float
        Price the proposal is executed against. Supplied by the driver so the
        replay never depends on a clock or network (ADR 0007).
    """

    context: MarketContext
    mark_price: float


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Aggregate statistics for one backtest campaign.

    Attributes
    ----------
    symbol: str
        Symbol that was replayed.
    steps: int
        Number of replay steps (proposals processed).
    starting_equity: float
        Equity at the start of the campaign.
    final_equity: float
        Equity after the last step.
    total_pnl: float
        Final equity minus starting equity.
    returns_pct: float
        Total PnL as a percentage of starting equity.
    max_drawdown_pct: float
        Largest peak-to-trough decline in equity, as a fraction of the peak.
    trades_opened: int
        Number of positions opened during the campaign.
    trades_closed: int
        Number of positions closed during the campaign.
    wins: int
        Closed trades with positive realised PnL.
    losses: int
        Closed trades with negative realised PnL.
    flats: int
        Closed trades with zero realised PnL.
    approved: int
        Proposals approved by the risk gate.
    rejected: int
        Proposals rejected by the risk gate.
    total_fees: float
        Sum of execution fees charged across all filled orders.
    total_slippage_bps: float
        Sum of absolute slippage (basis points vs arrival mid) across all
        filled orders.
    gross_profit: float
        Sum of positive realised PnL over closed trades.
    gross_loss: float
        Absolute sum of negative realised PnL over closed trades.
    """

    symbol: str
    steps: int
    starting_equity: float
    final_equity: float
    total_pnl: float
    returns_pct: float
    max_drawdown_pct: float
    trades_opened: int
    trades_closed: int
    wins: int
    losses: int
    flats: int
    approved: int
    rejected: int
    total_fees: float = 0.0
    total_slippage_bps: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    equity_curve: tuple[float, ...] = ()  # per-step equity, including the starting value

    @property
    def win_rate(self) -> float:
        """Fraction of closed trades that were wins (0.0 when none closed)."""
        closed = self.trades_closed
        return self.wins / closed if closed > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        """Gross profit divided by gross loss (0.0 when nothing was lost)."""
        if self.gross_loss <= 0.0:
            return 0.0
        return self.gross_profit / self.gross_loss

    @property
    def net_expectancy(self) -> float:
        """Average realised PnL per closed trade (0.0 when none closed)."""
        if self.trades_closed <= 0:
            return 0.0
        return (self.gross_profit - self.gross_loss) / self.trades_closed
