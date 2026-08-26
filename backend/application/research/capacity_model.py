# backend/application/research/capacity_model.py
"""Strategy capacity model (task T2-18-1).

Answers "how large may this strategy trade before its own market impact
eats the edge?" from the *calibrated* square-root impact law
(``SquareRootImpactCalibrator``, integration #26):

    impact(s) = half_spread + eta * volatility * sqrt(s / ADV)

The capacity bound is the size ``s`` at which ``impact(s)`` reaches the
budget: ``max_impact_share`` of the strategy's expected edge. Inverting the
law is closed-form:

    s / ADV = ((budget - half_spread) / (eta * volatility)) ** 2

Design rules
------------
- **No calibration, no capacity.** ``capacity`` returns None unless a
  usable calibration exists; the estimate carries the calibration's eta /
  r-squared / observation count so its provenance is auditable.
- **The edge is operator-supplied, never guessed.** ``expected_edge_bps``
  (the expected reward per unit the strategy trades) is a caller input in
  the same spirit as the allocator's operator-supplied volatility — the
  passport's pooled excess is per-fold, not per-trade, and converting
  between frames without evidence would be fabrication.
- **Zero is honest.** No edge, or a half-spread that alone exceeds the
  budget, yields ``executable=False`` with capacity 0.0 — not a tiny
  number that still loses money.
- **The window is bounded.** Participation is capped at
  ``max_participation`` (default 100% of ADV). A negative ``eta`` (slippage
  below the spread baseline) never crosses the budget inside the window;
  the estimate is ``unbounded_within_model`` at the cap, not infinity.

This module is library/research only: nothing here reaches the live path.
"""

from __future__ import annotations

import math

from backend.application.execution.market_impact import (
    ImpactCalibration,
    SquareRootImpactCalibrator,
)
from backend.domain.research.capacity import CapacityEstimate, ImpactCurvePoint

_DEFAULT_MAX_IMPACT_SHARE = 0.2
_DEFAULT_MAX_PARTICIPATION = 1.0
_DEFAULT_CURVE_POINTS = 8


class CapacityModel:
    """Trade-size vs impact curve and capacity bound from one calibration.

    Parameters
    ----------
    max_impact_share: float
        Impact budget as a fraction of the expected edge (default 0.2:
        impact may consume at most 20% of the reward).
    max_participation: float
        Cap on the participation ratio (default 1.0 = 100% of ADV); sizes
        beyond this are outside the square-root law's credible window.
    curve_points: int
        Number of points on the impact curve (default 8).
    """

    def __init__(
        self,
        *,
        max_impact_share: float = _DEFAULT_MAX_IMPACT_SHARE,
        max_participation: float = _DEFAULT_MAX_PARTICIPATION,
        curve_points: int = _DEFAULT_CURVE_POINTS,
    ) -> None:
        if not 0.0 < max_impact_share < 1.0:
            raise ValueError("max_impact_share must be in (0, 1)")
        if max_participation <= 0.0:
            raise ValueError("max_participation must be positive")
        if curve_points < 2:
            raise ValueError("curve_points must be >= 2")
        self._max_impact_share = max_impact_share
        self._max_participation = max_participation
        self._curve_points = curve_points

    def capacity(
        self,
        symbol: str,
        *,
        adv: float,
        volatility_bps: float,
        half_spread_bps: float,
        expected_edge_bps: float,
        calibrator: SquareRootImpactCalibrator | None = None,
        calibration: ImpactCalibration | None = None,
    ) -> CapacityEstimate | None:
        """Capacity bound for one symbol under one calibration.

        Supply either a ``calibrator`` (its current calibration for the
        symbol is read) or an explicit ``calibration``. Returns None when
        no usable calibration exists.
        """
        if adv <= 0.0:
            raise ValueError("adv must be positive")
        if volatility_bps < 0.0 or half_spread_bps < 0.0:
            raise ValueError("volatility_bps and half_spread_bps must be non-negative")
        calibration = self._resolve(calibrator, calibration, symbol)
        if calibration is None:
            return None

        budget_bps = self._max_impact_share * expected_edge_bps
        if expected_edge_bps <= 0.0 or budget_bps <= half_spread_bps:
            return CapacityEstimate(
                symbol=symbol,
                eta=calibration.eta,
                r_squared=calibration.r_squared,
                observations=calibration.observations,
                edge_bps=expected_edge_bps,
                max_impact_share=self._max_impact_share,
                capacity_quantity=0.0,
                capacity_participation_pct=0.0,
                capacity_impact_bps=budget_bps,
                executable=False,
                unbounded_within_model=False,
                curve=(),
            )

        unbounded = calibration.eta <= 0.0
        if unbounded:
            participation = self._max_participation
        else:
            solved = (budget_bps - half_spread_bps) / (calibration.eta * volatility_bps)
            solved = solved * solved
            participation = min(solved, self._max_participation)

        quantity = participation * adv
        impact_at_capacity = self._impact(
            calibration.eta, volatility_bps, half_spread_bps, participation
        )
        curve = self._curve(calibration.eta, volatility_bps, half_spread_bps, adv, participation)
        return CapacityEstimate(
            symbol=symbol,
            eta=calibration.eta,
            r_squared=calibration.r_squared,
            observations=calibration.observations,
            edge_bps=expected_edge_bps,
            max_impact_share=self._max_impact_share,
            capacity_quantity=quantity,
            capacity_participation_pct=participation,
            capacity_impact_bps=impact_at_capacity,
            executable=True,
            unbounded_within_model=unbounded,
            curve=curve,
        )

    def curve(
        self,
        symbol: str,
        *,
        adv: float,
        volatility_bps: float,
        half_spread_bps: float,
        calibrator: SquareRootImpactCalibrator | None = None,
        calibration: ImpactCalibration | None = None,
    ) -> tuple[ImpactCurvePoint, ...] | None:
        """The trade-size vs impact curve up to the participation cap.

        Returns None when no usable calibration exists. The curve spans
        log-spaced participations from a small fraction of ADV up to
        ``max_participation``.
        """
        calibration = self._resolve(calibrator, calibration, symbol)
        if calibration is None:
            return None
        if adv <= 0.0:
            raise ValueError("adv must be positive")
        if volatility_bps < 0.0 or half_spread_bps < 0.0:
            raise ValueError("volatility_bps and half_spread_bps must be non-negative")
        return self._curve(
            calibration.eta, volatility_bps, half_spread_bps, adv, self._max_participation
        )

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _resolve(
        calibrator: SquareRootImpactCalibrator | None,
        calibration: ImpactCalibration | None,
        symbol: str,
    ) -> ImpactCalibration | None:
        if calibration is not None:
            return calibration
        if calibrator is not None:
            return calibrator.calibration(symbol)
        return None

    @staticmethod
    def _impact(
        eta: float, volatility_bps: float, half_spread_bps: float, participation: float
    ) -> float:
        return half_spread_bps + eta * volatility_bps * math.sqrt(participation)

    def _curve(
        self,
        eta: float,
        volatility_bps: float,
        half_spread_bps: float,
        adv: float,
        up_to_participation: float,
    ) -> tuple[ImpactCurvePoint, ...]:
        points: list[ImpactCurvePoint] = []
        for index in range(self._curve_points):
            ratio = index / (self._curve_points - 1)
            # Log-spaced: from 0.1% of the cap up to the cap, regardless of
            # the cap's magnitude (the exponent stays in [0, 1]).
            participation = up_to_participation * (0.001 ** (1.0 - ratio))
            points.append(
                ImpactCurvePoint(
                    participation_pct=participation,
                    quantity=participation * adv,
                    impact_bps=self._impact(eta, volatility_bps, half_spread_bps, participation),
                )
            )
        return tuple(points)


def build_capacity_model(
    *,
    max_impact_share: float = _DEFAULT_MAX_IMPACT_SHARE,
    max_participation: float = _DEFAULT_MAX_PARTICIPATION,
) -> CapacityModel:
    """Bootstrap seam: construct a capacity model."""
    return CapacityModel(max_impact_share=max_impact_share, max_participation=max_participation)
