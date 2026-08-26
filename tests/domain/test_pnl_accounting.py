"""Unit tests for the shared signed PnL accounting (task P0-009).

The single signed formula ``direction * (current - entry) * quantity`` must
produce correct realized and unrealized PnL for both long (buy) and short
(sell) positions. These tests use hand-computed values so a broken sign or a
side-branch regression is caught immediately.
"""

from __future__ import annotations

import pytest
from backend.domain.execution.order import OrderSide
from backend.domain.execution.pnl import realized_pnl, signed_direction, unrealized_pnl


class TestSignedDirection:
    def test_long_is_positive(self) -> None:
        assert signed_direction(OrderSide.BUY) == 1

    def test_short_is_negative(self) -> None:
        assert signed_direction(OrderSide.SELL) == -1


class TestRealizedPnl:
    def test_long_profit(self) -> None:
        # Buy at 100, sell at 110, qty 10 -> (110-100)*10 = +100
        assert realized_pnl(OrderSide.BUY, 100.0, 110.0, 10.0) == pytest.approx(100.0)

    def test_long_loss(self) -> None:
        # Buy at 100, sell at 90 -> (90-100)*10 = -100
        assert realized_pnl(OrderSide.BUY, 100.0, 90.0, 10.0) == pytest.approx(-100.0)

    def test_short_profit(self) -> None:
        # Sell at 100, buy back at 90 -> -(90-100)*10 = +100
        assert realized_pnl(OrderSide.SELL, 100.0, 90.0, 10.0) == pytest.approx(100.0)

    def test_short_loss(self) -> None:
        # Sell at 100, buy back at 110 -> -(110-100)*10 = -100
        assert realized_pnl(OrderSide.SELL, 100.0, 110.0, 10.0) == pytest.approx(-100.0)

    def test_zero_quantity_yields_zero(self) -> None:
        assert realized_pnl(OrderSide.SELL, 100.0, 110.0, 0.0) == pytest.approx(0.0)

    def test_flat_round_trip_yields_zero(self) -> None:
        assert realized_pnl(OrderSide.BUY, 100.0, 100.0, 5.0) == pytest.approx(0.0)
        assert realized_pnl(OrderSide.SELL, 100.0, 100.0, 5.0) == pytest.approx(0.0)


class TestUnrealizedPnl:
    def test_long_profits_when_mark_rises(self) -> None:
        # Long 10 @ 100, mark 110 -> +100
        assert unrealized_pnl(OrderSide.BUY, 100.0, 110.0, 10.0) == pytest.approx(100.0)

    def test_long_loses_when_mark_falls(self) -> None:
        # Long 10 @ 100, mark 90 -> -100
        assert unrealized_pnl(OrderSide.BUY, 100.0, 90.0, 10.0) == pytest.approx(-100.0)

    def test_short_profits_when_mark_falls(self) -> None:
        # Short 10 @ 100, mark 90 -> +100 (this is the historical sign bug)
        assert unrealized_pnl(OrderSide.SELL, 100.0, 90.0, 10.0) == pytest.approx(100.0)

    def test_short_loses_when_mark_rises(self) -> None:
        # Short 10 @ 100, mark 110 -> -100 (this is the historical sign bug)
        assert unrealized_pnl(OrderSide.SELL, 100.0, 110.0, 10.0) == pytest.approx(-100.0)

    def test_zero_quantity_yields_zero(self) -> None:
        assert unrealized_pnl(OrderSide.BUY, 100.0, 90.0, 0.0) == pytest.approx(0.0)
