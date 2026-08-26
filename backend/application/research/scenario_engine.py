# backend/application/research/scenario_engine.py
"""Scenario engine (task P3-001).

The scenario engine turns a volatility estimate into a small, *calibrated* set
of possible horizon outcomes and prices a proposed action against them,
net of the exact transaction-cost ruler the baselines (P1-003) pay.

Design rules
------------
- **Multiple scenarios, calibrated probabilities.** The horizon return is
  modelled as a normal bucket model. ``n_points`` quantile buckets are cut
  from the horizon standard deviation ``vol_pct/100 * sqrt(horizon)``; each
  scenario's ``return_pct`` is the *conditional mean* of its bucket and its
  ``probability`` the analytic bucket mass, so the set is calibrated by
  construction: probabilities sum to one and empirical frequencies match them
  (verified by :meth:`ScenarioEngine.calibration_report`, seeded and
  deterministic).
- **Expected value net of costs.** A candidate long/short pays a full round
  trip of spread + taker fee on notional. The decision is made on ``EV - cost``;
  the position is taken only when that net expectation is positive.
- **Abstention is explicit.** If no candidate's net expectation clears costs,
  the engine returns an ``ABSTAIN`` decision rather than a forced trade.
  ``FLAT`` is a zero-cost, always-abstain action used as the inaction ruler.

Both the generator and the evaluator are pure functions of their inputs: the
same (vol, horizon, drift, costs, n_points) always yields the same scenario
set and the same decision.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any

import numpy as np

from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.domain.research.scenario import (
    Scenario,
    ScenarioAction,
    ScenarioDecision,
    ScenarioEvaluation,
    ScenarioSet,
)

logger = logging.getLogger(__name__)

_SQRT2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _normal_cdf(x: float) -> float:
    """Standard normal cumulative distribution function."""
    return 0.5 * (1.0 + math.erf(x / _SQRT2))


def _normal_pdf(x: float) -> float:
    """Standard normal probability density function."""
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _inverse_normal(p: float) -> float:
    """Acklam's inverse normal CDF; |error| < 1.15e-9 for p in (0, 1)."""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be strictly between 0 and 1")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (
        7.784695709041462e-03,
        3.224671290700398e-01,
        2.445134137142996e00,
        3.754408661907416e00,
    )
    p_low = 0.02425
    p_high = 1.0 - p_low
    if p < p_low:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p <= p_high:
        q = p - 0.5
        r = q * q
        return (
            (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
            * q
            / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0)
        )
    q = math.sqrt(-2.0 * math.log(1.0 - p))
    return -(
        (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5])
        / ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0)
    )


def _labels(n_points: int) -> list[str]:
    """Deterministic bucket labels; central bucket is always ``"flat"``."""
    if n_points == 5:
        return ["strong_down", "down", "flat", "up", "strong_up"]
    if n_points == 7:
        return ["strong_down", "down", "slight_down", "flat", "slight_up", "up", "strong_up"]
    return [f"bucket_{i + 1}" for i in range(n_points)]


def _validate_inputs(vol_pct: float, horizon: int, n_points: int, expected_move_pct: float) -> None:
    if vol_pct < 0.0 or not math.isfinite(vol_pct):
        raise ValueError("vol_pct must be a finite, non-negative percentage")
    if not isinstance(horizon, int) or horizon < 1:
        raise ValueError("horizon must be a positive integer number of bars")
    if not isinstance(n_points, int) or n_points < 3:
        raise ValueError("n_points must be an integer of at least 3")
    if not math.isfinite(expected_move_pct):
        raise ValueError("expected_move_pct must be finite")


def build_scenario_set(
    *,
    vol_pct: float,
    horizon: int,
    expected_move_pct: float = 0.0,
    n_points: int = 5,
) -> ScenarioSet:
    """Build a calibrated set of ``n_points`` horizon scenarios.

    Probabilities come from the analytic normal quantile buckets and each
    ``return_pct`` is the bucket's conditional mean (plus ``expected_move_pct``
    drift). The result is deterministic: no randomness is involved.
    """
    _validate_inputs(vol_pct, horizon, n_points, expected_move_pct)
    sigma = (vol_pct / 100.0) * math.sqrt(horizon)
    drift = expected_move_pct / 100.0

    interior = [_inverse_normal(k / n_points) * sigma for k in range(1, n_points)]
    edges: list[float] = [float("-inf"), *interior, float("inf")]
    probabilities = [
        _normal_cdf(edges[k] / max(sigma, 1e-300)) - _normal_cdf(edges[k - 1] / max(sigma, 1e-300))
        for k in range(1, n_points + 1)
    ]
    if sigma <= 0.0:
        returns = [drift for _ in range(n_points)]
    else:
        returns = [
            _conditional_mean(lo, hi, sigma) + drift
            for lo, hi in zip(edges, edges[1:], strict=False)
        ]

    scenarios = tuple(
        Scenario(label=_labels(n_points)[k], return_pct=round(r * 100.0, 8), probability=p)
        for k, (r, p) in enumerate(zip(returns, probabilities, strict=True))
    )
    return ScenarioSet(horizon=horizon, expected_move_pct=expected_move_pct, scenarios=scenarios)


def _conditional_mean(lo: float, hi: float, sigma: float) -> float:
    """Mean of the normal bucket ``(lo, hi)``.

    Returns the expected outcome in price units (``sigma`` is the horizon
    standard deviation already in decimal terms). Boundaries may be infinite.
    """
    if lo == float("-inf") and hi == float("inf"):
        return 0.0
    pdf_diff = (_normal_pdf(lo / sigma) if lo != float("-inf") else 0.0) - (
        _normal_pdf(hi / sigma) if hi != float("inf") else 0.0
    )
    mass = _normal_cdf(hi / sigma) - _normal_cdf(lo / sigma)
    return (pdf_diff / mass) * sigma


def round_trip_cost_pct(costs: EvaluationCosts) -> float:
    """Full entry+exit cost of one position as a percentage of notional."""
    return 2.0 * (costs.half_spread_pct + costs.taker_fee_pct) * 100.0


@dataclass(frozen=True, slots=True)
class CalibrationBucket:
    """Empirical-vs-model calibration evidence for one scenario bucket."""

    label: str
    model_probability: float
    empirical_frequency: float
    error: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "model_probability": self.model_probability,
            "empirical_frequency": self.empirical_frequency,
            "error": self.error,
        }


class ScenarioEngine:
    """Price an action (or the best of several) against calibrated scenarios."""

    def __init__(self, costs: EvaluationCosts | None = None) -> None:
        self._costs = costs or EvaluationCosts.realistic()

    @property
    def costs(self) -> EvaluationCosts:
        return self._costs

    def evaluate(
        self,
        *,
        action: ScenarioAction,
        vol_pct: float,
        horizon: int,
        expected_move_pct: float = 0.0,
        n_points: int = 5,
    ) -> ScenarioEvaluation:
        """Evaluate ``action`` against a calibrated scenario set.

        The decision is a trade only when the probability-weighted expectation
        minus a full round trip of costs is positive; otherwise it abstains.
        """
        scenario_set = build_scenario_set(
            vol_pct=vol_pct,
            horizon=horizon,
            expected_move_pct=expected_move_pct,
            n_points=n_points,
        )
        return self._evaluate_set(action, scenario_set)

    def evaluate_set(
        self, *, action: ScenarioAction, scenario_set: ScenarioSet
    ) -> ScenarioEvaluation:
        """Evaluate ``action`` against an already-built scenario set."""
        if scenario_set.horizon < 1:
            raise ValueError("scenario_set.horizon must be a positive integer")
        return self._evaluate_set(action, scenario_set)

    def _evaluate_set(
        self, action: ScenarioAction, scenario_set: ScenarioSet
    ) -> ScenarioEvaluation:
        multiplier = {"long": 1.0, "short": -1.0, "flat": 0.0}[action.value]
        gross = [
            scenario.return_pct * multiplier * scenario.probability
            for scenario in scenario_set.scenarios
        ]
        gross_ev = sum(gross)
        cost = 0.0 if action is ScenarioAction.FLAT else round_trip_cost_pct(self._costs)
        net_ev = gross_ev - cost
        if action is ScenarioAction.FLAT:
            decision = ScenarioDecision.ABSTAIN
        elif action is ScenarioAction.LONG:
            decision = ScenarioDecision.TRADE_LONG if net_ev > 0.0 else ScenarioDecision.ABSTAIN
        else:
            decision = ScenarioDecision.TRADE_SHORT if net_ev > 0.0 else ScenarioDecision.ABSTAIN
        return ScenarioEvaluation(
            action=action,
            scenarios=scenario_set,
            decision=decision,
            gross_expected_value_pct=round(gross_ev, 8),
            round_trip_cost_pct=round(cost, 8),
            expected_value_pct=round(net_ev, 8),
            per_scenario_gross_pct=tuple(round(g, 8) for g in gross),
            per_scenario_net_pct=tuple(round(g - cost, 8) for g in gross),
        )

    def best_action(
        self,
        *,
        vol_pct: float,
        horizon: int,
        expected_move_pct: float = 0.0,
        n_points: int = 5,
    ) -> ScenarioEvaluation:
        """Pick the net-positive direction with the best expectation.

        Both sides are priced under the same scenarios and costs; the highest
        net expectation wins. If neither clears costs the engine abstains.
        """
        long_eval = self.evaluate(
            action=ScenarioAction.LONG,
            vol_pct=vol_pct,
            horizon=horizon,
            expected_move_pct=expected_move_pct,
            n_points=n_points,
        )
        short_eval = self.evaluate(
            action=ScenarioAction.SHORT,
            vol_pct=vol_pct,
            horizon=horizon,
            expected_move_pct=expected_move_pct,
            n_points=n_points,
        )
        winner = (
            long_eval
            if long_eval.expected_value_pct >= short_eval.expected_value_pct
            else short_eval
        )
        if winner.expected_value_pct <= 0.0:
            return ScenarioEvaluation(
                action=winner.action,
                scenarios=winner.scenarios,
                decision=ScenarioDecision.ABSTAIN,
                gross_expected_value_pct=winner.gross_expected_value_pct,
                round_trip_cost_pct=winner.round_trip_cost_pct,
                expected_value_pct=winner.expected_value_pct,
                per_scenario_gross_pct=winner.per_scenario_gross_pct,
                per_scenario_net_pct=winner.per_scenario_net_pct,
            )
        return winner

    def calibration_report(
        self,
        *,
        vol_pct: float,
        horizon: int,
        n_points: int = 5,
        draws: int = 50_000,
        seed: int = 0,
    ) -> dict[str, Any]:
        """Empirically check that the generated probabilities are calibrated.

        Draws ``draws`` horizon returns from the same normal model and compares
        each scenario's empirical frequency to its model probability. Seeded
        and therefore deterministic. Returns a plain report dict.
        """
        if draws < 100:
            raise ValueError("draws must be at least 100")
        scenario_set = build_scenario_set(
            vol_pct=vol_pct, horizon=horizon, expected_move_pct=0.0, n_points=n_points
        )
        sigma = (vol_pct / 100.0) * math.sqrt(horizon)
        rng = np.random.default_rng(seed)
        sample = rng.normal(0.0, sigma, draws).tolist()
        edges = [_inverse_normal(k / n_points) for k in range(1, n_points)]
        counts = [0] * n_points
        for value in sample:
            bucket = 0
            while bucket < n_points - 1 and value >= edges[bucket] * sigma:
                bucket += 1
            counts[bucket] += 1
        frequencies = [c / draws for c in counts]
        errors = [
            f - s.probability for f, s in zip(frequencies, scenario_set.scenarios, strict=True)
        ]
        buckets = [
            CalibrationBucket(
                label=s.label,
                model_probability=round(s.probability, 8),
                empirical_frequency=round(f, 8),
                error=round(e, 8),
            )
            for s, f, e in zip(scenario_set.scenarios, frequencies, errors, strict=True)
        ]
        return {
            "horizon": horizon,
            "vol_pct": vol_pct,
            "n_scenarios": n_points,
            "draws": draws,
            "seed": seed,
            "max_abs_error": round(max(abs(e) for e in errors), 8),
            "brier_score": round(sum(e * e for e in errors), 8),
            "buckets": [b.as_dict() for b in buckets],
        }
