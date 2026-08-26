"""Unit tests for triple-barrier labelling and meta-labelling (integration #25)."""

from __future__ import annotations

import pytest
from backend.application.validation.triple_barrier import (
    TripleBarrierConfig,
    label_series,
    label_triple_barrier,
    meta_label,
    side_for_outcome,
)
from backend.domain.decision.proposal import ProposedActionType


class TestTripleBarrierConfig:
    def test_rejects_loss_multiple(self) -> None:
        with pytest.raises(ValueError):
            TripleBarrierConfig(loss_multiple=0.0)

    def test_rejects_negative_volatility(self) -> None:
        with pytest.raises(ValueError):
            TripleBarrierConfig(volatility=-1.0)

    def test_rejects_missing_profit_anchor(self) -> None:
        with pytest.raises(ValueError):
            TripleBarrierConfig(volatility=0.0, profit_distance=0.0)

    def test_offsets_derived_from_volatility(self) -> None:
        config = TripleBarrierConfig(volatility=10.0, profit_multiple=2.0, loss_multiple=3.0)
        assert config.profit_offset == pytest.approx(20.0)
        assert config.loss_offset == pytest.approx(30.0)

    def test_offsets_from_absolute_distances(self) -> None:
        config = TripleBarrierConfig(profit_distance=5.0, loss_distance=7.0)
        assert config.profit_offset == pytest.approx(5.0)
        assert config.loss_offset == pytest.approx(7.0)


class TestLabelTripleBarrier:
    def test_upper_barrier_touched_first(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0)
        label = label_triple_barrier([100.0, 100.5, 101.2, 99.0], config)
        assert label is not None
        assert label.outcome == 1.0
        assert label.barrier == "upper"
        assert label.exit_step == 2
        assert label.exit_price == pytest.approx(101.2)

    def test_lower_barrier_touched_first(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0)
        label = label_triple_barrier([100.0, 100.5, 98.0], config)
        assert label is not None
        assert label.outcome == -1.0
        assert label.barrier == "lower"
        assert label.exit_step == 2

    def test_vertical_barrier_when_no_touch(self) -> None:
        config = TripleBarrierConfig(profit_distance=10.0, loss_distance=10.0, max_steps=3)
        label = label_triple_barrier([100.0, 100.2, 100.4, 100.6], config)
        assert label is not None
        assert label.outcome == 0.0
        assert label.barrier == "vertical"
        assert label.exit_step == 3

    def test_barrier_touch_at_boundary_is_an_exact_hit(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0)
        label = label_triple_barrier([100.0, 101.0], config)
        assert label is not None
        assert label.outcome == 1.0
        assert label.exit_step == 1

    def test_returns_none_for_tiny_slice(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0)
        assert label_triple_barrier([], config) is None
        assert label_triple_barrier([100.0], config) is None

    def test_trend_state_case(self) -> None:
        # Strong uptrend touches upper even inside the loop, before vertical.
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0, max_steps=5)
        label = label_triple_barrier([100.0, 100.1, 100.2, 101.3], config)
        assert label is not None
        assert label.barrier == "upper"
        assert label.exit_step == 3

    def test_side_flags_direction(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0, max_steps=5)
        label = label_triple_barrier([100.0, 99.0], config)
        assert label is not None
        assert label.side == -1
        assert label.pnl == pytest.approx(-1.0)


class TestLabelSeries:
    def test_labels_every_start(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0, max_steps=5)
        labels = label_series([100.0, 101.0, 102.0, 103.0], config)
        assert len(labels) == 4
        assert labels[0] is not None
        assert labels[0].barrier == "upper"

    def test_out_of_range_start_is_none(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0, max_steps=5)
        labels = label_series([100.0, 101.0], config, starts=[0, 10])
        assert labels[0] is not None
        assert labels[1] is None


class TestMetaLabelMapping:
    def test_side_for_outcome(self) -> None:
        assert side_for_outcome(1.0) is ProposedActionType.ENTER_LONG
        assert side_for_outcome(-1.0) is ProposedActionType.ENTER_SHORT
        assert side_for_outcome(0.0) is None


class TestMetaLabel:
    def test_bet_one_when_prediction_matches_upper_hit(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0)
        label = label_triple_barrier([100.0, 101.0], config)
        assert label is not None
        m = meta_label(label, predicted_side=1)
        assert m.bet == 1
        assert m.pnl == pytest.approx(1.0)
        assert m.touched_barrier == "upper"

    def test_bet_zero_when_prediction_is_lost(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0)
        label = label_triple_barrier([100.0, 99.0], config)
        assert label is not None
        m = meta_label(label, predicted_side=1)
        assert m.bet == 0

    def test_bet_signed_by_prediction_direction(self) -> None:
        config = TripleBarrierConfig(profit_distance=1.0, loss_distance=1.0)
        label = label_triple_barrier([100.0, 101.0], config)
        assert label is not None
        # The window rose, but a short prediction lost; pnl signed negative.
        m = meta_label(label, predicted_side=-1)
        assert m.bet == 0
        assert m.pnl == pytest.approx(-1.0)

    def test_vertical_barrier_decided_by_net_move(self) -> None:
        config = TripleBarrierConfig(profit_distance=10.0, loss_distance=10.0, max_steps=3)
        label = label_triple_barrier([100.0, 100.2, 100.4, 100.6], config)
        assert label is not None
        m = meta_label(label, predicted_side=1)
        assert m.bet == 1  # net drift upward favors long
        assert m.touched_barrier == "vertical"
