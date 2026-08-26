# backend/domain/research/scenario.py
"""Scenario-evaluation contracts (task P3-001).

A scenario is one possible market outcome over a decision horizon. The engine
that produces these objects is in
``backend.application.research.scenario_engine``; this module owns the value
objects and the vocabulary the decision layer reads.

Design principles
-----------------
- A :class:`ScenarioSet` is a *calibrated* probabilistic view of the horizon:
  probabilities sum to one and each scenario's ``return_pct`` is that
  bucket's expected market move, not an arbitrary label.
- An :class:`ScenarioEvaluation` always separates the gross expected move from
  the execution costs the proposed action would pay. A decision is a trade
  only when the *net* expectation after a full round trip of costs is
  positive; otherwise the engine abstains. Abstention is an explicit,
  enumerated decision, never an afterthought.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class ScenarioAction(Enum):
    """A candidate trading posture to evaluate against the scenario set."""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class ScenarioDecision(Enum):
    """What to do with the evaluated action.

    ``TRADE_*`` means the net expected value is positive after a full round
    trip of costs. ``ABSTAIN`` means trading it is not worth its costs (or the
    action holds no exposure at all).
    """

    TRADE_LONG = "trade_long"
    TRADE_SHORT = "trade_short"
    ABSTAIN = "abstain"


@dataclass(frozen=True, slots=True)
class Scenario:
    """One possible market outcome over the decision horizon.

    Attributes
    ----------
    label: str
        Human-readable bucket name (e.g. ``"strong_up"``).
    return_pct: float
        The market move this scenario expects, as a percentage. Signed:
        positive is an up move.
    probability: float
        Calibrated probability of this scenario occurring, in (0, 1]. The
        probabilities of a set always sum to 1.
    """

    label: str
    return_pct: float
    probability: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "return_pct": self.return_pct,
            "probability": self.probability,
        }


@dataclass(frozen=True, slots=True)
class ScenarioSet:
    """A calibrated set of horizon scenarios.

    Attributes
    ----------
    horizon: int
        Decision horizon in bars.
    expected_move_pct: float
        The drift the probabilities were centred on (the mean return the
        set is calibrated to).
    scenarios: tuple[Scenario, ...]
        Ordered scenarios. Every probability is in (0, 1] and the total is 1.
    """

    horizon: int
    expected_move_pct: float
    scenarios: tuple[Scenario, ...]

    @property
    def total_probability(self) -> float:
        """Sum of the scenario probabilities (validated to be 1.0)."""
        return sum(s.probability for s in self.scenarios)

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "expected_move_pct": self.expected_move_pct,
            "scenarios": [s.as_dict() for s in self.scenarios],
        }


@dataclass(frozen=True, slots=True)
class ScenarioEvaluation:
    """The result of evaluating one action against one scenario set.

    Attributes
    ----------
    action: ScenarioAction
        The posture that was evaluated.
    scenarios: ScenarioSet
        The scenario set the expectation was computed over (fully
        reproducible).
    decision: ScenarioDecision
        ``TRADE_*`` when the *net* expectation clears costs, else ``ABSTAIN``.
    gross_expected_value_pct: float
        Probability-weighted expected move of ``action``, before costs.
    round_trip_cost_pct: float
        Full entry+exit cost the action would pay (0 for ``FLAT``).
    expected_value_pct: float
        ``gross_expected_value_pct - round_trip_cost_pct``. The number a
        decision is made on.
    per_scenario_gross_pct: tuple[float, ...]
        Signed gross contribution of each scenario (already multiplied by its
        probability).
    per_scenario_net_pct: tuple[float, ...]
        ``per_scenario_gross_pct`` minus the round-trip cost.
    """

    action: ScenarioAction
    scenarios: ScenarioSet
    decision: ScenarioDecision
    gross_expected_value_pct: float
    round_trip_cost_pct: float
    expected_value_pct: float
    per_scenario_gross_pct: tuple[float, ...]
    per_scenario_net_pct: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "decision": self.decision.value,
            "scenarios": self.scenarios.as_dict(),
            "gross_expected_value_pct": self.gross_expected_value_pct,
            "round_trip_cost_pct": self.round_trip_cost_pct,
            "expected_value_pct": self.expected_value_pct,
            "per_scenario_gross_pct": list(self.per_scenario_gross_pct),
            "per_scenario_net_pct": list(self.per_scenario_net_pct),
        }
