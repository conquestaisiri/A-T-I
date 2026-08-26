# backend/application/research/baseline_evaluation.py
"""Baseline strategy evaluation suite (task P1-003).

Baselines are the null hypothesis of the research factory: a few dumb,
deterministic strategies every real model must beat *after costs*. They exist
so that:

1. **a claim of an edge is never made against a free buy-and-hold**, it is
   made against a costed reference and a costed set of simple strategies;
2. the evaluation harness and its costs are shared, so every baseline (and
   later every model) is measured by exactly the same ruler.

Design rules
------------
- Each strategy is a pure function ``price series -> target positions`` in
  ``{-1, 0, +1}`` (short / flat / long). Signals are causal by construction:
  the target at bar ``i`` may only depend on prices up to and including
  ``i``.
- The evaluator charges **realistic costs on every transition**: entering a
  position pays half-spread + taker fee on notional, exiting pays the same.
  Costs are taken from :class:`EvaluationCosts`; ``realistic()`` returns
  representative values so researchers start honest by default.
- All strategies run on the *same* price series with the *same* cost model,
  and every :class:`BaselineResult` carries the costed buy-and-hold reference
  so results are directly comparable.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from backend.domain.research.evaluation import BaselineResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EvaluationCosts:
    """Transaction-cost model applied to every trade.

    Attributes
    ----------
    half_spread_pct: float
        Half of the bid/ask spread, as a fraction of notional, charged on
        entry and on exit (a round trip therefore pays the full spread).
    taker_fee_pct: float
        Venue taker fee as a fraction of notional, charged on each fill.
    """

    half_spread_pct: float
    taker_fee_pct: float

    @classmethod
    def realistic(cls) -> EvaluationCosts:
        """Representative perpetual-futures costs (taker pass-through).

        2 bps half-spread each side and 4 bps taker fee each fill; per side
        the researcher pays spread-half + fee = 6 bps, a round trip is
        12 bps. Values are representative, never a claim about a venue.
        """
        return cls(half_spread_pct=0.0002, taker_fee_pct=0.0004)

    @classmethod
    def free(cls) -> EvaluationCosts:
        """Zero-cost model for structural sanity checks (never for claims)."""
        return cls(half_spread_pct=0.0, taker_fee_pct=0.0)


class BaselineStrategy(Protocol):
    """A causal baseline: prices in, target positions out."""

    name: str

    def targets(self, prices: Sequence[float]) -> Sequence[float]:
        """Return one target in {-1, 0, +1} per price bar (causal)."""
        ...

    def describe(self) -> str:
        """Human-readable description for reporting/registry."""
        ...


# -- concrete baselines -----------------------------------------------------


class AlwaysFlatBaseline:
    """Never holds a position. Returns zero (minus nothing) by construction.

    Measures the cost of *doing nothing*: flat equity means a strategy that
    is not flat must overcome the costs of every flip it makes.
    """

    name = "always_flat"

    def targets(self, prices: Sequence[float]) -> Sequence[float]:
        return tuple(0.0 for _ in prices)

    def describe(self) -> str:
        return "Never holds a position; zero-cost reference of inaction."


class BuyAndHoldBaseline:
    """Long from the first bar to the last.

    The classic bar a strategy must clear. Because costs are charged on entry
    and exit, buy-and-hold here is not free: it proves whether the market went
    up *net of* spread and fees.
    """

    name = "buy_and_hold"

    def targets(self, prices: Sequence[float]) -> Sequence[float]:
        return tuple(1.0 for _ in prices)

    def describe(self) -> str:
        return "Long the first bar to the last; the market reference after costs."


class MovingAverageCrossoverBaseline:
    """Long/short/flat on a fast/slow simple moving-average crossover.

    Causal: the MA at bar ``i`` is computed over prices ``(i - window + 1) ..
    i`` and produces the target for bar ``i`` only after that bar's price is
    known. Bars before the slow window is full are flat.
    """

    name = "ma_crossover"

    def __init__(self, fast: int = 5, slow: int = 20) -> None:
        if fast < 1 or slow < 1:
            raise ValueError("moving-average windows must be positive")
        if fast >= slow:
            raise ValueError("fast window must be shorter than slow window")
        self._fast = fast
        self._slow = slow

    def targets(self, prices: Sequence[float]) -> Sequence[float]:
        targets = []
        for i in range(len(prices)):
            if i + 1 < self._slow:
                targets.append(0.0)
                continue
            fast = sum(prices[i - self._fast + 1 : i + 1]) / self._fast
            slow = sum(prices[i - self._slow + 1 : i + 1]) / self._slow
            if fast > slow:
                targets.append(1.0)
            elif fast < slow:
                targets.append(-1.0)
            else:
                targets.append(0.0)
        return tuple(targets)

    def describe(self) -> str:
        return f"Long/short on {self._fast}/{self._slow} simple MA crossover."


class MomentumBaseline:
    """Long/flat/short on price momentum over a lookback window.

    Causal: at bar ``i`` the target depends only on the price ``lookback``
    bars behind and the current bar's price. Bars before ``lookback`` bars
    exist are flat.
    """

    name = "momentum"

    def __init__(self, lookback: int = 10) -> None:
        if lookback < 1:
            raise ValueError("lookback must be positive")
        self._lookback = lookback

    def targets(self, prices: Sequence[float]) -> Sequence[float]:
        targets = []
        for i in range(len(prices)):
            if i < self._lookback:
                targets.append(0.0)
                continue
            if prices[i] > prices[i - self._lookback]:
                targets.append(1.0)
            elif prices[i] < prices[i - self._lookback]:
                targets.append(-1.0)
            else:
                targets.append(0.0)
        return tuple(targets)

    def describe(self) -> str:
        return f"Long/flat/short on {self._lookback}-bar price momentum."


# -- the harness ------------------------------------------------------------


class BaselineEvaluator:
    """Run a baseline over a price series and score it under one cost model.

    The evaluator is deterministic: the same (prices, strategy, costs) always
    produces the same equity curve and the same metrics.
    """

    def __init__(self, costs: EvaluationCosts | None = None) -> None:
        self._costs = costs or EvaluationCosts.realistic()

    def evaluate(
        self,
        *,
        strategy: BaselineStrategy,
        prices: Sequence[float],
        starting_equity: float = 100_000.0,
    ) -> BaselineResult:
        """Backtest ``strategy`` on ``prices`` and return comparable metrics."""
        if starting_equity <= 0:
            raise ValueError("starting_equity must be positive")
        if len(prices) < 2:
            raise ValueError("prices must contain at least two bars")
        if any(p <= 0 for p in prices):
            raise ValueError("prices must be strictly positive")

        targets = _validated_targets(strategy.targets(prices), len(prices))
        equity, costs_paid, trades, wins, exposure = self._simulate(
            prices, targets, starting_equity
        )
        reference = self._buy_and_hold(prices, starting_equity)

        per_bar_returns = _per_bar_returns(equity)
        vol_raw = _std_dev(per_bar_returns)
        vol_pct = vol_raw * 100.0
        sharpe = _mean(per_bar_returns) / vol_raw if vol_raw > 0.0 else 0.0
        drawdown = _max_drawdown_pct(equity)

        total_return = (equity[-1] / starting_equity - 1.0) * 100.0
        reference_return = (reference[-1] / starting_equity - 1.0) * 100.0
        num_trades = len(trades)
        win_rate = (wins / num_trades) if num_trades else 1.0
        exposure_pct = (exposure / (len(prices) - 1)) * 100.0 if len(prices) > 1 else 0.0

        return BaselineResult(
            name=str(strategy.name),
            description=strategy.describe(),
            starting_equity=starting_equity,
            final_equity=round(equity[-1], 6),
            total_return_pct=round(total_return, 6),
            buy_and_hold_return_pct=round(reference_return, 6),
            excess_return_pct=round(total_return - reference_return, 6),
            per_bar_volatility_pct=round(vol_pct, 6),
            sharpe_per_bar=round(sharpe, 6),
            max_drawdown_pct=round(drawdown, 6),
            num_trades=num_trades,
            win_rate=round(win_rate, 6),
            transaction_cost_pct=round((costs_paid / starting_equity) * 100.0, 6),
            sample_exposure_pct=round(exposure_pct, 6),
            equity_curve=tuple(round(e, 6) for e in equity),
        )

    # -- simulation ---------------------------------------------------------

    def _simulate(
        self,
        prices: Sequence[float],
        targets: Sequence[float],
        starting_equity: float,
    ) -> tuple[list[float], float, list[float], int, int]:
        """Walk the price series applying ``targets`` with real costs.

        A position is opened when the target leaves 0 and closed when it
        returns to 0 or flips sign. Every fill — entry or exit — pays
        ``half_spread + taker_fee`` on the notional at risk (``equity``).
        ``position`` carries direction: long gains ``notional * change`` on a
        rise, short loses it. The position held over bar ``i`` is the target
        of bar ``i - 1``, so signals never use the bar they trade.
        """
        equity = starting_equity
        curve = [equity]
        costs_paid = 0.0
        trades: list[float] = []
        wins = 0
        exposure_bars = 0

        position = targets[0]
        open_notional = 0.0
        block_equity = 0.0
        if position != 0.0:
            cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
            equity -= cost
            costs_paid += cost
            open_notional = equity
            block_equity = equity
            exposure_bars += 1

        for i in range(1, len(prices)):
            price = prices[i]
            prev = prices[i - 1]

            # Realize PnL for the position held over (i-1, i].
            if position != 0.0:
                change = (price - prev) / prev
                equity += open_notional * change * position
                open_notional = equity

            new_position = targets[i]
            if new_position != position:
                if position == 0.0:
                    # Open a position at this bar's price.
                    cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
                    equity -= cost
                    costs_paid += cost
                    open_notional = equity
                    block_equity = equity
                elif new_position == 0.0:
                    # Close the position, charging the same per-side cost.
                    cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
                    equity -= cost
                    costs_paid += cost
                    block_return = equity / block_equity - 1.0
                    trades.append(block_return)
                    if block_return > 0.0:
                        wins += 1
                    position = 0.0
                    open_notional = 0.0
                else:
                    # Flip sign directly: close and reopen at the same price.
                    cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
                    equity -= cost
                    costs_paid += cost
                    block_return = equity / block_equity - 1.0
                    trades.append(block_return)
                    if block_return > 0.0:
                        wins += 1
                    cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
                    equity -= cost
                    costs_paid += cost
                    open_notional = equity
                    block_equity = equity
                position = new_position

            if position != 0.0:
                exposure_bars += 1

            curve.append(equity)

        # Close any position still open at the end of the series.
        if position != 0.0:
            cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
            equity -= cost
            costs_paid += cost
            block_return = equity / block_equity - 1.0
            trades.append(block_return)
            if block_return > 0.0:
                wins += 1
            curve[-1] = round(equity, 6)

        curve[-1] = round(equity, 6)
        return curve, costs_paid, trades, wins, exposure_bars

    def _buy_and_hold(self, prices: Sequence[float], starting_equity: float) -> list[float]:
        """Costed long buy-and-hold equity curve (the per-run reference)."""
        curve = [starting_equity]
        equity = starting_equity
        entry_cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
        equity -= entry_cost
        equity = equity * prices[-1] / prices[0]
        exit_cost = (self._costs.half_spread_pct + self._costs.taker_fee_pct) * equity
        equity -= exit_cost
        curve.append(round(equity, 6))
        return curve


def _validated_targets(targets: Sequence[float], n: int) -> tuple[float, ...]:
    """Validate and normalise a target sequence."""
    if len(targets) != n:
        raise ValueError("strategy returned a target per price bar")
    out = []
    for t in targets:
        if t not in (-1.0, 0.0, 1.0):
            raise ValueError(f"invalid target {t!r}; must be in {{-1, 0, 1}}")
        out.append(float(t))
    return tuple(out)


def _per_bar_returns(equity: Sequence[float]) -> list[float]:
    returns = []
    for i in range(1, len(equity)):
        prev = equity[i - 1]
        if prev <= 0:
            returns.append(0.0)
        else:
            returns.append(equity[i] / prev - 1.0)
    return returns


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std_dev(values: Sequence[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    total = 0.0
    for v in values:
        total += (v - mean) ** 2
    return math.sqrt(total / (len(values) - 1))


def _max_drawdown_pct(equity: Sequence[float]) -> float:
    peak = equity[0]
    worst = 0.0
    for value in equity:
        if value > peak:
            peak = value
        if peak > 0:
            drawdown = (peak - value) / peak
            if drawdown > worst:
                worst = drawdown
    return worst * 100.0


def compare_strategies(
    *,
    prices: Sequence[float],
    strategies: Sequence[BaselineStrategy],
    costs: EvaluationCosts | None = None,
    starting_equity: float = 100_000.0,
) -> list[BaselineResult]:
    """Run every strategy on the same prices/costs and sort by excess return.

    Returns results highest-excess-first so the research factory presents the
    same ranking regardless of who calls it. The costed buy-and-hold reference
    and the always-flat inaction baseline are always included, since no
    comparison is meaningful without them.
    """
    evaluator = BaselineEvaluator(costs)
    runners = list(strategies) + [BuyAndHoldBaseline()]
    seen: set[str] = set()
    results: list[BaselineResult] = []
    for strat in runners:
        if strat.name in seen:
            continue
        seen.add(strat.name)
        results.append(
            evaluator.evaluate(strategy=strat, prices=prices, starting_equity=starting_equity)
        )
    results.sort(key=lambda r: r.excess_return_pct, reverse=True)
    return results
