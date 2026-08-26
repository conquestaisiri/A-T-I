# backend/domain/execution/pnl.py
"""Signed PnL accounting (task P0-009).

One formula models long and short positions. A long is a buy (+1), a short is
a sell (-1); PnL is always::

    direction * (current_price - entry_price) * quantity

``realized_pnl`` prices at the exit fill, ``unrealized_pnl`` at the current
mark. This single signed definition eliminates the side-branch duplication
previously scattered across the simulator and fixes sign errors for shorts.
"""

from __future__ import annotations

from .order import OrderSide, signed_direction


def realized_pnl(
    side: OrderSide,
    entry_price: float,
    exit_price: float,
    quantity: float,
) -> float:
    """Realized PnL for a closed quantity at the exit fill price."""
    return signed_direction(side) * (exit_price - entry_price) * quantity


def unrealized_pnl(
    side: OrderSide,
    entry_price: float,
    mark_price: float,
    quantity: float,
) -> float:
    """Unrealized PnL for an open quantity at the current mark price."""
    return signed_direction(side) * (mark_price - entry_price) * quantity
