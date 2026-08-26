# backend/application/validation/triple_barrier.py
"""Triple-barrier labelling and meta-labelling (integration #25).

A sample is a window of price observations starting at an event time. Three
barriers close the window: an upper profit-take barrier, a lower stop-loss
barrier, and a vertical time barrier. The label is the first barrier touched.
Meta-labelling layers a secondary model on top of a primary *direction* model:
the primary decides the side, the secondary decides, conditional on the
primary's side, whether the bet is *worth taking* (1) or not (0). The final
bet only enters when the secondary model is confident, exactly the
Lopez de Prado ``mlbetting`` recipe.

This module is deliberately dependency-free (stdlib only): it works on plain
price sequences with a NumPy-style API, and the label horizon is measured in
index steps so it composes with the purged-CV interval semantics already in
the codebase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.domain.decision.proposal import ProposedActionType


@dataclass(frozen=True, slots=True)
class TripleBarrierConfig:
    """Barrier layout for a labelling run.

    Attributes
    ----------
    profit_multiple: float
        Upper barrier offset as a multiple of ``volatility``.
    loss_multiple: float
        Lower barrier offset as a multiple of ``volatility``.
    volatility: float
        Barrier distance anchor in price units. When 0, barriers are
        absolute offsets (profit/loss given directly).
    profit_distance: float
        Absolute upper-barrier distance in price units when ``volatility``
        is 0.
    loss_distance: float
        Absolute lower-barrier distance in price units when ``volatility``
        is 0.
    max_steps: int
        Vertical barrier: the furthest a label may look ahead (in steps).
        When 0, no vertical barrier is applied and the label is defined only
        by the horizontal barriers.
    """

    profit_multiple: float = 2.0
    loss_multiple: float = 2.0
    volatility: float = 0.0
    profit_distance: float = 0.0
    loss_distance: float = 0.0
    max_steps: int = 0

    def __post_init__(self) -> None:
        if self.profit_multiple <= 0.0:
            raise ValueError("profit_multiple must be positive")
        if self.loss_multiple <= 0.0:
            raise ValueError("loss_multiple must be positive")
        if self.volatility < 0.0:
            raise ValueError("volatility cannot be negative")
        if self.profit_distance < 0.0 or self.loss_distance < 0.0:
            raise ValueError("barrier distances cannot be negative")
        if self.max_steps < 0:
            raise ValueError("max_steps cannot be negative")
        if self.volatility == 0.0 and self.profit_distance <= 0.0:
            raise ValueError("profit barrier needs volatility or profit_distance")
        if self.volatility == 0.0 and self.loss_distance <= 0.0:
            raise ValueError("loss barrier needs volatility or loss_distance")

    @property
    def profit_offset(self) -> float:
        """Upper barrier offset in price units."""
        if self.volatility > 0.0:
            return self.volatility * self.profit_multiple
        return self.profit_distance

    @property
    def loss_offset(self) -> float:
        """Lower barrier offset in price units."""
        if self.volatility > 0.0:
            return self.volatility * self.loss_multiple
        return self.loss_distance


@dataclass(frozen=True, slots=True)
class TripleBarrierLabel:
    """Label outcome for one sample.

    Attributes
    ----------
    outcome: float
        +1 upper barrier touched first, -1 lower barrier touched first,
        0 vertical barrier touched first.
    side: int
        Sign of the price change over the window (measure of direction).
    barrier: str
        Which barrier closed the window: "upper", "lower", or "vertical".
    exit_step: int
        Index offset (relative to the sample start) where the barrier was
        touched; equals ``max_steps`` on a vertical touch.
    exit_price: float
        Price at the touching observation.
    pnl: float
        P&L of holding one unit through the window in price units.
    """

    outcome: float
    side: int
    barrier: str
    exit_step: int
    exit_price: float
    pnl: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "side": self.side,
            "barrier": self.barrier,
            "exit_step": self.exit_step,
            "exit_price": round(self.exit_price, 8),
            "pnl": round(self.pnl, 8),
        }


def _barrier_offsets(config: TripleBarrierConfig) -> tuple[float, float]:
    return config.profit_offset, config.loss_offset


def label_triple_barrier(
    prices: list[float],
    config: TripleBarrierConfig,
    *,
    start: int = 0,
) -> TripleBarrierLabel | None:
    """Label a single sample of ``prices`` with triple-barrier semantics.

    ``prices`` is a slice starting at the event. Returns None when the slice
    is too short to resolve (no horizontal barrier reached and the index
    exhausts before ``max_steps`` on a bounded run without an explicit
    vertical barrier).
    """
    if not prices:
        return None
    upward, downward = _barrier_offsets(config)
    anchor = prices[0]
    upper = anchor + upward
    lower = anchor - downward

    horizon = len(prices) if config.max_steps == 0 else min(len(prices), config.max_steps + 1)
    if horizon < 2:
        return None

    last_idx = len(prices) - 1
    for i in range(1, horizon):
        px = prices[i]
        if px >= upper:
            return TripleBarrierLabel(
                outcome=1.0,
                side=1,
                barrier="upper",
                exit_step=i,
                exit_price=px,
                pnl=px - anchor,
            )
        if px <= lower:
            return TripleBarrierLabel(
                outcome=-1.0,
                side=-1,
                barrier="lower",
                exit_step=i,
                exit_price=px,
                pnl=px - anchor,
            )
        if config.max_steps > 0 and i == config.max_steps:
            return TripleBarrierLabel(
                outcome=0.0,
                side=1 if px >= anchor else -1 if px < anchor else 0,
                barrier="vertical",
                exit_step=i,
                exit_price=px,
                pnl=px - anchor,
            )

    if config.max_steps == 0:
        return None
    px = prices[last_idx]
    return TripleBarrierLabel(
        outcome=0.0,
        side=1 if px >= anchor else -1 if px < anchor else 0,
        barrier="vertical",
        exit_step=last_idx,
        exit_price=px,
        pnl=px - anchor,
    )


def label_series(
    prices: list[float],
    config: TripleBarrierConfig,
    *,
    starts: list[int] | None = None,
) -> list[TripleBarrierLabel | None]:
    """Label every possible start (or an explicit set of ``starts``)."""
    starts = starts or list(range(len(prices)))
    out: list[TripleBarrierLabel | None] = []
    for s in starts:
        if s >= len(prices):
            out.append(None)
            continue
        out.append(label_triple_barrier(prices[s:], config))
    return out


def side_for_outcome(outcome: float) -> ProposedActionType | None:
    """Map a meta-label to the executable side (for the betting pipeline)."""
    if outcome > 0.0:
        return ProposedActionType.ENTER_LONG
    if outcome < 0.0:
        return ProposedActionType.ENTER_SHORT
    return None


@dataclass(frozen=True, slots=True)
class MetaLabel:
    """Target for the secondary (bet-sizing) model, per Lopez de Prado.

    A meta-label conditions the bet on the primary model's *direction*: the
    secondary model is only asked whether a bet in the primary's predicted
    direction is worth taking (1) or not (0). It never predicts direction
    itself — that separation is exactly what lets the betting model size
    modestly but accurately.
    """

    primary_side: int
    bet: int
    pnl: float
    touched_barrier: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary_side": self.primary_side,
            "bet": self.bet,
            "pnl": round(self.pnl, 8),
            "touched_barrier": self.touched_barrier,
        }


def meta_label(
    label: TripleBarrierLabel,
    *,
    predicted_side: int,
) -> MetaLabel:
    """Derive a meta-label for a primary-prediction of ``predicted_side``.

    The bet is 1 when the primary's predicted direction turned out to be
    profitable, 0 otherwise. For a horizontal-barrier resolution the outcome's
    sign states profitability directly (the favorable barrier was hit in the
    predicted direction); for a vertical-barrier resolution the window's net
    price movement decides. P&L is signed in the *primary's* direction so the
    betting model sees a coherent, directional target.
    """
    direction = 1 if predicted_side > 0 else -1
    if label.outcome != 0.0:
        profitable = label.outcome * direction > 0
    else:
        profitable = label.pnl * direction >= 0.0
    return MetaLabel(
        primary_side=predicted_side,
        bet=1 if profitable else 0,
        pnl=label.pnl * direction,
        touched_barrier=label.barrier,
    )
