# backend/application/research/strategy_allocator.py
"""Strategy allocator (task P3-003).

Divides a risk budget (a portfolio volatility cap) between competing
strategies, using risk parity scaled to the budget, re-weighted by each
strategy's fit to the current regime, and always subordinated to the risk
gate.

Design rules
------------
- **Competition is explicit.** Strategies whose ``regime_fit`` is below
  ``min_regime_fit`` are eliminated from the competition; among the survivors,
  the weight shape is risk parity ``fit / volatility`` scaled so the portfolio
  consumes exactly the risk budget. Higher-risk strategies and weaker-fit
  strategies claim less budget by construction.
- **Correlation is included.** The covariance matrix is built from each
  strategy's volatility and the pairwise correlations; the scaling factor is
  derived from the *portfolio* variance ``w^T Sigma w``, so correlated
  strategies dilute each other's claim and the result's portfolio volatility
  is at the budget cap.
- **The risk gate cannot be bypassed.** The allocator takes an explicit
  ``risk_gate_allowed`` flag. When the gate vetoes new exposure the result is
  ``status="blocked"`` with every weight zero and the veto recorded — an
  attractive strategy can never override it. This invariant is structural:
  block means zero allocation, regardless of inputs.

The engine is deterministic and uses only the arithmetic of the inputs.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np

from backend.domain.research.allocation import (
    AllocationResult,
    StrategyAllocation,
    StrategyProfile,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AllocationConfig:
    """Allocator behaviour knobs.

    Attributes
    ----------
    min_regime_fit: float
        Strategies with ``regime_fit`` strictly below this are eliminated from
        the competition (default ``0.0`` = no elimination).
    """

    min_regime_fit: float = 0.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_regime_fit <= 1.0:
            raise ValueError("min_regime_fit must be in [0, 1]")


def allocate_strategies(
    *,
    strategies: tuple[StrategyProfile, ...] | list[StrategyProfile],
    risk_budget_pct: float,
    correlations: tuple[tuple[float, ...], ...] | None = None,
    risk_gate_allowed: bool = True,
    blocked_reason: str | None = None,
    config: AllocationConfig | None = None,
) -> AllocationResult:
    """Allocate ``risk_budget_pct`` of portfolio volatility across strategies.

    Returns a ``"blocked"`` result with zero weights when ``risk_gate_allowed``
    is False, so a risk-gate veto always wins over any allocation.
    """
    config = config or AllocationConfig()
    profiles = tuple(strategies)
    _validate(profiles, risk_budget_pct, correlations, config)
    covariance = _covariance_matrix(
        profiles, correlations if correlations is not None else _identity(len(profiles))
    )

    if not risk_gate_allowed:
        reason = blocked_reason or "risk gate veto"
        return _result(
            allocations=(),
            status="blocked",
            risk_budget_pct=risk_budget_pct,
            blocked_reason=reason,
        )

    eligible_indices = [i for i, s in enumerate(profiles) if s.regime_fit >= config.min_regime_fit]
    if not eligible_indices:
        return _result(
            allocations=(),
            status="allocated",
            risk_budget_pct=risk_budget_pct,
        )

    eligible = [profiles[i] for i in eligible_indices]
    sub_covariance = covariance[np.ix_(eligible_indices, eligible_indices)]
    weights = _risk_parity_weights(eligible, sub_covariance)
    allocations, portfolio_vol = _allocate_and_bound(
        eligible, weights, sub_covariance, risk_budget_pct
    )
    expected_return = sum(
        a.weight * profile.expected_return_pct
        for a, profile in zip(allocations, eligible, strict=True)
    )
    return _result(
        allocations=tuple(allocations),
        status="allocated",
        risk_budget_pct=risk_budget_pct,
        portfolio_expected_return_pct=round(expected_return, 6),
        portfolio_volatility_pct=round(portfolio_vol, 6),
    )


# -- internal machinery -------------------------------------------------


def _validate(
    profiles: tuple[StrategyProfile, ...],
    risk_budget_pct: float,
    correlations: tuple[tuple[float, ...], ...] | None,
    config: AllocationConfig,
) -> None:
    if risk_budget_pct <= 0.0 or not math.isfinite(risk_budget_pct):
        raise ValueError("risk_budget_pct must be a finite, positive percentage")
    if not profiles:
        raise ValueError("at least one strategy is required")
    names = [s.name for s in profiles]
    if len(set(names)) != len(names):
        raise ValueError("strategy names must be unique")
    for s in profiles:
        if s.volatility_pct <= 0.0 or not math.isfinite(s.volatility_pct):
            raise ValueError(f"strategy {s.name!r} volatility_pct must be positive and finite")
        if not 0.0 <= s.regime_fit <= 1.0:
            raise ValueError(f"strategy {s.name!r} regime_fit must be in [0, 1]")
        if not math.isfinite(s.expected_return_pct):
            raise ValueError(f"strategy {s.name!r} expected_return_pct must be finite")
    if correlations is not None:
        n = len(profiles)
        if len(correlations) != n or any(len(row) != n for row in correlations):
            raise ValueError("correlations must be an n x n matrix for the strategies")
        for i in range(n):
            for j in range(n):
                value = correlations[i][j]
                if not -1.0 <= value <= 1.0:
                    raise ValueError("correlation values must be in [-1, 1]")
                if (i == j and value != 1.0) or (correlations[i][j] != correlations[j][i]):
                    raise ValueError("correlations must be symmetric with unit diagonal")
    _ = config  # validation of min_regime_fit happens in AllocationConfig.__post_init__


def _identity(n: int) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(1.0 if i == j else 0.0 for j in range(n)) for i in range(n))


def _covariance_matrix(
    profiles: tuple[StrategyProfile, ...],
    correlations: tuple[tuple[float, ...], ...],
) -> np.ndarray:
    n = len(profiles)
    covariance = np.zeros((n, n), dtype=float)
    for i in range(n):
        for j in range(n):
            covariance[i][j] = (
                profiles[i].volatility_pct * profiles[j].volatility_pct * correlations[i][j]
            )
    return covariance


def _risk_parity_weights(profiles: list[StrategyProfile], covariance: np.ndarray) -> np.ndarray:
    """Shape weights as ``regime_fit / volatility`` (risk-parity pre-budget)."""
    n = len(profiles)
    shape = np.zeros(n, dtype=float)
    for i, profile in enumerate(profiles):
        shape[i] = profile.regime_fit / profile.volatility_pct
    portfolio_var = float(shape @ covariance @ shape)
    if portfolio_var <= 0.0:
        return np.full(n, 1.0 / n, dtype=float)
    return shape / math.sqrt(portfolio_var)


def _allocate_and_bound(
    eligible: list[StrategyProfile],
    unit_weights: np.ndarray,
    covariance: np.ndarray,
    risk_budget_pct: float,
) -> tuple[list[StrategyAllocation], float]:
    """Scale unit weights to exactly the risk budget and build the result.

    ``unit_weights`` already have portfolio variance 1.0, so scaling by the
    budget yields portfolio volatility == budget (up to rounding).
    """
    scaled = unit_weights * risk_budget_pct
    portfolio_var = float(scaled @ covariance @ scaled)
    portfolio_vol = math.sqrt(max(portfolio_var, 0.0))
    allocations = [
        StrategyAllocation(
            strategy_name=profile.name,
            weight=round(float(scaled[i]), 6),
            reason=_reason(profile, float(scaled[i])),
        )
        for i, profile in enumerate(eligible)
    ]
    allocations.sort(key=lambda a: a.weight, reverse=True)
    return allocations, portfolio_vol


def _reason(profile: StrategyProfile, weight: float) -> str:
    return (
        f"risk-parity weight scaled to budget (vol {profile.volatility_pct}%, "
        f"fit {profile.regime_fit})"
    )


def _result(
    *,
    allocations: tuple[StrategyAllocation, ...],
    status: str,
    risk_budget_pct: float,
    portfolio_expected_return_pct: float = 0.0,
    portfolio_volatility_pct: float = 0.0,
    blocked_reason: str | None = None,
) -> AllocationResult:
    return AllocationResult(
        allocations=allocations,
        status=status,
        risk_budget_pct=risk_budget_pct,
        portfolio_expected_return_pct=portfolio_expected_return_pct,
        portfolio_volatility_pct=portfolio_volatility_pct,
        blocked_reason=blocked_reason,
    )
