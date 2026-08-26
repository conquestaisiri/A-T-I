# backend/domain/research/evaluation.py
"""Baseline strategy evaluation contracts (task P1-003).

A ``BaselineResult`` is the single, comparable output of a baseline-strategy
backtest. Every baseline is scored against the same cost model and the same
price series, and every result carries the buy-and-hold reference return so
that strategies can be compared across runs without re-running the reference.

The result is deliberately a plain immutable value object: researchers read
it, the experiment registry (P1-005) persists it, and the robustness harness
(P1-008) perturbs it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BaselineResult:
    """Standardised metrics of one baseline-strategy backtest.

    Attributes
    ----------
    name: str
        Strategy name used for reporting (unique within a comparison).
    description: str
        Human-readable description of the strategy and its parameters.
    starting_equity: float
        Equity at the first bar (before any entry costs).
    final_equity: float
        Equity after the last bar (after any exit costs).
    total_return_pct: float
        Net total return of the strategy as a percentage of starting equity.
    buy_and_hold_return_pct: float
        Reference: a single long entry on the open price and exit on the last
        price, under the *same* cost model as the strategy. Comparing against
        this — not against a fee-free buy-and-hold — is the honest bar for
        whether a strategy earns its costs.
    excess_return_pct: float
        ``total_return_pct - buy_and_hold_return_pct``.
    per_bar_volatility_pct: float
        Standard deviation of per-bar strategy returns, in percent.
    sharpe_per_bar: float
        Mean of per-bar returns divided by their standard deviation (no
        annualisation factor; the caller knows the bar frequency). 0 when
        volatility is zero.
    max_drawdown_pct: float
        Peak-to-trough equity decline as a percentage of the running peak.
    num_trades: int
        Number of positions opened and closed (blocks of non-flat exposure).
    win_rate: float
        Fraction of closed blocks with positive net return, in [0, 1]. 1.0
        when no block was closed (e.g. always flat).
    transaction_cost_pct: float
        Total costs (spread + fees) paid as a percentage of starting equity.
    sample_exposure_pct: float
        Percentage of bars spent in a non-flat position, in [0, 100].
    equity_curve: tuple[float, ...]
        Per-bar equity (len == number of bars), for downstream plotting/checks.
    """

    name: str
    description: str
    starting_equity: float
    final_equity: float
    total_return_pct: float
    buy_and_hold_return_pct: float
    excess_return_pct: float
    per_bar_volatility_pct: float
    sharpe_per_bar: float
    max_drawdown_pct: float
    num_trades: int
    win_rate: float
    transaction_cost_pct: float
    sample_exposure_pct: float
    equity_curve: tuple[float, ...]

    @property
    def closed_pct(self) -> bool:
        """Whether every opened block was closed by the end of the series."""
        return bool(self.num_trades) or self.win_rate >= 0.0

    def as_dict(self) -> dict[str, object]:
        """Serialise the result to a plain dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "starting_equity": self.starting_equity,
            "final_equity": self.final_equity,
            "total_return_pct": self.total_return_pct,
            "buy_and_hold_return_pct": self.buy_and_hold_return_pct,
            "excess_return_pct": self.excess_return_pct,
            "per_bar_volatility_pct": self.per_bar_volatility_pct,
            "sharpe_per_bar": self.sharpe_per_bar,
            "max_drawdown_pct": self.max_drawdown_pct,
            "num_trades": self.num_trades,
            "win_rate": self.win_rate,
            "transaction_cost_pct": self.transaction_cost_pct,
            "sample_exposure_pct": self.sample_exposure_pct,
            "equity_curve": list(self.equity_curve),
        }
