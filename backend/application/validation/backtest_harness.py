# backend/application/validation/backtest_harness.py
"""Validation harness for backtesting strategies.

Provides realistic fill modeling for microstructure strategies:
- Queue-position-aware fills (orders fill based on queue position)
- Market impact (temporary + permanent)
- Maker/taker fee modeling

Fill model assumptions (documented for P2-003):
- A passive (maker) order rests at its limit price. Its fill probability
  decays geometrically with queue position: ``0.5 ** queue_position`` scaled
  by ``queue_fill_probability``. Queue position is estimated as one slot per
  price tick away from the best price, bounded by ``max_queue_position``.
- An aggressive (taker) order fills immediately at the touch, but the fill
  price is moved by ``temporary_impact`` of the spread and a permanent impact
  proportional to order size is recorded on the result.
- Fees are linear in order size: maker pays ``maker_fee`` (negative = rebate),
  taker pays ``taker_fee``. A resting maker order that does not fill pays no fee.
- A maker fill may be partial: the filled ratio is capped by the available
  depth at the touch (``bid_size``/``ask_size`` / order size).

All randomness flows from a per-instance ``numpy.random.Generator`` seeded by
``HarnessConfig.seed``. Setting a seed makes every run reproducible; leaving it
``None`` draws fresh entropy per harness instance and never touches the global
NumPy state, so parallel or repeated runs cannot interfere with one another
(P2-003: randomness is seeded or controlled).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FillResult:
    """Result of a simulated fill."""

    filled: bool
    fill_price: float
    fill_ratio: float  # 0-1, fraction of order filled
    maker: bool  # True if maker (liquidity provider)
    fee: float
    queue_position: int  # position in queue (0 = best)
    impact: float  # permanent price impact


@dataclass(frozen=True, slots=True)
class OrderDecision:
    """A strategy's order decision for one step of the fill-aware backtest.

    ``side`` is ``"buy"`` or ``"sell"``; ``None`` means hold (no order).
    ``limit_price`` is the price at which the strategy is willing to trade;
    a passive order rests there, an aggressive order crosses the touch.
    ``size`` is the order quantity.
    """

    side: str | None
    limit_price: float = 0.0
    size: float = 1.0

    @property
    def is_order(self) -> bool:
        return self.side in ("buy", "sell")


@dataclass(frozen=True, slots=True)
class BookSnapshot:
    """A point-in-time order-book state for the fill-aware backtest."""

    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float


@dataclass(frozen=True, slots=True)
class BacktestResult:
    """Performance and microstructure diagnostics for a fill-aware run."""

    total_return: float
    sharpe: float
    max_drawdown: float
    win_rate: float
    n_trades: int
    n_signals: int
    n_fill_attempts: int
    n_filled: int
    fill_rate: float
    total_fees: float
    total_impact_bps: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_return": self.total_return,
            "sharpe": self.sharpe,
            "max_drawdown": self.max_drawdown,
            "win_rate": self.win_rate,
            "n_trades": self.n_trades,
            "n_signals": self.n_signals,
            "n_fill_attempts": self.n_fill_attempts,
            "n_filled": self.n_filled,
            "fill_rate": self.fill_rate,
            "total_fees": self.total_fees,
            "total_impact_bps": self.total_impact_bps,
        }


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    """Configuration for the validation harness.

    See the module docstring for the full fill-model assumptions.
    ``seed`` controls the per-instance generator: a fixed integer makes fills
    reproducible, ``None`` draws fresh entropy per harness instance.
    """

    maker_fee: float = -0.0002  # negative = rebate
    taker_fee: float = 0.0005
    temporary_impact: float = 0.1  # 10% of spread per unit
    permanent_impact: float = 0.01  # 1% permanent impact
    queue_fill_probability: float = 0.3  # probability of fill per queue step
    max_queue_position: int = 5
    seed: int | None = None


class ValidationHarness:
    """Validation harness with realistic fill modeling."""

    def __init__(self, config: HarnessConfig | None = None) -> None:
        self._config = config or HarnessConfig()
        self._rng: np.random.Generator = np.random.default_rng(self._config.seed)

    def simulate_fill(
        self,
        order_side: str,  # "buy" or "sell"
        order_price: float,
        order_size: float,
        best_bid: float,
        best_ask: float,
        bid_size: float,
        ask_size: float,
    ) -> FillResult:
        """Simulate a fill with realistic queue modeling."""
        spread = best_ask - best_bid

        if order_side == "buy":
            # Buy order
            if order_price < best_bid:
                # Order inside bid (maker)
                queue_pos = self._estimate_queue_position(order_price, best_bid, bid_size)
                fill_prob = self._config.queue_fill_probability * (0.5**queue_pos)
                filled = self._rng.random() < fill_prob
                if filled:
                    return FillResult(
                        filled=True,
                        fill_price=order_price,
                        fill_ratio=min(1.0, bid_size / order_size) if order_size > 0 else 0.0,
                        maker=True,
                        fee=self._config.maker_fee * order_size,
                        queue_position=queue_pos,
                        impact=0.0,
                    )
                return FillResult(
                    filled=False,
                    fill_price=0.0,
                    fill_ratio=0.0,
                    maker=True,
                    fee=0.0,
                    queue_position=queue_pos,
                    impact=0.0,
                )
            elif order_price >= best_ask:
                # Buy at or above ask (taker)
                impact = self._config.permanent_impact * order_size
                return FillResult(
                    filled=True,
                    fill_price=best_ask + spread * self._config.temporary_impact,
                    fill_ratio=1.0,
                    maker=False,
                    fee=self._config.taker_fee * order_size,
                    queue_position=0,
                    impact=impact,
                )
            else:
                # Between bid and ask (unlikely but handle)
                return FillResult(
                    filled=False,
                    fill_price=0.0,
                    fill_ratio=0.0,
                    maker=False,
                    fee=0.0,
                    queue_position=0,
                    impact=0.0,
                )
        else:
            # Sell order
            if order_price > best_ask:
                # Order inside ask (maker)
                queue_pos = self._estimate_queue_position(order_price, best_ask, ask_size)
                fill_prob = self._config.queue_fill_probability * (0.5**queue_pos)
                filled = self._rng.random() < fill_prob
                if filled:
                    return FillResult(
                        filled=True,
                        fill_price=order_price,
                        fill_ratio=min(1.0, ask_size / order_size) if order_size > 0 else 0.0,
                        maker=True,
                        fee=self._config.maker_fee * order_size,
                        queue_position=queue_pos,
                        impact=0.0,
                    )
                return FillResult(
                    filled=False,
                    fill_price=0.0,
                    fill_ratio=0.0,
                    maker=True,
                    fee=0.0,
                    queue_position=queue_pos,
                    impact=0.0,
                )
            elif order_price <= best_bid:
                # Sell at or below bid (taker)
                impact = self._config.permanent_impact * order_size
                return FillResult(
                    filled=True,
                    fill_price=best_bid - spread * self._config.temporary_impact,
                    fill_ratio=1.0,
                    maker=False,
                    fee=self._config.taker_fee * order_size,
                    queue_position=0,
                    impact=impact,
                )
            else:
                return FillResult(
                    filled=False,
                    fill_price=0.0,
                    fill_ratio=0.0,
                    maker=False,
                    fee=0.0,
                    queue_position=0,
                    impact=0.0,
                )

    def _estimate_queue_position(
        self, order_price: float, best_price: float, best_size: float
    ) -> int:
        """Estimate queue position based on price distance from best."""
        spread = abs(order_price - best_price)
        if spread <= 0:
            return 0
        # Simplified: 1 position per tick
        return min(int(spread * 100), self._config.max_queue_position)

    def backtest_ofi_strategy(
        self,
        ofi_series: np.ndarray,
        returns: np.ndarray,
        threshold: float = 0.1,
    ) -> dict[str, Any]:
        """Backtest a simple OFI-based strategy.

        Goes long when OFI > threshold, short when OFI < -threshold.
        """
        positions = np.where(ofi_series > threshold, 1, np.where(ofi_series < -threshold, -1, 0))
        strategy_returns = positions[:-1] * returns[1:]

        # Compute metrics
        total_return = float(np.sum(strategy_returns))
        sharpe = float(np.mean(strategy_returns) / (np.std(strategy_returns) + 1e-6) * np.sqrt(252))
        max_dd = float(self._max_drawdown(strategy_returns))
        win_rate = (
            float(np.sum(strategy_returns > 0) / len(strategy_returns))
            if len(strategy_returns) > 0
            else 0.0
        )

        return {
            "total_return": total_return,
            "sharpe": sharpe,
            "max_drawdown": max_dd,
            "win_rate": win_rate,
            "n_trades": int(np.sum(np.diff(positions) != 0)),
        }

    def backtest_microstructure(
        self,
        decisions: list[OrderDecision],
        books: list[BookSnapshot],
        *,
        initial_price: float,
    ) -> BacktestResult:
        """Backtest a strategy through realistic fills (P2-003).

        Every order decision is routed through :meth:`simulate_fill`, so fills
        pay the queue model, temporary/permanent impact, and maker/taker fees.
        The strategy expresses a target side each step; the harness submits on
        its behalf and accounts for the actual (possibly partial, possibly
        unfilled, fee-bearing) fills. Fill-allocation policy documented here:

        - A ``buy`` decision closes short exposure first, then opens long.
        - A ``sell`` decision closes long exposure first, then opens short.
        - Partial fills reduce the position by the filled ratio; unfilled
          orders contribute nothing (a passive order that never filled pays no
          fee, matching the fill model).
        - Integer unit assumption: ``size=1`` in every
          :class:`OrderDecision` means one unit of the asset; ``initial_price``
          is the unit price used to denominate returns.

        Seeded harnesses are fully reproducible; unseeded harnesses draw fresh
        entropy per instance but never the global NumPy state.
        """
        if len(decisions) != len(books):
            raise ValueError("decisions and books must be step-aligned")
        if initial_price <= 0.0:
            raise ValueError("initial_price must be positive")

        long_position = 0.0  # in units of the asset
        short_position = 0.0
        long_entry = 0.0  # avg entry price for open long exposure
        short_entry = 0.0
        realized_pnl = 0.0
        total_fees = 0.0
        total_impact_bps = 0.0
        n_signals = 0
        n_attempts = 0
        n_filled = 0
        n_round_trips = 0
        step_delta: list[float] = []

        for decision, book in zip(decisions, books, strict=True):
            if decision.side not in ("buy", "sell"):
                step_delta.append(0.0)
                continue
            n_signals += 1
            n_attempts += 1
            result = self.simulate_fill(
                order_side=decision.side,
                order_price=decision.limit_price,
                order_size=decision.size,
                best_bid=book.best_bid,
                best_ask=book.best_ask,
                bid_size=book.bid_size,
                ask_size=book.ask_size,
            )
            total_fees += result.fee
            total_impact_bps += (
                (result.impact / book.best_bid * 10_000.0) if book.best_bid > 0 else 0.0
            )
            if not result.filled or result.fill_ratio <= 0.0:
                step_delta.append(0.0)
                continue
            n_filled += 1
            filled = result.fill_ratio * decision.size

            before = (short_position, long_position)
            if decision.side == "buy":
                closed = min(short_position, filled)
                if closed > 0.0:
                    realized_pnl += (result.fill_price - short_entry) * closed
                    short_position -= closed
                    filled -= closed
                if filled > 0.0:
                    new_total = long_position + filled
                    long_entry = (
                        (long_entry * long_position + result.fill_price * filled) / new_total
                        if new_total > 0.0
                        else result.fill_price
                    )
                    long_position = new_total
            else:  # sell
                closed = min(long_position, filled)
                if closed > 0.0:
                    realized_pnl += (long_entry - result.fill_price) * closed
                    long_position -= closed
                    filled -= closed
                if filled > 0.0:
                    new_total = short_position + filled
                    short_entry = (
                        (short_entry * short_position + result.fill_price * filled) / new_total
                        if new_total > 0.0
                        else result.fill_price
                    )
                    short_position = new_total
            after = (short_position, long_position)

            # A round trip is completed when exposure on a side is removed
            # (either back to flat or flipped to the other side).
            if after[0] < before[0] or after[1] < before[1]:
                n_round_trips += 1

            # Step PnL is the increment in realized PnL since the last step.
            step_delta.append(realized_pnl - sum(step_delta))

        # Unrealised PnL of any still-open exposure at the final touch.
        final_book = books[-1]
        final_mid = (final_book.best_bid + final_book.best_ask) / 2.0
        unrealized = (final_mid - long_entry) * long_position + (
            short_entry - final_mid
        ) * short_position
        total_return = (realized_pnl + unrealized) / initial_price

        series = np.asarray(step_delta, dtype=float) / initial_price if step_delta else np.zeros(1)
        sharpe = (
            float(np.mean(series) / (np.std(series) + 1e-9) * np.sqrt(252))
            if len(series) > 0
            else 0.0
        )
        max_dd = float(self._max_drawdown(series))
        traded_deltas = [d for d in step_delta if d != 0.0]
        win_rate = (
            float(sum(1.0 for d in traded_deltas if d > 0.0) / len(traded_deltas))
            if traded_deltas
            else 0.0
        )
        fill_rate = n_filled / n_attempts if n_attempts > 0 else 0.0

        return BacktestResult(
            total_return=total_return,
            sharpe=sharpe,
            max_drawdown=max_dd,
            win_rate=win_rate,
            n_trades=n_round_trips,
            n_signals=n_signals,
            n_fill_attempts=n_attempts,
            n_filled=n_filled,
            fill_rate=fill_rate,
            total_fees=total_fees,
            total_impact_bps=total_impact_bps,
        )

    def _max_drawdown(self, returns: np.ndarray) -> float:
        """Compute maximum drawdown."""
        cumulative = np.cumsum(returns)
        running_max = np.maximum.accumulate(cumulative)
        drawdown = cumulative - running_max
        return float(-np.min(drawdown)) if len(drawdown) > 0 else 0.0
