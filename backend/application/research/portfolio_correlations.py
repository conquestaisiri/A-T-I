# backend/application/research/portfolio_correlations.py
"""Portfolio-level correlation extraction (task T2-13-2).

Given each candidate's aligned out-of-sample return series (shared OOS
bars or folds), produce the pairwise Pearson correlation surface that the
ensemble allocator (T2-13-1) consumes.

Design rules
------------
- **Misaligned series are refused, never patched.** All series must share
  the same length; differing lengths raise ``ValueError``. A correlation
  across differently aligned windows would be fabricated.
- **Unmeasurable pairs are neutral, with a reason.** A pair with a
  constant series or fewer than two shared observations enters the matrix
  as 0.0 (no diversification credit) and is recorded in ``pairs`` with its
  state. 0.0 is the conservative default: an unknown correlation is not
  assumed to be beneficial.
- **The output plugs straight into the allocator.** ``matrix`` is
  symmetric with unit diagonal and values in [-1, 1], the exact shape
  ``allocate_strategies`` validates.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

from backend.domain.research.portfolio_correlations import (
    PairCorrelation,
    PairCorrelationState,
    PortfolioCorrelationMatrix,
)

_MIN_SHARED_OBSERVATIONS = 2


def correlations_from_returns(
    series_by_id: Mapping[str, Sequence[float]],
) -> PortfolioCorrelationMatrix:
    """Pairwise correlation matrix from aligned per-passport return series.

    Parameters
    ----------
    series_by_id:
        passport id -> per-period return series (percent or any linear
        scale; only the correlation is used). Every series must have the
        same length; ids are sorted for a deterministic matrix order.

    Raises
    ------
    ValueError:
        When fewer than two ids are supplied, series differ in length, or
        a series contains a non-finite value.
    """
    ids = tuple(sorted(series_by_id))
    if len(ids) < 2:
        raise ValueError("at least two series are required to estimate correlations")
    length = _validate_series(ids, series_by_id)

    pairs: list[PairCorrelation] = []
    matrix = [[0.0 for _ in range(len(ids))] for _ in range(len(ids))]
    for i, left in enumerate(ids):
        matrix[i][i] = 1.0
        for j in range(i + 1, len(ids)):
            right = ids[j]
            pair = _estimate_pair(left, right, series_by_id[left], series_by_id[right], length)
            pairs.append(pair)
            value = pair.value if pair.value is not None else 0.0
            matrix[i][j] = value
            matrix[j][i] = value

    return PortfolioCorrelationMatrix(
        ids=ids,
        matrix=tuple(tuple(row) for row in matrix),
        pairs=tuple(pairs),
    )


def _validate_series(ids: tuple[str, ...], series_by_id: Mapping[str, Sequence[float]]) -> int:
    """All series must share one finite length; return that length."""
    length: int | None = None
    for pid in ids:
        series = series_by_id[pid]
        if not isinstance(series, Sequence) or isinstance(series, (str, bytes)):
            raise ValueError(f"series for {pid!r} must be a sequence of numbers")
        if length is None:
            length = len(series)
        elif len(series) != length:
            raise ValueError(
                f"series lengths differ: {pid!r} has {len(series)} observations, expected {length}"
            )
        if length < _MIN_SHARED_OBSERVATIONS:
            raise ValueError(
                f"series for {pid!r} has {length} observation(s); at least "
                f"{_MIN_SHARED_OBSERVATIONS} are required"
            )
        for value in series:
            if not isinstance(value, (int, float)):
                raise ValueError(f"series for {pid!r} contains a non-numeric value")
            if not math.isfinite(float(value)):
                raise ValueError(f"series for {pid!r} contains a non-finite value")
    assert length is not None
    return length


def _estimate_pair(
    left: str,
    right: str,
    left_series: Sequence[float],
    right_series: Sequence[float],
    n_shared: int,
) -> PairCorrelation:
    """Pearson r for one pair, or a recorded neutral state."""
    if n_shared < _MIN_SHARED_OBSERVATIONS:
        return PairCorrelation(
            left=left,
            right=right,
            state=PairCorrelationState.INSUFFICIENT_OBSERVATIONS,
            value=None,
            n_shared=n_shared,
        )
    mean_left = sum(left_series) / n_shared
    mean_right = sum(right_series) / n_shared
    covariance = 0.0
    var_left = 0.0
    var_right = 0.0
    for x, y in zip(left_series, right_series, strict=True):
        dx = float(x) - mean_left
        dy = float(y) - mean_right
        covariance += dx * dy
        var_left += dx * dx
        var_right += dy * dy
    if var_left <= 0.0 or var_right <= 0.0:
        return PairCorrelation(
            left=left,
            right=right,
            state=PairCorrelationState.CONSTANT_SERIES,
            value=None,
            n_shared=n_shared,
        )
    r = covariance / math.sqrt(var_left * var_right)
    r = max(-1.0, min(1.0, r))
    return PairCorrelation(
        left=left,
        right=right,
        state=PairCorrelationState.MEASURED,
        value=r,
        n_shared=n_shared,
    )
