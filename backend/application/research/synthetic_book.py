# backend/application/research/synthetic_book.py
"""Synthetic order book for capacity-pipeline tests (task T2-18-2).

T2-18-2's gate ("wire-in only after the simulator-vs-execution proof",
P5-006) is open: P5-006 is DONE, so the capacity pipeline can now be
proven end-to-end against a synthetic microstructure that follows the
square-root impact law *by construction*:

- Each ``fill`` realizes slippage = half_spread + eta * volatility *
  sqrt(quantity / ADV) plus seeded Gaussian noise, exactly the model the
  calibrator (integration #26) fits and the capacity model (T2-18-1)
  inverts.
- A test can therefore (a) feed fills into
  ``SquareRootImpactCalibrator`` and check the recovered ``eta`` tracks the
  true one, then (b) feed the calibration into ``CapacityModel`` and check
  the recovered capacity bound's impact equals the budget — proving the
  whole fills -> calibration -> capacity chain, not just isolated math.

The book is deterministic under a caller-supplied seed. stdlib-only.
"""

from __future__ import annotations

import math
import random


class SyntheticFillBook:
    """A symbol whose fills realize slippage per the square-root law.

    Parameters
    ----------
    symbol: str
        The symbol this book stands in for.
    adv: float
        Average daily volume in units.
    volatility_bps: float
        Annualised volatility in basis points.
    half_spread_bps: float
        Half the quoted spread in basis points.
    eta: float
        The book's true impact coefficient (ground truth for tests).
    noise_bps: float
        Standard deviation of the per-fill slippage noise in bps.
    seed: int | None
        Seed for the deterministic noise stream.
    """

    def __init__(
        self,
        *,
        symbol: str,
        adv: float,
        volatility_bps: float,
        half_spread_bps: float,
        eta: float,
        noise_bps: float = 0.0,
        seed: int | None = None,
    ) -> None:
        if adv <= 0.0:
            raise ValueError("adv must be positive")
        if volatility_bps < 0.0 or half_spread_bps < 0.0:
            raise ValueError("volatility_bps and half_spread_bps must be non-negative")
        if eta < 0.0:
            raise ValueError("eta must be non-negative (a synthetic book models positive impact)")
        if noise_bps < 0.0:
            raise ValueError("noise_bps must be non-negative")
        self.symbol = symbol
        self.adv = adv
        self.volatility_bps = volatility_bps
        self.half_spread_bps = half_spread_bps
        self.eta = eta
        self.noise_bps = noise_bps
        self._rng = random.Random(seed)

    def fill(self, quantity: float) -> float:
        """Realize one fill: slippage per the square-root law plus noise.

        Returns the realized slippage in basis points, the quantity the
        calibrator's ``ImpactObservation`` expects.
        """
        if quantity <= 0.0:
            raise ValueError("quantity must be positive")
        expected = self.half_spread_bps + self.eta * self.volatility_bps * math.sqrt(
            quantity / self.adv
        )
        if self.noise_bps > 0.0:
            expected += self._rng.gauss(0.0, self.noise_bps)
        return max(0.0, expected)
