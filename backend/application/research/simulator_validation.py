# backend/application/research/simulator_validation.py
"""Prove the paper simulator against realistic execution (task T1-9-1).

The critique's Tier-1 #9 demands evidence that the simulator is not a
fantasy engine: a strategy validated inside it is only as believable as the
execution model it trades against. This module runs the real
``PaperFillEngine`` on historical bars and asks whether its fills behave
like the Square-Root Law of market impact (``SquareRootImpactCalibrator``):

    impact_bps = half_spread_bps + eta * sigma_bps * sqrt(quantity / ADV)

How the validation works
------------------------
1. For each bar, a deterministic multi-level book is built around the bar's
   close (a touch spread derived from ``half_spread_pct`` plus a depth
   ladder sized in fractions of ADV). This is the simulator's temporary
   impact surface: a large order consumes deeper, worse-priced levels.
2. Market orders of increasing participation (fraction of ADV) sweep the
   book through the real ``PaperFillEngine``, producing ``ExecutionReport``
   fills with arrival-based slippage measured against the mid.
3. Every fill becomes an ``ImpactObservation`` for the symbol; the
   calibrator fits ``eta`` from realized slippage against
   ``volatility * sqrt(participation)``.
4. The fitted square-root model is scored on the same fills: correlation of
   realized vs model slippage, and the mean residual relative to the mean
   realized level. The verdict is CONSISTENT only when both the *shape*
   (correlation) and the *level* (residual) of the simulator's fills match
   the law within tolerance.

What the verdict honestly can and cannot say
--------------------------------------------
- CONSISTENT: fills scale with size like the square-root law, and the
  calibrated model prices them within tolerance. The simulator's depth
  model is a *plausible* representation of temporary impact.
- DEVIATES: fills do not track the law (e.g. a flat, participation-
  independent impact add-on swamps the depth effect). This is a research
  finding, not a failure: it is the recalibration input the critique's
  calibration loop demands.
- INSUFFICIENT_DATA: too few fills or a degenerate fit — never a claim.

Nothing here touches the live path: the module is research instrumentation
over the existing paper gateway and calibrator.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from backend.application.execution.market_impact import (
    ImpactCalibration,
    ImpactObservation,
    SquareRootImpactCalibrator,
)
from backend.application.simulation.paper_fill_engine import (
    OrderBook,
    PaperFeeConfig,
    PaperFillEngine,
)
from backend.domain.execution.order import (
    OrderRequest,
    OrderSide,
    OrderType,
    TimeInForce,
)
from backend.domain.research.historical_bar import HistoricalBar

# Default participation fractions (fraction of ADV per order). The ladder
# sizes below are chosen so these span the first three depth levels.
DEFAULT_PARTICIPATION_FRACTIONS = (0.001, 0.003, 0.008, 0.015, 0.025, 0.04, 0.06)
# Depth ladder sizes as fractions of ADV (same ladder on both sides).
DEFAULT_LEVEL_SIZES = (0.01, 0.02, 0.05, 0.10)
# Ask prices sit at odd multiples of the half-spread above the mid; bids mirror.
PRICE_OFFSETS = (1.0, 3.0, 5.0, 7.0)


class SimulationVerdict(StrEnum):
    """The validation's verdict on the simulator's execution realism."""

    CONSISTENT = "consistent"
    DEVIATES = "deviates"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class SimulatorValidationReport:
    """The full outcome of one simulator-vs-square-root-law validation.

    Attributes
    ----------
    symbol: str
        Symbol whose bars were replayed.
    n_bars: int
        Bars used to build books.
    n_observations: int
        Fills recorded (bars x participation fractions).
    half_spread_bps: float
        The touch half-spread assumed (bps of mid).
    mean_participation: float
        Mean participation ratio across all fills.
    flat_impact_bps: float
        The simulator's flat participation-cost add-on (0 = off). A flat
        add-on cannot scale with order size, so it shows up as a systematic
        residual against the square-root model.
    calibration: ImpactCalibration | None
        The fitted square-root calibration, None when unusable.
    mean_realized_slippage_bps: float
        Mean realized slippage across all fills (vs arrival mid).
    mean_model_slippage_bps: float
        Mean square-root-model slippage across all fills (fitted eta).
    mean_residual_bps: float
        mean_realized - mean_model. Positive = the simulator costs more
        than the square-root law prices at the sample's participation.
    correlation: float | None
        Pearson correlation of realized vs model slippage across fills;
        None when degenerate (e.g. a constant model).
    model_impact_bps_at_mean_participation: float | None
        The calibrated model's expected impact at the sample's mean
        participation, versus the simulator's flat add-on.
    verdict: SimulationVerdict
        The honest verdict (see module docstring).
    reasons: tuple[str, ...]
        The numbers that produced the verdict (audit trail).
    """

    symbol: str
    n_bars: int
    n_observations: int
    half_spread_bps: float
    mean_participation: float
    flat_impact_bps: float
    calibration: ImpactCalibration | None
    mean_realized_slippage_bps: float
    mean_model_slippage_bps: float
    mean_residual_bps: float
    correlation: float | None
    model_impact_bps_at_mean_participation: float | None
    verdict: SimulationVerdict
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        """Serialise the validation report to a plain dictionary."""
        return {
            "symbol": self.symbol,
            "n_bars": self.n_bars,
            "n_observations": self.n_observations,
            "half_spread_bps": round(self.half_spread_bps, 6),
            "mean_participation": round(self.mean_participation, 6),
            "flat_impact_bps": round(self.flat_impact_bps, 6),
            "calibration": self.calibration.as_dict() if self.calibration else None,
            "mean_realized_slippage_bps": round(self.mean_realized_slippage_bps, 6),
            "mean_model_slippage_bps": round(self.mean_model_slippage_bps, 6),
            "mean_residual_bps": round(self.mean_residual_bps, 6),
            "correlation": (round(self.correlation, 6) if self.correlation is not None else None),
            "model_impact_bps_at_mean_participation": (
                round(self.model_impact_bps_at_mean_participation, 6)
                if self.model_impact_bps_at_mean_participation is not None
                else None
            ),
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
        }


class SimulatorValidator:
    """Replay bars through the paper simulator and score its execution realism.

    Parameters
    ----------
    fee_config: PaperFeeConfig | None
        The simulator's fee/impact configuration (defaults to the legacy
        zero-cost model; the flat ``impact_bps`` add-on is surfaced in the
        report as the flat-vs-square-root comparison).
    min_observations: int
        Minimum fills required before a calibration is usable (forwarded to
        ``SquareRootImpactCalibrator``).
    correlation_threshold: float
        Minimum correlation of realized vs square-root-model slippage for a
        CONSISTENT verdict (shape gate).
    residual_tolerance: float
        Maximum |mean residual| as a fraction of mean realized slippage for
        a CONSISTENT verdict (level gate).
    """

    def __init__(
        self,
        *,
        fee_config: PaperFeeConfig | None = None,
        min_observations: int = 30,
        correlation_threshold: float = 0.7,
        residual_tolerance: float = 0.5,
    ) -> None:
        if correlation_threshold <= 0.0 or correlation_threshold > 1.0:
            raise ValueError("correlation_threshold must be in (0, 1]")
        if residual_tolerance <= 0.0:
            raise ValueError("residual_tolerance must be positive")
        self._fee_config = fee_config or PaperFeeConfig()
        self._min_observations = min_observations
        self._correlation_threshold = correlation_threshold
        self._residual_tolerance = residual_tolerance

    def validate(
        self,
        bars: Sequence[HistoricalBar],
        *,
        symbol: str = "BTCUSDT",
        adv: float,
        half_spread_pct: float = 0.0002,
        participation_fractions: Sequence[float] | None = None,
        bars_to_trade: int | None = None,
        bars_per_year: int = 365 * 24,
    ) -> SimulatorValidationReport:
        """Replay ``bars`` through the simulator and return the verdict.

        Parameters
        ----------
        bars: Sequence[HistoricalBar]
            The historical series to build books from (chronological).
        symbol: str
            Symbol used for orders and calibration.
        adv: float
            Average daily volume in units (ladder depth = fraction of ADV).
        half_spread_pct: float
            Touch half-spread as a fraction of mid (default 2 bps).
        participation_fractions: Sequence[float] | None
            Order sizes as fractions of ADV (defaults to the module family).
        bars_to_trade: int | None
            Number of bars to trade on (defaults to all bars). At least
            ``ceil(min_observations / len(fractions))`` bars are needed for a
            usable calibration; fewer yields INSUFFICIENT_DATA.
        bars_per_year: int
            Annualization factor for the volatility term of the model.
        """
        bar_list = list(bars)
        if not bar_list:
            raise ValueError("validation requires at least one historical bar")
        if not isinstance(adv, (int, float)) or adv <= 0.0:
            raise ValueError("adv must be positive")
        if not isinstance(half_spread_pct, (int, float)) or half_spread_pct <= 0.0:
            raise ValueError("half_spread_pct must be positive")
        fractions = tuple(participation_fractions or DEFAULT_PARTICIPATION_FRACTIONS)
        if not fractions or any(f <= 0.0 for f in fractions):
            raise ValueError("participation fractions must be positive")
        if bars_to_trade is not None and bars_to_trade < 1:
            raise ValueError("bars_to_trade must be >= 1")
        trade_bars = bar_list[: bars_to_trade if bars_to_trade is not None else len(bar_list)]

        volatility_bps = _annualized_volatility_bps(
            [bar.close for bar in bar_list], bars_per_year=bars_per_year
        )
        calibrator = SquareRootImpactCalibrator(min_observations=self._min_observations)
        engine = PaperFillEngine(fee_config=self._fee_config)
        observations: list[tuple[ImpactObservation, float]] = []

        for bar_index, bar in enumerate(trade_bars):
            book = _book_for_bar(bar, adv=adv, half_spread_pct=half_spread_pct)
            engine.set_book(book)
            for fraction_index, fraction in enumerate(fractions):
                order = OrderRequest(
                    order_id=f"VALIDATE-{bar_index}-{fraction_index}",
                    proposal_id=None,
                    symbol=symbol,
                    side=OrderSide.BUY,
                    order_type=OrderType.MARKET,
                    quantity=fraction * adv,
                    limit_price=None,
                    created_at=bar.timestamp,
                    time_in_force=TimeInForce.GTC,
                )
                report = engine.submit(order)
                slippage_bps = report.slippage_bps
                if slippage_bps is None or not report.is_filled:
                    continue
                obs = ImpactObservation(
                    quantity=report.quantity,
                    adv=adv,
                    volatility_bps=volatility_bps,
                    realized_slippage_bps=slippage_bps,
                    half_spread_bps=half_spread_pct * 10_000.0,
                )
                calibrator.observe(symbol, obs)
                observations.append((obs, slippage_bps))

        return self._report(
            symbol=symbol,
            n_bars=len(trade_bars),
            half_spread_bps=half_spread_pct * 10_000.0,
            calibrator=calibrator,
            observations=observations,
        )

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def _report(
        self,
        *,
        symbol: str,
        n_bars: int,
        half_spread_bps: float,
        calibrator: SquareRootImpactCalibrator,
        observations: list[tuple[ImpactObservation, float]],
    ) -> SimulatorValidationReport:
        """Score the fills against the fitted square-root model."""
        calibration = calibrator.calibration(symbol)
        n_observations = len(observations)
        mean_participation = (
            statistics.fmean(obs.participation_ratio for obs, _ in observations)
            if observations
            else 0.0
        )
        mean_realized = (
            statistics.fmean(slippage for _, slippage in observations) if observations else 0.0
        )

        reasons: list[str] = []
        if calibration is None or not observations:
            verdict = SimulationVerdict.INSUFFICIENT_DATA
            reasons.append(
                f"{n_observations} fills (need at least {self._min_observations}) "
                "or degenerate fit: no usable square-root calibration"
            )
            return SimulatorValidationReport(
                symbol=symbol,
                n_bars=n_bars,
                n_observations=n_observations,
                half_spread_bps=half_spread_bps,
                mean_participation=mean_participation,
                flat_impact_bps=self._fee_config.impact_bps,
                calibration=calibration,
                mean_realized_slippage_bps=round(mean_realized, 6),
                mean_model_slippage_bps=0.0,
                mean_residual_bps=round(mean_realized, 6),
                correlation=None,
                model_impact_bps_at_mean_participation=None,
                verdict=verdict,
                reasons=tuple(reasons),
            )

        model_values: list[float] = []
        for obs, _ in observations:
            model_values.append(
                obs.half_spread_bps + calibration.eta * obs.volatility_bps * obs.sqrt_participation
            )
        mean_model = statistics.fmean(model_values)
        mean_residual = mean_realized - mean_model
        correlation = _pearson([s for _, s in observations], model_values)

        if correlation is not None and correlation < self._correlation_threshold:
            verdict = SimulationVerdict.DEVIATES
            reasons.append(
                f"realized vs model slippage correlation {correlation:.4f} below "
                f"{self._correlation_threshold:.2f}: fills do not track the "
                "square-root law (flat impact add-on may be dominating depth)"
            )
        elif mean_realized > 0.0 and abs(mean_residual) > self._residual_tolerance * mean_realized:
            verdict = SimulationVerdict.DEVIATES
            reasons.append(
                f"mean residual {mean_residual:+.4f} bps exceeds "
                f"{self._residual_tolerance:.0%} of mean realized {mean_realized:.4f} bps: "
                "the square-root model cannot price the simulator's fills within tolerance"
            )
        else:
            verdict = SimulationVerdict.CONSISTENT
            reasons.append(
                f"correlation {correlation:.4f}" if correlation is not None else "constant model",
            )
            reasons.append(
                f"mean residual {mean_residual:+.4f} bps within tolerance of "
                f"mean realized {mean_realized:.4f} bps"
            )

        model_impact = calibrator.estimate_impact_bps(
            symbol,
            quantity=mean_participation * observations[0][0].adv,
            adv=observations[0][0].adv,
            volatility_bps=observations[0][0].volatility_bps,
            half_spread_bps=half_spread_bps,
        )
        return SimulatorValidationReport(
            symbol=symbol,
            n_bars=n_bars,
            n_observations=n_observations,
            half_spread_bps=half_spread_bps,
            mean_participation=round(mean_participation, 6),
            flat_impact_bps=self._fee_config.impact_bps,
            calibration=calibration,
            mean_realized_slippage_bps=round(mean_realized, 6),
            mean_model_slippage_bps=round(mean_model, 6),
            mean_residual_bps=round(mean_residual, 6),
            correlation=round(correlation, 6) if correlation is not None else None,
            model_impact_bps_at_mean_participation=model_impact,
            verdict=verdict,
            reasons=tuple(reasons),
        )


def _book_for_bar(bar: HistoricalBar, *, adv: float, half_spread_pct: float) -> OrderBook:
    """Build the deterministic multi-level book for one bar.

    The mid is the bar's close; asks sit at odd multiples of the half-spread
    above the mid and bids mirror below. Level sizes are fractions of ADV,
    so a participation of 1% or more must consume deeper levels — that depth
    ladder is the simulator's temporary-impact surface.
    """
    mid = bar.close
    half_spread = mid * half_spread_pct
    bids: list[tuple[float, float]] = []
    asks: list[tuple[float, float]] = []
    for offset, size in zip(PRICE_OFFSETS, DEFAULT_LEVEL_SIZES, strict=True):
        asks.append((mid + half_spread * offset, size * adv))
        bids.append((mid - half_spread * offset, size * adv))
    return OrderBook(
        best_bid=mid - half_spread,
        best_ask=mid + half_spread,
        bid_size=0.0,
        ask_size=0.0,
        bids=bids,
        asks=asks,
    )


def _annualized_volatility_bps(closes: Sequence[float], *, bars_per_year: int) -> float:
    """Annualized standard deviation of close-to-close returns, in bps."""
    if bars_per_year < 1:
        raise ValueError("bars_per_year must be >= 1")
    returns = [
        current / previous - 1.0
        for previous, current in zip(closes, closes[1:], strict=False)
        if previous > 0.0
    ]
    if len(returns) < 2:
        return 0.0
    std_dev = statistics.stdev(returns)
    return std_dev * math.sqrt(bars_per_year) * 10_000.0


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Pearson correlation, None when degenerate (no variance or < 2 pairs)."""
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    cov = statistics.fmean((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    var_x = statistics.fmean((x - mean_x) ** 2 for x in xs)
    var_y = statistics.fmean((y - mean_y) ** 2 for y in ys)
    if var_x == 0.0 or var_y == 0.0:
        return None
    return cov / math.sqrt(var_x * var_y)
