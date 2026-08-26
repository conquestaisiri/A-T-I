"""Unit tests for the strategy capacity model (task T2-18-1)."""

from __future__ import annotations

import math

import pytest
from backend.application.execution.market_impact import ImpactCalibration
from backend.application.research.capacity_model import CapacityModel, build_capacity_model
from backend.domain.research.capacity import CapacityEstimate


def calibration(*, eta: float = 1.0, r_squared: float = 0.8) -> ImpactCalibration:
    return ImpactCalibration(eta=eta, r_squared=r_squared, observations=50, symbol="btcusdt")


def model(**kwargs) -> CapacityModel:
    return CapacityModel(**kwargs)


class TestConfigValidation:
    def test_rejects_impact_share_out_of_bounds(self) -> None:
        with pytest.raises(ValueError):
            CapacityModel(max_impact_share=0.0)
        with pytest.raises(ValueError):
            CapacityModel(max_impact_share=1.0)

    def test_rejects_non_positive_participation_cap(self) -> None:
        with pytest.raises(ValueError):
            CapacityModel(max_participation=0.0)

    def test_rejects_too_few_curve_points(self) -> None:
        with pytest.raises(ValueError):
            CapacityModel(curve_points=1)


class TestCapacity:
    def test_no_calibration_yields_none(self) -> None:
        assert (
            model().capacity(
                "btcusdt",
                adv=1_000.0,
                volatility_bps=300.0,
                half_spread_bps=2.0,
                expected_edge_bps=50.0,
            )
            is None
        )

    def test_explicit_calibration_is_used(self) -> None:
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=50.0,
            calibration=calibration(),
        )
        assert estimate is not None
        assert isinstance(estimate, CapacityEstimate)
        assert estimate.eta == 1.0
        assert estimate.observations == 50

    def test_capacity_inverts_square_root_law(self) -> None:
        edge = 50.0
        share = 0.2
        half_spread = 2.0
        volatility = 300.0
        eta = 1.0
        adv = 1_000.0
        budget = share * edge
        expected_participation = ((budget - half_spread) / (eta * volatility)) ** 2
        estimate = model().capacity(
            "btcusdt",
            adv=adv,
            volatility_bps=volatility,
            half_spread_bps=half_spread,
            expected_edge_bps=edge,
            calibration=calibration(eta=eta),
        )
        assert estimate is not None
        assert estimate.capacity_participation_pct == pytest.approx(expected_participation)
        assert estimate.capacity_quantity == pytest.approx(expected_participation * adv)
        assert estimate.executable is True
        assert estimate.unbounded_within_model is False
        # impact at capacity equals the budget
        assert estimate.capacity_impact_bps == pytest.approx(budget)

    def test_no_edge_is_zero_capacity(self) -> None:
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=0.0,
            calibration=calibration(),
        )
        assert estimate is not None
        assert estimate.executable is False
        assert estimate.capacity_quantity == 0.0
        assert estimate.capacity_participation_pct == 0.0

    def test_negative_edge_is_zero_capacity(self) -> None:
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=-10.0,
            calibration=calibration(),
        )
        assert estimate is not None
        assert estimate.executable is False

    def test_half_spread_alone_exceeding_budget_kills_capacity(self) -> None:
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=40.0,
            expected_edge_bps=50.0,
            calibration=calibration(),
        )
        assert estimate is not None
        assert estimate.executable is False
        assert estimate.capacity_quantity == 0.0

    def test_negative_eta_is_unbounded_within_model(self) -> None:
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=50.0,
            calibration=calibration(eta=-0.5),
        )
        assert estimate is not None
        assert estimate.unbounded_within_model is True
        assert estimate.capacity_participation_pct == pytest.approx(1.0)  # at the cap
        assert estimate.capacity_quantity == pytest.approx(1_000.0)
        assert estimate.executable is True

    def test_solved_size_above_cap_is_clamped_to_cap(self) -> None:
        # Tiny impact coefficient -> solved size far beyond ADV
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=50.0,
            calibration=calibration(eta=0.01),
        )
        assert estimate is not None
        assert estimate.capacity_participation_pct == pytest.approx(1.0)
        assert estimate.unbounded_within_model is False

    def test_curve_is_log_spaced_up_to_bound(self) -> None:
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=50.0,
            calibration=calibration(),
        )
        assert estimate is not None
        assert len(estimate.curve) == 8
        participations = [p.participation_pct for p in estimate.curve]
        assert participations == sorted(participations)
        assert participations[0] == pytest.approx(0.001 * estimate.capacity_participation_pct)
        assert participations[-1] == pytest.approx(estimate.capacity_participation_pct)
        # impact grows with participation
        impacts = [p.impact_bps for p in estimate.curve]
        assert impacts == sorted(impacts)
        assert estimate.curve[-1].impact_bps == pytest.approx(estimate.capacity_impact_bps)

    def test_capacity_respects_custom_share(self) -> None:
        half_spread = 2.0
        volatility = 300.0
        eta = 1.0
        adv = 1_000.0
        edge = 50.0
        estimate = model(max_impact_share=0.5).capacity(
            "btcusdt",
            adv=adv,
            volatility_bps=volatility,
            half_spread_bps=half_spread,
            expected_edge_bps=edge,
            calibration=calibration(eta=eta),
        )
        assert estimate is not None
        expected = ((0.5 * edge - half_spread) / (eta * volatility)) ** 2
        assert estimate.capacity_participation_pct == pytest.approx(expected)

    def test_invalid_inputs_raise(self) -> None:
        with pytest.raises(ValueError):
            model().capacity(
                "btcusdt",
                adv=0.0,
                volatility_bps=300.0,
                half_spread_bps=2.0,
                expected_edge_bps=50.0,
                calibration=calibration(),
            )
        with pytest.raises(ValueError):
            model().capacity(
                "btcusdt",
                adv=1_000.0,
                volatility_bps=-1.0,
                half_spread_bps=2.0,
                expected_edge_bps=50.0,
                calibration=calibration(),
            )


class TestCurve:
    def test_curve_without_calibration_is_none(self) -> None:
        assert (
            model().curve("btcusdt", adv=1_000.0, volatility_bps=300.0, half_spread_bps=2.0) is None
        )

    def test_curve_spans_to_participation_cap(self) -> None:
        curve = model().curve(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            calibration=calibration(),
        )
        assert curve is not None
        assert len(curve) == 8
        assert curve[-1].participation_pct == pytest.approx(1.0)
        assert curve[-1].quantity == pytest.approx(1_000.0)

    def test_curve_first_point_is_tiny(self) -> None:
        curve = model().curve(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            calibration=calibration(),
        )
        assert curve is not None
        assert curve[0].participation_pct == pytest.approx(0.001)
        assert math.isclose(curve[0].impact_bps, 2.0 + 1.0 * 300.0 * math.sqrt(0.001), rel_tol=1e-6)


class TestBootstrap:
    def test_build_seam_returns_model(self) -> None:
        assert isinstance(build_capacity_model(), CapacityModel)


class TestSerialisation:
    def test_as_dict_round_trips(self) -> None:
        estimate = model().capacity(
            "btcusdt",
            adv=1_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=50.0,
            calibration=calibration(),
        )
        assert estimate is not None
        payload = estimate.as_dict()
        assert payload["symbol"] == "btcusdt"
        assert payload["calibration"]["eta"] == pytest.approx(1.0)
        assert payload["executable"] is True
        assert len(payload["curve"]) == 8
        assert payload["max_impact_share"] == 0.2
