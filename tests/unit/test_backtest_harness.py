"""Unit tests for the fill-aware validation harness (task P2-003).

The acceptance criteria are:
1. Microstructure strategies are tested through realistic fills (queue-aware
   makers, impact-paying takers, maker/taker fees).
2. Randomness is seeded or controlled: a seeded harness is fully reproducible
   and no harness ever touches the global NumPy state.
3. Assumptions are documented (see the module docstring of
   ``backend.application.validation.backtest_harness``) and covered here.

Each test pins down a deterministic fill-model guarantee rather than a draw.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pytest
from backend.application.validation import (
    BacktestResult,
    BookSnapshot,
    FillResult,
    HarnessConfig,
    OrderDecision,
    ValidationHarness,
)


def _book(
    bid: float = 99.0, ask: float = 101.0, bid_size: float = 100.0, ask_size: float = 100.0
) -> BookSnapshot:
    return BookSnapshot(best_bid=bid, best_ask=ask, bid_size=bid_size, ask_size=ask_size)


# --- simulate_fill: deterministic fill-model guarantees -----------------------


def test_taker_buy_fills_immediately_and_pays_impact() -> None:
    harness = ValidationHarness(HarnessConfig(seed=1))
    result = harness.simulate_fill(
        order_side="buy",
        order_price=120.0,  # at/above ask -> taker
        order_size=2.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=100.0,
        ask_size=100.0,
    )
    assert isinstance(result, FillResult)
    assert result.filled is True
    assert result.maker is False
    assert result.fill_ratio == pytest.approx(1.0)
    assert result.queue_position == 0
    assert result.fill_price == pytest.approx(101.0 + 2.0 * 0.1)  # ask + temp impact
    assert result.fee == pytest.approx(0.0005 * 2.0)
    assert result.impact == pytest.approx(0.01 * 2.0)


def test_taker_sell_fills_immediately_and_pays_impact() -> None:
    harness = ValidationHarness(HarnessConfig(seed=1))
    result = harness.simulate_fill(
        order_side="sell",
        order_price=50.0,  # at/below bid -> taker
        order_size=1.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=100.0,
        ask_size=100.0,
    )
    assert result.filled is True
    assert result.maker is False
    assert result.fill_price == pytest.approx(99.0 - 2.0 * 0.1)
    assert result.fee == pytest.approx(0.0005)
    assert result.impact == pytest.approx(0.01)


def test_unfilled_maker_pays_no_fee() -> None:
    harness = ValidationHarness(HarnessConfig(queue_fill_probability=0.0, seed=1))
    result = harness.simulate_fill(
        order_side="buy",
        order_price=98.0,  # inside bid, but never fills
        order_size=1.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=100.0,
        ask_size=100.0,
    )
    assert result.filled is False
    assert result.maker is True
    assert result.fill_price == pytest.approx(0.0)
    assert result.fill_ratio == pytest.approx(0.0)
    assert result.fee == pytest.approx(0.0)
    assert result.impact == pytest.approx(0.0)


def test_partial_maker_fill_is_capped_by_depth() -> None:
    harness = ValidationHarness(HarnessConfig(queue_fill_probability=1.0, seed=1))
    result = harness.simulate_fill(
        order_side="buy",
        order_price=98.999,  # one tick inside bid -> queue position 0
        order_size=1.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=0.5,
        ask_size=100.0,
    )
    assert result.filled is True
    assert result.maker is True
    assert result.fill_ratio == pytest.approx(0.5)
    assert result.fee == pytest.approx(-0.0002)  # maker rebate on full order size


def test_between_bid_and_ask_buy_does_not_fill() -> None:
    harness = ValidationHarness(HarnessConfig(seed=1))
    result = harness.simulate_fill(
        order_side="buy",
        order_price=100.0,  # inside the spread
        order_size=1.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=100.0,
        ask_size=100.0,
    )
    assert result.filled is False
    assert result.maker is False
    assert result.fee == pytest.approx(0.0)


# --- randomness: seeded or controlled ------------------------------------------


def test_seeded_harness_is_reproducible() -> None:
    first = ValidationHarness(HarnessConfig(seed=42))
    second = ValidationHarness(HarnessConfig(seed=42))
    kwargs: dict[str, Any] = dict(
        order_side="buy",
        order_price=98.0,
        order_size=1.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=100.0,
        ask_size=100.0,
    )
    outcomes_a = [first.simulate_fill(**kwargs).filled for _ in range(50)]
    outcomes_b = [second.simulate_fill(**kwargs).filled for _ in range(50)]
    assert outcomes_a == outcomes_b


def test_different_seeds_produce_different_draws() -> None:
    a = ValidationHarness(HarnessConfig(seed=1, queue_fill_probability=0.5))
    b = ValidationHarness(HarnessConfig(seed=3, queue_fill_probability=0.5))
    kwargs: dict[str, Any] = dict(
        order_side="buy",
        order_price=98.0,
        order_size=1.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=100.0,
        ask_size=100.0,
    )
    outcomes_a = [a.simulate_fill(**kwargs).filled for _ in range(300)]
    outcomes_b = [b.simulate_fill(**kwargs).filled for _ in range(300)]
    assert outcomes_a != outcomes_b


def _global_state_key() -> Any:
    state: Any = np.random.get_state()
    return (state[0], state[1].tobytes(), state[2], state[3], state[4])


def test_harness_never_touches_global_numpy_state() -> None:
    before = _global_state_key()
    harness = ValidationHarness(HarnessConfig(seed=None))  # unseeded, fresh entropy
    kwargs: dict[str, Any] = dict(
        order_side="buy",
        order_price=98.0,
        order_size=1.0,
        best_bid=99.0,
        best_ask=101.0,
        bid_size=100.0,
        ask_size=100.0,
    )
    for _ in range(100):
        harness.simulate_fill(**kwargs)
    after = _global_state_key()
    assert after == before


# --- backtest_microstructure ------------------------------------------------


def test_backtest_alignment_validation() -> None:
    harness = ValidationHarness(HarnessConfig(seed=1))
    with pytest.raises(ValueError, match="step-aligned"):
        harness.backtest_microstructure(
            decisions=[OrderDecision(side="buy")],
            books=[_book(), _book()],
            initial_price=100.0,
        )


def test_backtest_requires_positive_initial_price() -> None:
    harness = ValidationHarness(HarnessConfig(seed=1))
    with pytest.raises(ValueError, match="positive"):
        harness.backtest_microstructure(
            decisions=[OrderDecision(side="buy")],
            books=[_book()],
            initial_price=0.0,
        )


def test_backtest_taker_round_trip_metrics_exact() -> None:
    harness = ValidationHarness(HarnessConfig(seed=1))
    decisions = [
        OrderDecision(side="buy", limit_price=101.0, size=1.0),  # taker
        OrderDecision(side="sell", limit_price=99.0, size=1.0),  # taker
    ]
    books = [_book(), _book()]
    result = harness.backtest_microstructure(decisions, books, initial_price=100.0)

    assert isinstance(result, BacktestResult)
    # buy fills at 101.2, sell fills at 98.8 -> realized 2.4, flat at the end.
    assert result.total_return == pytest.approx(2.4 / 100.0)
    assert result.n_signals == 2
    assert result.n_fill_attempts == 2
    assert result.n_filled == 2
    assert result.fill_rate == pytest.approx(1.0)
    assert result.n_trades == 1  # one round trip (long closed)
    assert result.win_rate == pytest.approx(1.0)
    assert result.total_fees == pytest.approx(0.001)
    assert result.total_impact_bps == pytest.approx(2 * (0.01 / 99.0 * 10_000.0))
    assert result.sharpe > 0.0
    data = result.as_dict()
    assert data["total_return"] == pytest.approx(result.total_return)
    assert data["n_trades"] == 1


def test_backtest_hold_steps_are_not_signals() -> None:
    harness = ValidationHarness(HarnessConfig(seed=1))
    decisions = [
        OrderDecision(side=None),  # hold
        OrderDecision(side="buy", limit_price=101.0, size=1.0),
        OrderDecision(side="sell", limit_price=99.0, size=1.0),
        OrderDecision(side=None),  # hold
    ]
    books = [_book() for _ in decisions]
    result = harness.backtest_microstructure(decisions, books, initial_price=100.0)
    assert result.n_signals == 2
    assert result.n_fill_attempts == 2
    assert result.n_filled == 2


def test_backtest_unfilled_passive_orders_count_as_attempts() -> None:
    harness = ValidationHarness(HarnessConfig(queue_fill_probability=0.0, seed=1))
    decisions = [
        OrderDecision(side="buy", limit_price=98.0, size=1.0),  # never fills
        OrderDecision(side="sell", limit_price=103.0, size=1.0),  # never fills
    ]
    books = [_book(), _book()]
    result = harness.backtest_microstructure(decisions, books, initial_price=100.0)
    assert result.n_signals == 2
    assert result.n_fill_attempts == 2
    assert result.n_filled == 0
    assert result.fill_rate == pytest.approx(0.0)
    assert result.total_fees == pytest.approx(0.0)
    assert result.n_trades == 0
    assert result.win_rate == pytest.approx(0.0)


def test_backtest_partial_maker_fill_opens_partial_position() -> None:
    harness = ValidationHarness(HarnessConfig(queue_fill_probability=1.0, seed=1))
    decisions = [
        OrderDecision(side="buy", limit_price=98.999, size=1.0),  # maker, 0.5 depth
        OrderDecision(side="sell", limit_price=99.0, size=1.0),  # taker close
    ]
    books = [_book(bid_size=0.5), _book()]
    result = harness.backtest_microstructure(decisions, books, initial_price=100.0)
    assert result.n_filled == 2
    # Buy opens 0.5 of long, sell closes the whole 0.5 -> one round trip.
    assert result.n_trades == 1
    assert result.total_fees == pytest.approx(-0.0002 + 0.0005)


def test_backtest_is_reproducible_with_seed() -> None:
    decisions = [
        OrderDecision(side="buy", limit_price=98.0, size=1.0),
        OrderDecision(side="sell", limit_price=101.0, size=1.0),
        OrderDecision(side="buy", limit_price=98.0, size=1.0),
        OrderDecision(side="sell", limit_price=101.0, size=1.0),
    ]
    books = [_book() for _ in decisions]
    a = ValidationHarness(HarnessConfig(seed=7)).backtest_microstructure(
        decisions, books, initial_price=100.0
    )
    b = ValidationHarness(HarnessConfig(seed=7)).backtest_microstructure(
        decisions, books, initial_price=100.0
    )
    assert a.as_dict() == b.as_dict()
