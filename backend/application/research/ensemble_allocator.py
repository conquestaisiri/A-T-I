# backend/application/research/ensemble_allocator.py
"""Ensemble/competition wiring into allocation (task T2-13-1).

The research pipeline's last honest step before any allocation: take the
strategy population registry (T2-12), keep only candidates whose evidence
passed the gates, and feed them into the risk-parity allocator (P3-003) as
``StrategyProfile``s derived *only* from numbers the passport supports.

Wiring rules
------------
- **Eligibility = evidence gates passed.** A passport competes iff its
  verdict is ``PROMOTE_TO_PAPER`` and its status is not ``RETIRED``.
  Everything else is excluded with a recorded reason (REJECT, OBSERVE =
  insufficient evidence, RESEARCH = not evaluated).
- **Volatility is operator-supplied, never guessed.** ``volatility_pct_by_id``
  maps passport id -> annualized volatility estimate. A competing passport
  without an entry is excluded — fabricating a risk number from pooled
  evidence would poison the risk-parity math.
- **Expected return is the pooled mean excess** (the passport's own number,
  same frame the verdict gates grade); candidates without one are excluded.
- **Regime fit = T2-11-1 regime robustness score** when present, else the
  neutral 1.0 (no regime evidence against the candidate).
- **Correlation comes from shared evidence (T2-13-2), never guessed.**
  Either the caller supplies an explicit ``correlations`` matrix, or
  ``returns_by_id`` (aligned shared OOS bar/fold return series) from which
  the pairwise Pearson surface is measured; candidates without a series are
  excluded with a recorded reason. Unmeasurable pairs are neutral 0.0 with
  a recorded state — an unknown correlation is never credited as
  diversification.
- **The risk gate stays in charge.** ``risk_gate_allowed`` is passed through
  to the allocator, which refuses the allocation structurally when blocked.

This module is library/research only: nothing here reaches the live path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from backend.application.research.portfolio_correlations import correlations_from_returns
from backend.application.research.strategy_allocator import (
    AllocationConfig,
    allocate_strategies,
)
from backend.application.research.strategy_population import StrategyPopulationService
from backend.domain.research.allocation import StrategyProfile
from backend.domain.research.ensemble import EnsembleAllocationResult
from backend.domain.research.passport import EvidenceVerdict, PassportStatus
from backend.domain.research.strategy_population import StrategyPopulation


class EnsembleAllocator:
    """Wire the population registry into the risk-parity allocator.

    Parameters
    ----------
    population: StrategyPopulationService | StrategyPopulation
        The population source: either the service (its ``registry()`` is
        read per call) or an already-built registry projection.
    min_regime_fit: float
        Allocator gate: candidates with regime fit strictly below this are
        eliminated from the competition (default 0.0 = no elimination).
    """

    def __init__(
        self,
        population: StrategyPopulationService | StrategyPopulation,
        *,
        min_regime_fit: float = 0.0,
    ) -> None:
        if not 0.0 <= min_regime_fit <= 1.0:
            raise ValueError("min_regime_fit must be in [0, 1]")
        self._population = population
        self._min_regime_fit = min_regime_fit

    def allocate(
        self,
        *,
        risk_budget_pct: float,
        volatility_pct_by_id: Mapping[str, float],
        correlations: Sequence[Sequence[float]] | None = None,
        returns_by_id: Mapping[str, Sequence[float]] | None = None,
        risk_gate_allowed: bool = True,
        blocked_reason: str | None = None,
    ) -> EnsembleAllocationResult:
        """Allocate the risk budget across gate-passing candidates.

        ``volatility_pct_by_id`` must name every competing candidate; a
        competing passport without a volatility estimate is excluded (never
        guessed). Unknown ids in the mapping are ignored: the mapping may be
        a superset of the population.

        Correlation input (T2-13-2) comes from exactly one source: either
        ``correlations`` (explicit matrix) or ``returns_by_id`` (aligned
        shared OOS return series per passport, from which the pairwise
        Pearson surface is measured; a competing passport without a series
        is excluded). Supplying both is a contradiction and raises
        ``ValueError``; supplying neither leaves the allocator on the
        identity surface (no diversification credit).
        """
        if correlations is not None and returns_by_id is not None:
            raise ValueError("supply either correlations or returns_by_id, not both")
        registry = (
            self._population.registry()
            if isinstance(self._population, StrategyPopulationService)
            else self._population
        )
        profiles, excluded = self._profiles(registry, volatility_pct_by_id)

        correlation_matrix: tuple[tuple[float, ...], ...] | None
        if returns_by_id is not None:
            profiles, series_excluded = self._series_complete(profiles, returns_by_id)
            excluded.extend(series_excluded)
            if not profiles:
                return EnsembleAllocationResult(
                    allocation=None,
                    competitors=(),
                    excluded=tuple(excluded),
                    reason="no candidate passed the evidence gates with a return series",
                )
            if len(profiles) == 1:
                # A single competitor has no pairwise surface to measure;
                # the allocator's identity fallback is exactly right.
                correlation_matrix = None
            else:
                measured = correlations_from_returns(
                    {profile.name: returns_by_id[profile.name] for profile in profiles}
                )
                correlation_matrix = tuple(tuple(row) for row in measured.matrix)
        else:
            correlation_matrix = (
                tuple(tuple(row) for row in correlations) if correlations is not None else None
            )

        if not profiles:
            return EnsembleAllocationResult(
                allocation=None,
                competitors=(),
                excluded=tuple(excluded),
                reason="no candidate passed the evidence gates with a volatility estimate",
            )
        result = allocate_strategies(
            strategies=profiles,
            risk_budget_pct=risk_budget_pct,
            correlations=correlation_matrix,
            risk_gate_allowed=risk_gate_allowed,
            blocked_reason=blocked_reason,
            config=AllocationConfig(min_regime_fit=self._min_regime_fit),
        )
        return EnsembleAllocationResult(
            allocation=result,
            competitors=tuple(profile.name for profile in profiles),
            excluded=tuple(excluded),
            reason="allocated" if not result.blocked else "risk gate blocked the allocation",
        )

    def _profiles(
        self,
        registry: StrategyPopulation,
        volatility_pct_by_id: Mapping[str, float],
    ) -> tuple[list[StrategyProfile], list[tuple[str, str]]]:
        """Gate-passing candidates -> allocator profiles; the rest -> reasons."""
        profiles: list[StrategyProfile] = []
        excluded: list[tuple[str, str]] = []
        for member in registry.members:
            pid = member.passport_id
            if member.verdict is not EvidenceVerdict.PROMOTE_TO_PAPER:
                excluded.append(
                    (pid, f"verdict {member.verdict.value!r}: evidence gates did not pass")
                )
                continue
            if member.status is PassportStatus.RETIRED:
                excluded.append((pid, "status retired: strategy is dead"))
                continue
            if member.mean_excess_return_pct is None:
                excluded.append((pid, "pooled mean excess return unavailable"))
                continue
            volatility = volatility_pct_by_id.get(pid)
            if volatility is None:
                excluded.append((pid, "no operator-supplied volatility estimate"))
                continue
            if not isinstance(volatility, (int, float)) or volatility <= 0.0:
                excluded.append((pid, "volatility estimate must be a positive number"))
                continue
            profiles.append(
                StrategyProfile(
                    name=pid,
                    expected_return_pct=member.mean_excess_return_pct,
                    volatility_pct=float(volatility),
                    regime_fit=(
                        member.regime_robustness_score
                        if member.regime_robustness_score is not None
                        else 1.0
                    ),
                )
            )
        return profiles, excluded

    def _series_complete(
        self,
        profiles: list[StrategyProfile],
        returns_by_id: Mapping[str, Sequence[float]],
    ) -> tuple[list[StrategyProfile], list[tuple[str, str]]]:
        """Drop competing candidates without a shared return series.

        When correlation is sourced from ``returns_by_id``, a candidate
        without a series cannot contribute its correlation surface; it is
        excluded with a recorded reason (never guessed). Unknown ids in the
        mapping are ignored.
        """
        complete: list[StrategyProfile] = []
        excluded: list[tuple[str, str]] = []
        for profile in profiles:
            series = returns_by_id.get(profile.name)
            if series is None:
                excluded.append((profile.name, "no shared OOS return series for correlation"))
                continue
            complete.append(profile)
        return complete, excluded
