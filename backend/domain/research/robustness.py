# backend/domain/research/robustness.py
"""Robustness and multiple-testing contracts (task P1-008).

A headline backtest number is the best of however many tries a researcher
made, and "best of N" is inflated by selection — that is the multiple-testing
problem this module makes explicit instead of hiding. Three ideas:

- **Parameter perturbation.** The same trade idea run with nearby parameter
  choices must not collapse. A robust edge survives modest perturbations; a
  tuned one does not, and the report shows exactly how many variants kept
  their sign.
- **Expense/slippage stress.** Every result is scored under a ladder of cost
  and slippage assumptions. A claim that evaporates at twice-likely costs is
  a claim about the cost model, not about the market.
- **Selection-bias reporting.** Every report carries the number of attempts
  that produced the champion and an explicit correction: the best of N is
  compared against the expected best of N under a null with the observed
  dispersion, so the inflated part of the headline is stated rather than
  silently believed.

All values are plain immutable dataclasses the experiment registry (P1-005)
can persist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from backend.domain.research.evaluation import BaselineResult


@dataclass(frozen=True, slots=True)
class PerturbationOutcome:
    """One strategy variant scored on the same shared price/cost world."""

    label: str
    result: BaselineResult
    excess_return_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "result": self.result.as_dict(),
            "excess_return_pct": self.excess_return_pct,
        }


@dataclass(frozen=True, slots=True)
class PerturbationReport:
    """Outcome of perturbing one trade idea across parameter choices.

    Attributes
    ----------
    champion_label: str
        The variant with the highest excess return (the number a researcher
        would otherwise headline).
    champion_excess_pct: float
        The champion's excess return.
    variant_count: int
        Number of parameter variants scored (the multiple-testing N).
    outcomes: tuple[PerturbationOutcome, ...]
        Every variant, for inspection and persistence.
    positive_variants: int
        Number of variants with positive excess return.
    positive_fraction: float
        ``positive_variants / variant_count`` in [0, 1].
    robust: bool
        Whether a clear majority of perturbations kept a positive excess
        (``positive_fraction >= 0.6``). The flag is deliberately uneven: an
        edge must survive most nearby parameters, not half.
    """

    champion_label: str
    champion_excess_pct: float
    variant_count: int
    outcomes: tuple[PerturbationOutcome, ...]
    positive_variants: int
    positive_fraction: float
    robust: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "champion_label": self.champion_label,
            "champion_excess_pct": self.champion_excess_pct,
            "variant_count": self.variant_count,
            "outcomes": [o.as_dict() for o in self.outcomes],
            "positive_variants": self.positive_variants,
            "positive_fraction": self.positive_fraction,
            "robust": self.robust,
        }


@dataclass(frozen=True, slots=True)
class ExpenseStressPoint:
    """A strategy's costed result at one multiplier of a cost axis."""

    multiplier: float
    excess_return_pct: float
    total_return_pct: float
    transaction_cost_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "multiplier": self.multiplier,
            "excess_return_pct": self.excess_return_pct,
            "total_return_pct": self.total_return_pct,
            "transaction_cost_pct": self.transaction_cost_pct,
        }


@dataclass(frozen=True, slots=True)
class ExpenseStressReport:
    """A strategy's edge under escalating cost and slippage assumptions.

    ``cost_axis`` scales both half-spread and taker fee together; ``slippage_axis``
    scales only half-spread (the liquidity-sensitive part), letting a researcher
    separate "fees are doing this" from "liquidity is doing this".

    Attributes
    ----------
    strategy_name: str
        The strategy being stressed.
    cost_axis: tuple[ExpenseStressPoint, ...]
        Excess at 1x, 2x, ... realistic costs.
    slippage_axis: tuple[ExpenseStressPoint, ...]
        Excess with half-spread only scaled up.
    survives_2x_cost: bool
        Excess stays positive at 2x realistic cost.
    survives_2x_slippage: bool
        Excess stays positive at 2x realistic half-spread.
    """

    strategy_name: str
    cost_axis: tuple[ExpenseStressPoint, ...] = field(default_factory=tuple)
    slippage_axis: tuple[ExpenseStressPoint, ...] = field(default_factory=tuple)
    survives_2x_cost: bool = False
    survives_2x_slippage: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "cost_axis": [p.as_dict() for p in self.cost_axis],
            "slippage_axis": [p.as_dict() for p in self.slippage_axis],
            "survives_2x_cost": self.survives_2x_cost,
            "survives_2x_slippage": self.survives_2x_slippage,
        }


@dataclass(frozen=True, slots=True)
class SelectionBiasReport:
    """Whether the champion's headline remains after accounting for N tries.

    Under a null model with the observed dispersion, picking the best of N
    variants would produce ``expected_best_null_pct`` by chance alone. The
    difference between that and the champion is how much of the headline is
    selection, not signal. ``adjusted_excess_pct`` subtracts it.

    Attributes
    ----------
    n_experiments: int
        Number of attempts (variants/evaluations) that produced the champion.
    champion_excess_pct: float
        The observed best excess return.
    mean_excess_pct: float
        Mean excess across the N attempts.
    std_excess_pct: float
        Standard deviation across the N attempts.
    expected_best_null_pct: float
        Expected best-of-N excess under a normal null with that mean/std.
    selection_inflation_pct: float
        ``expected_best_null_pct - mean_excess_pct``; the portion of the
        champion attributable to having tried N things.
    adjusted_excess_pct: float
        ``champion_excess_pct - selection_inflation_pct``; the conservative
        remaining edge after the multiple-testing correction.
    survives: bool
        Whether ``adjusted_excess_pct > 0``.
    """

    n_experiments: int
    champion_excess_pct: float
    mean_excess_pct: float
    std_excess_pct: float
    expected_best_null_pct: float
    selection_inflation_pct: float
    adjusted_excess_pct: float
    survives: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_experiments": self.n_experiments,
            "champion_excess_pct": self.champion_excess_pct,
            "mean_excess_pct": self.mean_excess_pct,
            "std_excess_pct": self.std_excess_pct,
            "expected_best_null_pct": self.expected_best_null_pct,
            "selection_inflation_pct": self.selection_inflation_pct,
            "adjusted_excess_pct": self.adjusted_excess_pct,
            "survives": self.survives,
        }
