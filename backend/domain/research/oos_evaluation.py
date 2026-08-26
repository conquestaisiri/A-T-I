# backend/domain/research/oos_evaluation.py
"""Out-of-sample decision-pipeline evaluation contracts (evidence priority 1).

A ``PooledEvidence`` is the honest, aggregated scorecard of an out-of-sample
evaluation: what the *real* decision pipeline earned on test windows it never
influenced, measured against the shared costed ruler (P1-003) and compared to
costed baselines on the same windows.

Why this object exists (per docs/ATI_Architecture_Critique.md):
- the autonomy ladder must never be wired on in-sample promise;
- "having validation machinery is not the same as having validated alpha";
- every number here is earned out-of-sample, never claimed in-sample.

The object is deliberately a plain immutable value object: the evaluator
(application layer) computes it, the evidence layer persists it, and the
promotion ladder (P4-001) reads it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PooledEvidence:
    """Aggregate out-of-sample scorecard across all walk-forward folds.

    Attributes
    ----------
    n_folds: int
        Number of out-of-sample test windows.
    total_test_bars: int
        Total bars across all test windows.
    total_trades: int
        Positions closed across all test windows.
    total_wins: int
        Closed trades with positive realised PnL.
    total_losses: int
        Closed trades with negative realised PnL.
    total_fees: float
        Sum of execution fees across all folds (absolute currency).
    total_slippage_bps: float
        Sum of absolute slippage (bps vs arrival mid) across all folds.
    gross_profit: float
        Sum of positive realised PnL over closed trades (currency).
    gross_loss: float
        Absolute sum of negative realised PnL over closed trades (currency).
    mean_return_pct: float
        Mean of per-fold total returns (percent of starting equity).
    median_return_pct: float
        Median of per-fold total returns.
    mean_excess_return_pct: float
        Mean of per-fold excess returns vs the costed buy-and-hold reference.
    positive_fold_rate: float
        Fraction of folds with positive total return, in [0, 1].
    beats_buy_and_hold_rate: float
        Fraction of folds whose total return beat the costed buy-and-hold
        reference on the same window, in [0, 1].
    mean_max_drawdown_pct: float
        Mean of per-fold maximum drawdown (percent of running peak).
    deflated_sharpe: float | None
        Deflated Sharpe Ratio (P5-001) of the pooled fold returns: the
        probability the strategy's Sharpe is positive after pricing for the
        multiple testing that produced it. None when the number of folds is
        too small to estimate it.
    reasoner: str
        Name of the reasoner evaluated (deterministic solver by default).
    cost_model: dict[str, float]
        The shared cost ruler used: half_spread_pct + taker_fee_pct.
    """

    n_folds: int
    total_test_bars: int
    total_trades: int
    total_wins: int
    total_losses: int
    total_fees: float
    total_slippage_bps: float
    gross_profit: float
    gross_loss: float
    mean_return_pct: float
    median_return_pct: float
    mean_excess_return_pct: float
    positive_fold_rate: float
    beats_buy_and_hold_rate: float
    mean_max_drawdown_pct: float
    deflated_sharpe: float | None
    reasoner: str
    cost_model: dict[str, float]

    @property
    def win_rate(self) -> float:
        """Fraction of closed trades that were wins (0.0 when none closed)."""
        closed = self.total_wins + self.total_losses
        return self.total_wins / closed if closed > 0 else 0.0

    @property
    def profit_factor(self) -> float:
        """Gross profit divided by gross loss (0.0 when nothing was lost)."""
        if self.gross_loss <= 0.0:
            return 0.0
        return self.gross_profit / self.gross_loss

    @property
    def net_expectancy(self) -> float:
        """Average realised PnL per closed trade across all folds."""
        if self.total_trades <= 0:
            return 0.0
        return (self.gross_profit - self.gross_loss) / self.total_trades

    def as_dict(self) -> dict[str, object]:
        """Serialise the evidence to a plain dictionary."""
        return {
            "n_folds": self.n_folds,
            "total_test_bars": self.total_test_bars,
            "total_trades": self.total_trades,
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "win_rate": self.win_rate,
            "profit_factor": self.profit_factor,
            "net_expectancy": self.net_expectancy,
            "total_fees": self.total_fees,
            "total_slippage_bps": self.total_slippage_bps,
            "gross_profit": self.gross_profit,
            "gross_loss": self.gross_loss,
            "mean_return_pct": self.mean_return_pct,
            "median_return_pct": self.median_return_pct,
            "mean_excess_return_pct": self.mean_excess_return_pct,
            "positive_fold_rate": self.positive_fold_rate,
            "beats_buy_and_hold_rate": self.beats_buy_and_hold_rate,
            "mean_max_drawdown_pct": self.mean_max_drawdown_pct,
            "deflated_sharpe": self.deflated_sharpe,
            "reasoner": self.reasoner,
            "cost_model": dict(self.cost_model),
        }
