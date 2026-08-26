"""Unit tests for the square-root impact calibrator (integration #26)."""

from __future__ import annotations

import math

import pytest
from backend.application.execution.market_impact import (
    ImpactObservation,
    SquareRootImpactCalibrator,
)


class TestImpactObservation:
    def test_rejects_nonpositive_quantity(self) -> None:
        with pytest.raises(ValueError):
            ImpactObservation(
                quantity=0.0,
                adv=10_000.0,
                volatility_bps=50.0,
                realized_slippage_bps=2.0,
                half_spread_bps=1.0,
            )

    def test_rejects_nonpositive_adv(self) -> None:
        with pytest.raises(ValueError):
            ImpactObservation(
                quantity=100.0,
                adv=0.0,
                volatility_bps=50.0,
                realized_slippage_bps=2.0,
                half_spread_bps=1.0,
            )

    def test_rejects_negative_volatility(self) -> None:
        with pytest.raises(ValueError):
            ImpactObservation(
                quantity=100.0,
                adv=10_000.0,
                volatility_bps=-1.0,
                realized_slippage_bps=2.0,
                half_spread_bps=1.0,
            )

    def test_participation_and_sqrt(self) -> None:
        obs = ImpactObservation(
            quantity=200.0,
            adv=10_000.0,
            volatility_bps=50.0,
            realized_slippage_bps=2.0,
            half_spread_bps=1.0,
        )
        assert obs.participation_ratio == pytest.approx(0.02)
        assert obs.sqrt_participation == pytest.approx(math.sqrt(0.02))
        assert obs.residual_slippage_bps == pytest.approx(1.0)

    def test_rejects_negative_residual_converted(self) -> None:
        # residual can be negative (maker fills beat the spread); no raise
        obs = ImpactObservation(
            quantity=100.0,
            adv=10_000.0,
            volatility_bps=50.0,
            realized_slippage_bps=0.2,
            half_spread_bps=1.0,
        )
        assert obs.residual_slippage_bps == pytest.approx(-0.8)


class TestSquareRootImpactCalibrator:
    def test_requires_min_observations(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=30)
        for _ in range(29):
            calibrator.observe(
                "btcusdt",
                ImpactObservation(
                    quantity=100.0,
                    adv=10_000.0,
                    volatility_bps=50.0,
                    realized_slippage_bps=2.0,
                    half_spread_bps=1.0,
                ),
            )
        assert calibrator.calibration("btcusdt") is None
        assert calibrator.observation_count("btcusdt") == 29

    def test_confirms_min_observations_met(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=30)
        for i in range(30):
            calibrator.observe(
                "btcusdt",
                ImpactObservation(
                    quantity=float(i + 1) * 100.0,
                    adv=10_000.0,
                    volatility_bps=50.0,
                    realized_slippage_bps=2.0,
                    half_spread_bps=1.0,
                ),
            )
        calib = calibrator.calibration("btcusdt")
        assert calib is not None
        assert calib.observations == 30

    def test_recovers_known_eta(self) -> None:
        """A synthetic series generated from a known eta is recovered exactly."""
        calibrator = SquareRootImpactCalibrator(min_observations=40)
        eta = 0.35
        for i in range(1, 41):
            quantity = float(i) * 50.0
            obs = ImpactObservation(
                quantity=quantity,
                adv=100_000.0,
                volatility_bps=60.0,
                realized_slippage_bps=1.0 + eta * 60.0 * math.sqrt(quantity / 100_000.0),
                half_spread_bps=1.0,
            )
            calibrator.observe("btcusdt", obs)
        calib = calibrator.calibration("btcusdt")
        assert calib is not None
        assert calib.eta == pytest.approx(eta, abs=1e-9)
        assert calib.r_squared == pytest.approx(1.0, abs=1e-6)

    def test_estimate_impact_scales_with_participation(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=40)
        eta = 0.35
        for i in range(1, 41):
            quantity = float(i) * 50.0
            obs = ImpactObservation(
                quantity=quantity,
                adv=100_000.0,
                volatility_bps=60.0,
                realized_slippage_bps=1.0 + eta * 60.0 * math.sqrt(quantity / 100_000.0),
                half_spread_bps=1.0,
            )
            calibrator.observe("btcusdt", obs)
        small = calibrator.estimate_impact_bps(
            "btcusdt",
            quantity=500.0,
            adv=100_000.0,
            volatility_bps=60.0,
            half_spread_bps=1.0,
        )
        large = calibrator.estimate_impact_bps(
            "btcusdt",
            quantity=5_000.0,
            adv=100_000.0,
            volatility_bps=60.0,
            half_spread_bps=1.0,
        )
        assert large is not None and small is not None
        assert large > small
        assert small == pytest.approx(1.0 + eta * 60.0 * math.sqrt(0.005), abs=1e-6)

    def test_estimate_none_without_calibration(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=40)
        assert (
            calibrator.estimate_impact_bps(
                "btcusdt",
                quantity=500.0,
                adv=100_000.0,
                volatility_bps=60.0,
                half_spread_bps=1.0,
            )
            is None
        )

    def test_estimate_rejects_bad_inputs(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=1)
        with pytest.raises(ValueError):
            calibrator.estimate_impact_bps(
                "btcusdt",
                quantity=0.0,
                adv=100_000.0,
                volatility_bps=60.0,
                half_spread_bps=1.0,
            )

    def test_symbol_isolation(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=1)
        calibrator.observe(
            "btcusdt",
            ImpactObservation(
                quantity=100.0,
                adv=10_000.0,
                volatility_bps=50.0,
                realized_slippage_bps=2.0,
                half_spread_bps=1.0,
            ),
        )
        assert calibrator.calibration("ethusdt") is None

    def test_calibration_serialises(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=1)
        for i in range(5):
            calibrator.observe(
                "btcusdt",
                ImpactObservation(
                    quantity=float(i + 1) * 100.0,
                    adv=10_000.0,
                    volatility_bps=50.0,
                    realized_slippage_bps=2.0,
                    half_spread_bps=1.0,
                ),
            )
        calib = calibrator.calibration("btcusdt")
        assert calib is not None
        data = calib.as_dict()
        assert set(data) == {"symbol", "eta", "r_squared", "observations"}
