# backend/domain/research/portfolio_correlations.py
"""Portfolio-level correlation contracts (task T2-13-2).

Diversification must be *measured from shared evidence*, never asserted.
This module is the contract for the pairwise correlation surface of a
strategy portfolio, derived from the strategies' aligned out-of-sample
return series (shared OOS bars / folds).

Honesty invariants
------------------
- **Correlation is measured or neutral, never guessed.** A pair whose
  correlation cannot be estimated (constant series, fewer than two shared
  observations) is recorded as unmeasured with a reason and enters the
  matrix as the neutral 0.0 — diversification is then not credited to that
  pair, which is the conservative direction for a portfolio that wants to
  spend its risk budget on genuinely independent edges.
- **The matrix is auditable.** Every pair's measurement state and shared
  observation count is kept alongside the matrix, so an allocation can
  reproduce exactly which correlations were measured and which were not.
- **Alignment is caller-checked.** The extractor refuses misaligned series
  (differing lengths) outright: a correlation between differently aligned
  windows would be fabricated.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class PairCorrelationState(enum.StrEnum):
    """How one pair's correlation value was obtained.

    MEASURED: Pearson correlation over the shared return series.
    CONSTANT_SERIES: at least one series has zero variance; the pair is
        unmeasured and neutral (0.0).
    INSUFFICIENT_OBSERVATIONS: fewer than two shared observations; the
        pair is unmeasured and neutral (0.0).
    """

    MEASURED = "measured"
    CONSTANT_SERIES = "constant_series"
    INSUFFICIENT_OBSERVATIONS = "insufficient_observations"


@dataclass(frozen=True, slots=True)
class PairCorrelation:
    """One measured-or-neutral pair in the portfolio.

    Attributes
    ----------
    left, right: str
        The two passport ids (left < right lexicographically, so each pair
        appears exactly once).
    state: PairCorrelationState
        How the value was obtained.
    value: float | None
        The correlation used in the matrix: the measured Pearson r when
        MEASURED, else None (the matrix carries the neutral 0.0).
    n_shared: int
        Number of aligned observations the pair shares.
    """

    left: str
    right: str
    state: PairCorrelationState
    value: float | None
    n_shared: int

    def as_dict(self) -> dict[str, Any]:
        """Serialise the pair to a plain dictionary."""
        return {
            "left": self.left,
            "right": self.right,
            "state": self.state.value,
            "value": round(self.value, 8) if self.value is not None else None,
            "n_shared": self.n_shared,
        }


@dataclass(frozen=True, slots=True)
class PortfolioCorrelationMatrix:
    """The portfolio's pairwise correlation surface.

    Attributes
    ----------
    ids: tuple[str, ...]
        Passport ids in matrix order.
    matrix: tuple[tuple[float, ...], ...]
        Symmetric n x n matrix, unit diagonal, values in [-1, 1]. Pairs that
        could not be measured carry the neutral 0.0 (see ``pairs``).
    pairs: tuple[PairCorrelation, ...]
        One entry per unordered pair, with measurement state and shared
        observation count (the audit trail for the matrix).
    """

    ids: tuple[str, ...]
    matrix: tuple[tuple[float, ...], ...]
    pairs: tuple[PairCorrelation, ...]

    def as_matrix(self) -> list[list[float]]:
        """The matrix as nested lists (allocator input shape)."""
        return [list(row) for row in self.matrix]

    def as_dict(self) -> dict[str, Any]:
        """Serialise the matrix to a plain dictionary."""
        return {
            "ids": list(self.ids),
            "matrix": self.as_matrix(),
            "pairs": [pair.as_dict() for pair in self.pairs],
        }


__all__ = [
    "PairCorrelation",
    "PairCorrelationState",
    "PortfolioCorrelationMatrix",
]
