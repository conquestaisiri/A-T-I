"""End-to-end capacity pipeline tests on a synthetic order book (T2-18-2).

The gate for this task — the simulator-vs-execution proof (P5-006) — is
DONE, so the pipeline fills -> calibrator -> capacity model is proven
against a synthetic book that follows the square-root impact law *by
construction*: the recovered calibration must track the book's true eta,
and the recovered capacity bound must sit where impact equals the budget.
"""

from __future__ import annotations

import math

import pytest
from backend.application.execution.market_impact import (
    ImpactObservation,
    SquareRootImpactCalibrator,
)
from backend.application.research.capacity_model import CapacityModel
from backend.application.research.synthetic_book import SyntheticFillBook


def book(*, eta: float = 1.0, noise_bps: float = 0.0, seed: int = 7) -> SyntheticFillBook:
    return SyntheticFillBook(
        symbol="btcusdt",
        adv=10_000.0,
        volatility_bps=300.0,
        half_spread_bps=2.0,
        eta=eta,
        noise_bps=noise_bps,
        seed=seed,
    )


def feed_calibrator(
    fill_book: SyntheticFillBook, quantities: list[float]
) -> SquareRootImpactCalibrator:
    calibrator = SquareRootImpactCalibrator(min_observations=1)
    for quantity in quantities:
        slippage = fill_book.fill(quantity)
        calibrator.observe(
            fill_book.symbol,
            ImpactObservation(
                quantity=quantity,
                adv=fill_book.adv,
                volatility_bps=fill_book.volatility_bps,
                realized_slippage_bps=slippage,
                half_spread_bps=fill_book.half_spread_bps,
            ),
        )
    return calibrator


class TestSyntheticFillBook:
    def test_fill_realizes_square_root_law_without_noise(self) -> None:
        fill_book = book(eta=1.0, noise_bps=0.0)
        expected = 2.0 + 1.0 * 300.0 * math.sqrt(0.01)  # 1% of ADV
        assert fill_book.fill(0.01 * fill_book.adv) == pytest.approx(expected)

    def test_fill_is_noisy_when_requested(self) -> None:
        fill_book = book(eta=1.0, noise_bps=5.0, seed=3)
        assert fill_book.fill(100.0) != fill_book.fill(100.0)

    def test_fill_is_deterministic_under_seed(self) -> None:
        first = book(eta=1.0, noise_bps=5.0, seed=3)
        second = book(eta=1.0, noise_bps=5.0, seed=3)
        quantities = [100.0, 200.0, 300.0]
        assert [first.fill(q) for q in quantities] == [second.fill(q) for q in quantities]

    def test_rejects_invalid_parameters(self) -> None:
        with pytest.raises(ValueError):
            SyntheticFillBook(
                symbol="s", adv=0.0, volatility_bps=300.0, half_spread_bps=2.0, eta=1.0
            )
        with pytest.raises(ValueError):
            SyntheticFillBook(
                symbol="s", adv=1.0, volatility_bps=300.0, half_spread_bps=2.0, eta=-1.0
            )
        with pytest.raises(ValueError):
            SyntheticFillBook(
                symbol="s",
                adv=1.0,
                volatility_bps=300.0,
                half_spread_bps=2.0,
                eta=1.0,
                noise_bps=-1.0,
            )
        with pytest.raises(ValueError):
            book().fill(0.0)


class TestPipelineRecoversGroundTruth:
    def test_calibration_recovers_true_eta_without_noise(self) -> None:
        fill_book = book(eta=0.8, noise_bps=0.0)
        calibrator = feed_calibrator(fill_book, [100.0, 500.0, 1_000.0, 2_000.0])
        calibration = calibrator.calibration(fill_book.symbol)
        assert calibration is not None
        assert calibration.eta == pytest.approx(0.8)
        assert calibration.r_squared == pytest.approx(1.0)

    def test_calibration_tracks_eta_with_noise(self) -> None:
        fill_book = book(eta=0.8, noise_bps=1.0, seed=11)
        quantities = [100.0, 250.0, 500.0, 750.0, 1_000.0, 1_500.0, 2_000.0] * 5
        calibrator = feed_calibrator(fill_book, quantities)
        calibration = calibrator.calibration(fill_book.symbol)
        assert calibration is not None
        assert calibration.eta == pytest.approx(0.8, abs=0.1)

    def test_capacity_bound_sits_at_the_budget_on_synthetic_book(self) -> None:
        edge_bps = 50.0
        share = 0.2
        fill_book = book(eta=1.0, noise_bps=0.0)
        calibrator = feed_calibrator(fill_book, [100.0, 500.0, 1_000.0, 2_000.0])
        estimate = CapacityModel().capacity(
            fill_book.symbol,
            adv=fill_book.adv,
            volatility_bps=fill_book.volatility_bps,
            half_spread_bps=fill_book.half_spread_bps,
            expected_edge_bps=edge_bps,
            calibrator=calibrator,
        )
        assert estimate is not None
        assert estimate.executable is True
        # impact at the recovered bound equals the budget: 20% of the edge
        assert estimate.capacity_impact_bps == pytest.approx(share * edge_bps)
        # and the bound is the true inversion of the book's law
        expected_participation = ((share * edge_bps - 2.0) / (1.0 * 300.0)) ** 2
        assert estimate.capacity_participation_pct == pytest.approx(expected_participation)

    def test_full_chain_with_noise_stays_within_tolerance(self) -> None:
        fill_book = book(eta=1.0, noise_bps=1.0, seed=13)
        calibrator = feed_calibrator(
            fill_book, [100.0, 250.0, 500.0, 750.0, 1_000.0, 1_500.0, 2_000.0] * 4
        )
        estimate = CapacityModel().capacity(
            fill_book.symbol,
            adv=fill_book.adv,
            volatility_bps=fill_book.volatility_bps,
            half_spread_bps=fill_book.half_spread_bps,
            expected_edge_bps=50.0,
            calibrator=calibrator,
        )
        assert estimate is not None
        assert estimate.eta == pytest.approx(1.0, abs=0.2)
        assert estimate.capacity_impact_bps == pytest.approx(10.0, abs=0.5)

    def test_no_fills_means_no_capacity(self) -> None:
        calibrator = SquareRootImpactCalibrator(min_observations=30)
        estimate = CapacityModel().capacity(
            "btcusdt",
            adv=10_000.0,
            volatility_bps=300.0,
            half_spread_bps=2.0,
            expected_edge_bps=50.0,
            calibrator=calibrator,
        )
        assert estimate is None  # nothing fabricated from an empty ledger
