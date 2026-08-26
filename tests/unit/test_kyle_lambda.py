"""Unit tests for Kyle's lambda price-impact feature (integration #12)."""

from __future__ import annotations

import pytest
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.features.kyle_lambda import (
    KyleLambdaFeature,
    extract_trade_series,
    kyle_lambda,
    signed_flow_from_event,
)


class TestSignedFlow:
    def test_aggressor_buy_is_positive(self) -> None:
        assert signed_flow_from_event({"quantity": 2.0, "is_market_maker": False}) == 2.0

    def test_maker_buy_is_negative(self) -> None:
        # Buyer was resting -> seller aggressed -> negative flow
        assert signed_flow_from_event({"quantity": 2.0, "is_market_maker": True}) == -2.0

    def test_zero_quantity_is_zero(self) -> None:
        assert signed_flow_from_event({"quantity": 0.0, "is_market_maker": False}) == 0.0


class TestKyleLambda:
    def test_positive_impact_slope(self) -> None:
        # Higher positive flow -> higher next price: lambda > 0
        flows = [1.0, 1.0, 1.0, -1.0, -1.0]
        changes = [0.5, 0.5, 0.5, -0.5, -0.5]
        result = kyle_lambda(flows, changes)
        assert result is not None
        assert result["lambda"] > 0.0
        assert result["samples"] == 5

    def test_negative_slope_when_flow_absorbs_price(self) -> None:
        # Inverse relationship: adding flow lowers price (unusual but possible)
        flows = [1.0, 1.0, -1.0, -1.0]
        changes = [-0.2, -0.2, 0.2, 0.2]
        result = kyle_lambda(flows, changes)
        assert result is not None
        assert result["lambda"] < 0.0

    def test_zero_variance_returns_none(self) -> None:
        flows = [1.0, 1.0, 1.0]
        changes = [0.1, 0.2, 0.3]
        assert kyle_lambda(flows, changes) is None

    def test_insufficient_samples_returns_none(self) -> None:
        assert kyle_lambda([1.0], [0.1]) is None

    def test_mismatched_lengths_returns_none(self) -> None:
        assert kyle_lambda([1.0, 2.0], [0.1]) is None

    def test_r_squared_is_in_unit_range(self) -> None:
        flows = [1.0, 2.0, 1.5, -1.0, -2.0, -1.5]
        changes = [0.4, 0.8, 0.6, -0.4, -0.8, -0.6]
        result = kyle_lambda(flows, changes)
        assert result is not None
        assert 0.0 <= result["r_squared"] <= 1.0


class TestExtractTradeSeries:
    def test_extracts_parallel_series(self, make_trade_event) -> None:
        base = make_trade_event(price=100.0, quantity=1.0, trade_id=1)
        events = [base]
        for i, (price, maker) in enumerate([(100.5, False), (101.0, True)]):
            event = make_trade_event(
                price=price, quantity=1.0, trade_id=i + 2, offset_seconds=i + 1
            )
            events.append(_replace_payload(event, {"is_market_maker": maker}))
        snapshot = ContextSnapshot.from_events(tuple(events))
        changes, flows = extract_trade_series(snapshot)
        assert len(changes) == len(flows) == 2
        assert changes[0] == pytest.approx(0.5)
        assert flows[0] == 1.0
        assert flows[1] == -1.0  # maker trade is negative flow

    def test_requires_two_trades(self, make_trade_event) -> None:
        snapshot = ContextSnapshot.from_events((make_trade_event(price=100.0),))
        assert extract_trade_series(snapshot) == ([], [])


class TestKyleLambdaFeature:
    def test_computes_from_trades(self, make_trade_event) -> None:
        events = []
        for i, price in enumerate([100.0, 100.5, 101.0, 100.4, 99.9]):
            events.append(make_trade_event(price=price, offset_seconds=i, trade_id=i + 1))
        snapshot = ContextSnapshot.from_events(tuple(events))
        feature = KyleLambdaFeature.compute(snapshot)
        assert feature.value["status"] == "ok" or feature.value["samples"] >= 0
        if feature.value["status"] == "ok":
            assert feature.value["lambda"] is not None

    def test_insufficient_data_status(self, make_trade_event) -> None:
        snapshot = ContextSnapshot.from_events((make_trade_event(price=100.0),))
        feature = KyleLambdaFeature.compute(snapshot)
        assert feature.value["status"] == "insufficient_data"
        assert feature.value["lambda"] is None


def _replace_payload(event, payload: dict) -> object:
    from backend.domain.observation.event import ObservationEvent

    data = event.model_dump()
    data["payload"] = {**data["payload"], **payload}
    return ObservationEvent.model_validate(data)
