# backend/application/research/capital_allocator.py
"""Portfolio-level capital allocation service (task T3-29-1).

The store-backed optimizer: it projects the passport population (P5-003,
the single source of truth), applies the evidence gate per passport, sizes
the eligible strategies with the correlation-aware allocation from T2-14-1
(the measured matrix from T2-13-2 discounts redundancy), and can turn any
current allocation into the exact rebalancing deltas.

Guardrail — *no allocation before evidence gates*: a passport earns capital
only when its pooled evidence passed ``verdict_for_evidence``
(PROMOTE_TO_PAPER) and it is not retired. REJECTed strategies failed the
gates; OBSERVE means the evidence is insufficient — never allocate on
insufficient evidence. Unevaluated passports (no pooled block) and dead
ones (RETIRED, T3-28-1 tombstone) get no capital. Every exclusion is named
in the plan, so the capital decision is auditable.

Library-only: nothing in the live path imports this service. The operator
runs it deliberately when real population evidence exists.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.application.interfaces.passport_store import PassportStore
from backend.application.research.portfolio_allocator import (
    allocate_correlation_damped,
)
from backend.domain.research.capital_allocation import (
    AllocationDelta,
    AllocationVerdict,
    CapitalAllocationPlan,
)
from backend.domain.research.passport import EvidenceVerdict, PassportStatus
from backend.domain.research.portfolio_correlations import PortfolioCorrelationMatrix
from backend.domain.research.strategy_population import (
    PopulationMember,
    member_from_passport,
)

_ELIGIBLE_REASON = "eligible: evidence gates passed"
_GATE_REJECTED = "excluded: evidence gates failed (REJECT)"
_GATE_OBSERVE = "excluded: insufficient evidence (OBSERVE); never allocate on insufficient evidence"
_GATE_UNEVALUATED = "excluded: no evaluated evidence (no pooled folds)"
_GATE_DEAD = "excluded: retired (terminal); dead strategies get no capital"
_GATE_NO_SCORE = "excluded: evidence score unavailable"
_GATE_BELOW_FLOOR = "excluded: score below the minimum (min_score)"


class CapitalAllocationService:
    """Allocate the portfolio risk budget from passports + measured correlations.

    Parameters
    ----------
    store: PassportStore
        The passport ledger (the population seed, P5-003).
    min_score: float
        Floor on the evidence score (pooled mean excess return pct): an
        eligible passport scoring below it earns no capital. Default 0.0 —
        only positive net excess return is worth capital.
    correlation_sensitivity: float
        Forwarded to the T2-14-1 dampening (how strongly redundancy is
        discounted). Default 1.0.
    """

    def __init__(
        self,
        store: PassportStore,
        *,
        min_score: float = 0.0,
        correlation_sensitivity: float = 1.0,
    ) -> None:
        self._store = store
        self._min_score = min_score
        self._correlation_sensitivity = correlation_sensitivity

    def plan(self, matrix: PortfolioCorrelationMatrix) -> CapitalAllocationPlan:
        """Compute the auditable capital plan for the whole population.

        Every passport gets a verdict (eligible or named exclusion); the
        eligible ones are sized by the correlation-damped allocator. When
        nothing is eligible the allocation is None with the reason — never
        an empty fabricated portfolio. When an eligible strategy is missing
        from the correlation matrix the plan refuses (ValueError): an
        allocation that skipped its correlation surface would be fabricated
        (T2-14-1 honesty rule).
        """
        verdicts: list[AllocationVerdict] = []
        scores: dict[str, float] = {}
        for passport in self._store.all_passports():
            member = member_from_passport(passport)
            verdict, score = self._verdict(member)
            verdicts.append(verdict)
            if verdict.eligible and score is not None:
                scores[member.passport_id] = score

        if not scores:
            return CapitalAllocationPlan(
                allocation=None,
                verdicts=tuple(verdicts),
                unavailable_reason=(
                    "no passport has passed the evidence gates: "
                    "capital is only allocated to gate-passing strategies"
                ),
                correlation_sensitivity=self._correlation_sensitivity,
            )

        allocation = allocate_correlation_damped(
            scores,
            matrix,
            correlation_sensitivity=self._correlation_sensitivity,
        )
        return CapitalAllocationPlan(
            allocation=allocation,
            verdicts=tuple(verdicts),
            correlation_sensitivity=self._correlation_sensitivity,
        )

    def rebalance(
        self,
        current: Mapping[str, float],
        plan: CapitalAllocationPlan,
    ) -> tuple[AllocationDelta, ...]:
        """Turn a plan into the exact per-strategy rebalancing deltas.

        ``current`` is the operator's current weight per passport id. The
        target comes from the plan: the allocation weights for eligible ids,
        zero for excluded ones — excluded strategies must exit (the gate
        rule is hard, not negotiable). Deltas are computed over the union of
        ids in ``current`` and the plan.
        """
        target = {
            weight.strategy_id: weight.weight
            for weight in (plan.allocation.weights if plan.allocation is not None else ())
        }
        reasons = {verdict.passport_id: verdict.reason for verdict in plan.verdicts}
        ids = sorted(set(current) | set(target))
        deltas: list[AllocationDelta] = []
        for passport_id in ids:
            current_weight = current.get(passport_id, 0.0)
            target_weight = target.get(passport_id, 0.0)
            reason = reasons.get(
                passport_id,
                "excluded: not in the capital plan (no gate-passing evidence)",
            )
            deltas.append(
                AllocationDelta(
                    passport_id=passport_id,
                    current_weight=current_weight,
                    target_weight=target_weight,
                    delta=target_weight - current_weight,
                    reason=reason,
                )
            )
        return tuple(deltas)

    # -- helpers ------------------------------------------------------------

    def _verdict(self, member: PopulationMember) -> tuple[AllocationVerdict, float | None]:
        """Decide one passport's capital eligibility (the evidence gate)."""
        passport_id = member.passport_id
        if member.status is PassportStatus.RETIRED:
            return (AllocationVerdict(passport_id, False, _GATE_DEAD, None), None)
        if member.n_folds <= 0:
            return (AllocationVerdict(passport_id, False, _GATE_UNEVALUATED, None), None)
        if member.verdict is EvidenceVerdict.REJECT:
            return (AllocationVerdict(passport_id, False, _GATE_REJECTED, None), None)
        if member.verdict is EvidenceVerdict.OBSERVE:
            return (AllocationVerdict(passport_id, False, _GATE_OBSERVE, None), None)
        if member.mean_excess_return_pct is None:
            return (AllocationVerdict(passport_id, False, _GATE_NO_SCORE, None), None)
        if member.mean_excess_return_pct < self._min_score:
            return (AllocationVerdict(passport_id, False, _GATE_BELOW_FLOOR, None), None)
        return (
            AllocationVerdict(passport_id, True, _ELIGIBLE_REASON, member.mean_excess_return_pct),
            member.mean_excess_return_pct,
        )


__all__ = ["CapitalAllocationService"]
