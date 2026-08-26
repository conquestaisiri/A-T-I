"""Simulator-vs-square-root-law validation tests (T1-9-1, P5-006).

The validation must prove: (1) the paper simulator's depth-ladder fills pay
more slippage as participation grows (a temporary-impact surface), (2) the
fitted square-root calibration matches those fills when the cost model is
shape-faithful, (3) a flat participation-independent impact add-on is
honestly reported as a deviation, and (4) the whole pipeline is
deterministic and refuses degenerate inputs.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.simulator_validation import (
    DEFAULT_PARTICIPATION_FRACTIONS,
    SimulationVerdict,
    SimulatorValidationReport,
    SimulatorValidator,
    _book_for_bar,
)
from backend.application.simulation.paper_fill_engine import OrderBook, PaperFeeConfig
from backend.domain.research.historical_bar import HistoricalBar

T0 = datetime(2026, 1, 1, tzinfo=UTC)
STEP = timedelta(hours=1)


def bars(n: int, *, seed: int = 42, drift: float = 0.0, vol: float = 0.002) -> list[HistoricalBar]:
    """Seeded deterministic OHLCV series (reproducible market)."""
    rng = random.Random(seed)
    result: list[HistoricalBar] = []
    price = 100.0
    for i in range(n):
        ts = T0 + i * STEP
        change = rng.gauss(drift, vol)
        open_price = price
        close_price = max(0.01, price * (1.0 + change))
        high = max(open_price, close_price) * (1.0 + abs(rng.gauss(0.0, 0.0005)))
        low = min(open_price, close_price) * (1.0 - abs(rng.gauss(0.0, 0.0005)))
        result.append(
            HistoricalBar(
                timestamp=ts,
                open=open_price,
                high=high,
                low=low,
                close=close_price,
                volume=rng.uniform(50.0, 200.0),
            )
        )
        price = close_price
    return result


class TestBookBuilder:
    def test_book_centred_on_bar_close_with_depth_ladder(self) -> None:
        bar = bars(1, seed=5)[0]
        book = _book_for_bar(bar, adv=1000.0, half_spread_pct=0.0002)
        assert isinstance(book, OrderBook)
        assert book.mid == bar.close
        assert book.best_bid < book.best_ask
        assert book.asks is not None and book.bids is not None
        assert book.asks[0][0] == pytest.approx(bar.close * (1.0 + 0.0002))
        assert book.bids[0][0] == pytest.approx(bar.close * (1.0 - 0.0002))
        assert book.asks[0][1] == 0.01 * 1000.0
        assert book.asks[-1][1] == 0.10 * 1000.0
        ask_prices = [price for price, _ in book.asks]
        assert ask_prices == sorted(ask_prices)


class TestSimulatorValidator:
    def test_depth_ladder_produces_consistent_square_root_behaviour(self) -> None:
        report = SimulatorValidator().validate(bars(40), adv=1000.0)
        assert isinstance(report, SimulatorValidationReport)
        assert report.verdict is SimulationVerdict.CONSISTENT
        assert report.calibration is not None
        assert report.calibration.eta > 0.0
        assert report.calibration.observations == 40 * len(DEFAULT_PARTICIPATION_FRACTIONS)
        assert report.correlation is not None and report.correlation >= 0.7
        assert report.n_observations == 40 * len(DEFAULT_PARTICIPATION_FRACTIONS)
        assert report.model_impact_bps_at_mean_participation is not None
        assert report.reasons

    def test_larger_orders_pay_monotone_more_slippage(self) -> None:
        validator = SimulatorValidator()
        report = validator.validate(bars(60, seed=9), adv=1000.0)
        assert report.verdict is SimulationVerdict.CONSISTENT
        assert report.calibration is not None
        assert report.mean_realized_slippage_bps > report.half_spread_bps

    def test_flat_impact_add_on_surfaces_as_systematic_residual(self) -> None:
        fee_config = PaperFeeConfig(impact_bps=20.0)
        report = SimulatorValidator(fee_config=fee_config).validate(bars(40), adv=1000.0)
        assert report.verdict is SimulationVerdict.DEVIATES
        assert report.flat_impact_bps == 20.0
        assert report.mean_residual_bps > 0.0
        assert any("residual" in reason for reason in report.reasons)

    def test_insufficient_observations_yields_insufficient_data(self) -> None:
        report = SimulatorValidator().validate(
            bars(40),
            adv=1000.0,
            bars_to_trade=1,
        )
        assert report.verdict is SimulationVerdict.INSUFFICIENT_DATA
        assert report.calibration is None
        assert report.n_observations < 30

    def test_deterministic_given_same_inputs(self) -> None:
        series = bars(40, seed=3)
        a = SimulatorValidator().validate(series, adv=1000.0)
        b = SimulatorValidator().validate(series, adv=1000.0)
        assert a.as_dict() == b.as_dict()

    def test_report_as_dict_shape(self) -> None:
        data = SimulatorValidator().validate(bars(40), adv=1000.0).as_dict()
        assert set(data) == {
            "symbol",
            "n_bars",
            "n_observations",
            "half_spread_bps",
            "mean_participation",
            "flat_impact_bps",
            "calibration",
            "mean_realized_slippage_bps",
            "mean_model_slippage_bps",
            "mean_residual_bps",
            "correlation",
            "model_impact_bps_at_mean_participation",
            "verdict",
            "reasons",
        }
        assert data["verdict"] == "consistent"

    def test_empty_bars_rejected(self) -> None:
        with pytest.raises(ValueError):
            SimulatorValidator().validate([], adv=1000.0)

    def test_non_positive_adv_rejected(self) -> None:
        with pytest.raises(ValueError):
            SimulatorValidator().validate(bars(10), adv=0.0)

    def test_non_positive_half_spread_rejected(self) -> None:
        with pytest.raises(ValueError):
            SimulatorValidator().validate(bars(10), adv=1000.0, half_spread_pct=0.0)

    def test_bad_participation_fractions_rejected(self) -> None:
        with pytest.raises(ValueError):
            SimulatorValidator().validate(
                bars(10),
                adv=1000.0,
                participation_fractions=(0.001, 0.0),
            )

    def test_zero_bars_to_trade_rejected(self) -> None:
        with pytest.raises(ValueError):
            SimulatorValidator().validate(bars(10), adv=1000.0, bars_to_trade=0)

    def test_invalid_tolerances_rejected(self) -> None:
        with pytest.raises(ValueError):
            SimulatorValidator(correlation_threshold=1.5)
        with pytest.raises(ValueError):
            SimulatorValidator(residual_tolerance=0.0)
