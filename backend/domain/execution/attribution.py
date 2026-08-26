# backend/domain/execution/attribution.py
"""Execution attribution: decompose a closed trade's realized PnL.

The realised net PnL of a trade is broken into:

- ``gross_pnl`` — PnL from price moves alone (before any cost).
- ``alpha_pnl`` — the PnL that would have been earned if both fills had
  happened exactly at the arrival mid (the decision-time price): pure
  market/strategy return, uncontaminated by execution.
- ``entry_slippage`` — cost of entering away from the entry arrival mid.
- ``exit_slippage`` — cost of exiting away from the exit arrival mid.

The identity is exact and verifiable per trade:

    gross_pnl = alpha_pnl - entry_slippage - exit_slippage
    net_pnl   = gross_pnl - fees - funding_cost

Slippage is expressed as a positive cost: a buy that fills above arrival, or
a sell that fills below arrival, is a cost paid for immediacy. The sign
convention lets the decompositions above hold exactly for both sides.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.domain.execution.order import OrderSide, signed_direction
from backend.domain.execution.trade_record import TradeRecord


@dataclass(frozen=True, slots=True)
class TradeAttribution:
    """Signed PnL decomposition of one closed trade.

    Attributes
    ----------
    trade_id: str
        Ledger trade id.
    symbol: str
        Market symbol.
    side: OrderSide
        Long or short.
    gross_pnl: float
        PnL before all costs.
    alpha_pnl: float
        PnL from returns alone, priced at arrival mids.
    entry_slippage: float
        Positive cost of the entry fill vs the entry arrival mid.
    exit_slippage: float
        Positive cost of the exit fill vs the exit arrival mid.
    entry_price_improvement: float
        Benefit when fill improved on arrival (maker inside spread).
    exit_price_improvement: float
        Benefit when exit improved on arrival.
    fee: float
        Total execution fee.
    funding_cost: float
        Total funding/carry cost.
    net_pnl: float
        PnL after all costs (equals ``TradeRecord.realized_pnl``).
    """

    trade_id: str
    symbol: str
    side: OrderSide
    gross_pnl: float
    alpha_pnl: float
    entry_slippage: float
    exit_slippage: float
    fee: float
    funding_cost: float
    net_pnl: float
    entry_price_improvement: float = 0.0
    exit_price_improvement: float = 0.0

    @property
    def total_slippage(self) -> float:
        """Total slippage cost (entry + exit)."""
        return self.entry_slippage + self.exit_slippage

    @property
    def total_price_improvement(self) -> float:
        return self.entry_price_improvement + self.exit_price_improvement

    def as_dict(self) -> dict[str, Any]:
        """Serialise to a plain dictionary for observability."""
        return {
            "trade_id": self.trade_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "gross_pnl": self.gross_pnl,
            "alpha_pnl": self.alpha_pnl,
            "entry_slippage": self.entry_slippage,
            "exit_slippage": self.exit_slippage,
            "entry_price_improvement": self.entry_price_improvement,
            "exit_price_improvement": self.exit_price_improvement,
            "total_slippage": self.total_slippage,
            "total_price_improvement": self.total_price_improvement,
            "fee": self.fee,
            "funding_cost": self.funding_cost,
            "net_pnl": self.net_pnl,
        }


def attribute_trade(
    trade: TradeRecord, entry_arrival: float | None, exit_arrival: float | None
) -> TradeAttribution:
    """Decompose a closed trade into its PnL components.

    ``entry_arrival`` and ``exit_arrival`` are the mid prices at the entry and
    exit decision times (the arrival prices captured by the gateway). When an
    arrival price is absent (e.g. legacy ledger rows), a zero split is
    assumed: the missing slippage is attributed to alpha. When a fill improves
    on its arrival (e.g. a passive maker fill inside the spread), slippage is
    floored at zero and the benefit is attributed to alpha, keeping the
    "slippage is a cost" convention monotone.

    The two identities hold exactly by construction:

        gross_pnl = alpha_pnl - entry_slippage - exit_slippage
        net_pnl   = gross_pnl - fee - funding_cost
    """
    direction = signed_direction(trade.side)
    qty = trade.quantity

    entry_slip = 0.0
    exit_slip = 0.0
    entry_improve = 0.0
    exit_improve = 0.0
    if entry_arrival is not None:
        raw_entry = direction * (trade.entry_price - entry_arrival) * qty
        entry_slip = max(raw_entry, 0.0)
        entry_improve = max(-raw_entry, 0.0)
    if exit_arrival is not None and trade.exit_price is not None:
        raw_exit = -direction * (trade.exit_price - exit_arrival) * qty
        exit_slip = max(raw_exit, 0.0)
        exit_improve = max(-raw_exit, 0.0)

    gross = trade.gross_pnl if trade.gross_pnl is not None else 0.0
    fee = trade.fee if trade.fee is not None else 0.0
    funding = trade.funding_cost if trade.funding_cost is not None else 0.0

    # Reconstruct alpha so the identity holds exactly given the observed
    # gross: what the market move would have returned at arrival prices.
    alpha = gross + entry_slip + exit_slip

    net = gross - fee - funding
    return TradeAttribution(
        trade_id=trade.trade_id,
        symbol=trade.symbol,
        side=trade.side,
        gross_pnl=gross,
        alpha_pnl=alpha,
        entry_slippage=entry_slip,
        exit_slippage=exit_slip,
        fee=fee,
        funding_cost=funding,
        net_pnl=net,
        entry_price_improvement=entry_improve,
        exit_price_improvement=exit_improve,
    )
