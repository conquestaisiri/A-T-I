# backend/application/research/portfolio_allocator.py
"""Correlation-aware portfolio allocation (task T2-14-1).

The portfolio-level half of the correlation work: consume the measured
correlation matrix (T2-13-2) plus per-strategy evidence scores and size
the portfolio so that redundancy is discounted and independence is
rewarded.

Algorithm (deterministic, one pass, no iteration)
-------------------------------------------------
For each strategy ``i`` with a positive score, the redundancy load is

    R_i = sum_{j != i} max(0, rho_ij)          (positive correlations only)

normalised by its maximum possible value (``n - 1`` fully-correlated
peers), and the dampening is

    damp_i = 1 / (1 + sensitivity * R_i / (n - 1))

then ``weight_i = score_i * damp_i``, renormalised to sum to 1. A pair
at rho = 1 dampens both members equally (score ratio preserved, combined
claim discounted); negative correlations never count against a strategy
(conservative: independence is only credited when it is real).

Honesty rules
-------------
- A scored strategy missing from the matrix is a ValueError: an
  allocation that skipped its correlation surface would be fabricated.
- No positive scores -> ValueError: nothing to allocate.
- Zero-score strategies are excluded with weight 0.0 and dampening 1.0,
  recorded in the allocation.
- Non-finite or negative scores raise. Garbage in, no allocation out.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.domain.research.portfolio_allocator import AllocatedWeight, PortfolioAllocation
from backend.domain.research.portfolio_correlations import PortfolioCorrelationMatrix

DEFAULT_CORRELATION_SENSITIVITY = 1.0


def allocate_correlation_damped(
    scores: Mapping[str, float],
    matrix: PortfolioCorrelationMatrix,
    *,
    correlation_sensitivity: float = DEFAULT_CORRELATION_SENSITIVITY,
) -> PortfolioAllocation:
    """Size the portfolio from evidence scores and the measured correlation matrix."""
    if correlation_sensitivity < 0.0:
        raise ValueError("correlation_sensitivity must be non-negative")
    if not scores:
        raise ValueError("no scores to allocate")

    score_by_id = {}
    for strategy_id, score in scores.items():
        if not float(score) == score or score == float("inf") or score == float("-inf"):
            raise ValueError(f"non-finite score for {strategy_id!r}")
        if score < 0.0:
            raise ValueError(f"negative score for {strategy_id!r}")
        score_by_id[strategy_id] = score

    matrix_ids = set(matrix.ids)
    missing = [strategy_id for strategy_id in score_by_id if strategy_id not in matrix_ids]
    if missing:
        raise ValueError(
            f"scored strategies missing from the correlation matrix: {sorted(missing)}"
        )

    n = len(matrix.ids)
    if n < 1:
        raise ValueError("correlation matrix has no strategies")

    positive = {strategy_id for strategy_id, score in score_by_id.items() if score > 0.0}
    if not positive:
        raise ValueError("no positive scores to allocate")

    position = {strategy_id: index for index, strategy_id in enumerate(matrix.ids)}

    weights: list[AllocatedWeight] = []
    raw: dict[str, float] = {}
    dampening: dict[str, float] = {}
    for strategy_id in matrix.ids:
        score = score_by_id.get(strategy_id, 0.0)
        if score <= 0.0:
            dampening[strategy_id] = 1.0
            raw[strategy_id] = 0.0
            continue
        redundancy = 0.0
        for other in matrix.ids:
            if other == strategy_id:
                continue
            rho = matrix.matrix[position[strategy_id]][position[other]]
            if rho > 0.0:
                redundancy += rho
        damp = (
            1.0
            if len(positive) == 1
            else 1.0 / (1.0 + correlation_sensitivity * redundancy / (len(positive) - 1))
        )
        dampening[strategy_id] = damp
        raw[strategy_id] = score * damp

    raw_total = sum(raw.values())
    for strategy_id in matrix.ids:
        score = score_by_id.get(strategy_id, 0.0)
        weight = raw[strategy_id] / raw_total if raw_total > 0.0 else 0.0
        # correlation load: the final portfolio's weight on correlated peers
        load = 0.0
        for other in matrix.ids:
            if other == strategy_id:
                continue
            load += matrix.matrix[position[strategy_id]][position[other]] * (
                raw[other] / raw_total if raw_total > 0.0 else 0.0
            )
        weights.append(
            AllocatedWeight(
                strategy_id=strategy_id,
                score=score,
                weight=weight,
                dampening=dampening[strategy_id],
                correlation_load=load,
            )
        )

    return PortfolioAllocation(
        weights=tuple(weights),
        correlation_sensitivity=correlation_sensitivity,
    )


__all__ = ["allocate_correlation_damped"]
