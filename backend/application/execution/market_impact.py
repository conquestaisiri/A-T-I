# backend/application/execution/market_impact.py
"""Square-root market impact calibration (integration #26).

The classic Square-Root Law of market impact (Almgren et al., Bouchaud et
al.) scales transient impact with the square root of participation:

    impact_bps = half_spread_bps + eta * sigma_bps * sqrt(quantity / ADV)

Rather than hard-coding ``eta`` (the research stream rejects that), this
module calibrates ``eta`` from ATI's **own fills**:
each executed order contributes an observation of realized slippage against
its participation ratio, and a least-squares fit of the linear-in-sqrt model
recovers the symbol's impact coefficient. The calibrated coefficient feeds
a pre-trade veto: a risk-increasing order whose expected impact consumes too
much of its expected reward is rejected.

Kept self-contained (stdlib only) and dependency-free, mirroring the VPIN
estimator's constraint set.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ImpactObservation:
    """One calibrated fill: realized slippage against participation.

    Attributes
    ----------
    quantity: float
        Units actually filled.
    adv: float
        Average daily volume (same units as ``quantity``).
    volatility_bps: float
        Annualised volatility of the symbol in basis points.
    realized_slippage_bps: float
        Accessible/adverse slippage actually paid on the fill: the absolute
        difference between the fill price and the arrival mid, in bps.
    half_spread_bps: float
        Half of the prevailing quoted spread at fill time, in bps.
    """

    quantity: float
    adv: float
    volatility_bps: float
    realized_slippage_bps: float
    half_spread_bps: float

    def __post_init__(self) -> None:
        if self.quantity <= 0.0:
            raise ValueError("quantity must be positive")
        if self.adv <= 0.0:
            raise ValueError("adv must be positive")
        if self.volatility_bps < 0.0:
            raise ValueError("volatility_bps cannot be negative")
        if self.realized_slippage_bps < 0.0:
            raise ValueError("realized_slippage_bps cannot be negative")
        if self.half_spread_bps < 0.0:
            raise ValueError("half_spread_bps cannot be negative")

    @property
    def participation_ratio(self) -> float:
        """Quantity relative to daily volume: quantity / ADV."""
        return self.quantity / self.adv

    @property
    def sqrt_participation(self) -> float:
        """Square root of the participation ratio, the model's explanatory term."""
        return math.sqrt(self.participation_ratio)

    @property
    def residual_slippage_bps(self) -> float:
        """Slippage net of the half-spread baseline the model attributes."""
        return self.realized_slippage_bps - self.half_spread_bps


@dataclass(frozen=True, slots=True)
class ImpactCalibration:
    """Least-squares fit of ``residual = eta * volatility * sqrt_participation``."""

    eta: float
    r_squared: float
    observations: int
    symbol: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "eta": round(self.eta, 6),
            "r_squared": round(self.r_squared, 6),
            "observations": self.observations,
        }


class SquareRootImpactCalibrator:
    """Per-symbol online calibrator for the square-root impact law.

    Each :meth:`observe` call appends one fill; :meth:`calibration` performs
    a closed-form least-squares regression of residual slippage (realized
    minus half-spread) on ``volatility * sqrt(participation)``, yielding the
    ``eta`` coefficient. A minimum number of observations is required before
    the calibration is considered usable (``min_observations``).
    """

    def __init__(self, min_observations: int = 30) -> None:
        if min_observations < 1:
            raise ValueError("min_observations must be >= 1")
        self._min_observations = min_observations
        self._observations: dict[str, list[ImpactObservation]] = {}

    def observe(self, symbol: str, obs: ImpactObservation) -> None:
        """Record one fill for ``symbol``."""
        self._observations.setdefault(symbol, []).append(obs)

    def observation_count(self, symbol: str) -> int:
        """Number of fills recorded for ``symbol``."""
        return len(self._observations.get(symbol, ()))

    def calibration(self, symbol: str) -> ImpactCalibration | None:
        """Best-fit impact calibration for ``symbol``, or None if unusable.

        Returns None when fewer than ``min_observations`` fills exist, when
        there is no variance in the explanatory term, or when the fit is
        numerically degenerate. The regression is unconstrained: a negative
        ``eta`` is kept and simply means the symbol's slippage is *below* the
        spread baseline (maker routing or abundant liquidity).
        """
        obs_list = self._observations.get(symbol, ())
        if len(obs_list) < self._min_observations:
            return None

        xs: list[float] = []
        ys: list[float] = []
        for obs in obs_list:
            xs.append(obs.volatility_bps * obs.sqrt_participation)
            ys.append(obs.residual_slippage_bps)

        mean_x = statistics.fmean(xs)
        var_x = statistics.fmean((x - mean_x) ** 2 for x in xs)
        if var_x == 0.0:
            return None
        mean_y = statistics.fmean(ys)
        cov = statistics.fmean((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
        eta = cov / var_x

        variance_y = statistics.fmean((y - mean_y) ** 2 for y in ys)
        if variance_y == 0.0:
            r_squared = 1.0
        else:
            ss_res = statistics.fmean((y - (eta * x)) ** 2 for x, y in zip(xs, ys, strict=True))
            r_squared = 1.0 - (ss_res / variance_y)

        return ImpactCalibration(
            eta=eta,
            r_squared=max(0.0, min(r_squared, 1.0)),
            observations=len(obs_list),
            symbol=symbol,
        )

    def estimate_impact_bps(
        self,
        symbol: str,
        *,
        quantity: float,
        adv: float,
        volatility_bps: float,
        half_spread_bps: float,
    ) -> float | None:
        """Expected impact in bps for a prospective order, using calibration.

        Returns None when no usable calibration exists yet for the symbol.
        """
        if quantity <= 0.0 or adv <= 0.0:
            raise ValueError("quantity and adv must be positive")
        calib = self.calibration(symbol)
        if calib is None:
            return None
        participation = quantity / adv
        impact = half_spread_bps + calib.eta * volatility_bps * math.sqrt(participation)
        return max(0.0, impact)
