# backend/application/research/cost_sweep.py
"""Cost-model sensitivity sweep (task T1-9-1, evidence priority 1).

The critique's Tier-1 #9 demands proof that an evidence verdict is not an
artifact of the assumed cost ruler: if a strategy only "works" at one
specific spread/fee/impact assumption, promoting it would be self-deception.
This sweep perturbs the shared cost model around its baseline and re-runs the
real out-of-sample evaluation under every perturbation, answering one
question: *does the promote-vs-not verdict survive a +/-50% perturbation of
spread, taker fee and participation impact?*

What is perturbed
-----------------
- ``half_spread_factor`` scales the half-spread charged to every fill
  (pipeline and baselines share the ruler, mirroring ``EvaluationCosts``).
- ``taker_fee_factor`` scales the taker fee charged to every fill.
- ``impact_factor`` scales only the pipeline's participation-impact add-on
  (``PaperFeeConfig.impact_bps``), decoupled from the spread, so a verdict
  that depends on participation cost alone is exposed as such.

How selection bias is handled
-----------------------------
The PBO family (P5-001) answers a different question than the sweep, and
re-fitting it seven times would make the sweep's runtime explode without
adding information: the variant family was selected under the baseline cost.
When ``variant_factories`` are provided, PBO is therefore computed **once on
the baseline cost** and reused by every scenario's verdict; the report states
this explicitly (``pbo_applied``). Verdicts without a PBO family skip the PBO
gate and are correspondingly weaker (documented, never silent).

Nothing here touches the live path: this is research instrumentation over the
already-gated evaluator.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.decision_pipeline_evaluator import (
    DecisionPipelineEvaluator,
    OutOfSampleReport,
    ReasonerFactory,
)
from backend.application.simulation.paper_fill_engine import PaperFeeConfig
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.observation.event import ObservationEvent
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import EvidenceVerdict, PassportVerdict
from backend.domain.research.pbo import PboResult


@dataclass(frozen=True, slots=True)
class CostScenario:
    """One cost perturbation, named for the audit trail.

    Every factor defaults to 1.0 (the baseline); a scenario perturbs one or
    more of the three cost dimensions. Factors must be strictly positive —
    a zero-cost scenario would silently disable the gate being tested.
    """

    name: str
    half_spread_factor: float = 1.0
    taker_fee_factor: float = 1.0
    impact_factor: float = 1.0

    def __post_init__(self) -> None:
        for factor in (self.half_spread_factor, self.taker_fee_factor, self.impact_factor):
            if not isinstance(factor, (int, float)) or factor <= 0.0:
                raise ValueError(f"cost factors must be strictly positive, got {factor!r}")

    def as_dict(self) -> dict[str, Any]:
        """Serialise the scenario definition to a plain dictionary."""
        return {
            "name": self.name,
            "half_spread_factor": self.half_spread_factor,
            "taker_fee_factor": self.taker_fee_factor,
            "impact_factor": self.impact_factor,
        }


def default_scenarios() -> tuple[CostScenario, ...]:
    """The standard +/-50% perturbation family (baseline plus six extremes)."""
    return (
        CostScenario("baseline"),
        CostScenario("spread_0.5x", half_spread_factor=0.5),
        CostScenario("spread_1.5x", half_spread_factor=1.5),
        CostScenario("fee_0.5x", taker_fee_factor=0.5),
        CostScenario("fee_1.5x", taker_fee_factor=1.5),
        CostScenario("impact_0.5x", impact_factor=0.5),
        CostScenario("impact_1.5x", impact_factor=1.5),
    )


@dataclass(frozen=True, slots=True)
class CostScenarioResult:
    """One scenario's full out-of-sample evidence and its verdict."""

    scenario: CostScenario
    report: OutOfSampleReport
    verdict: PassportVerdict

    @property
    def promotable(self) -> bool:
        """Whether the scenario's verdict is PROMOTE_TO_PAPER."""
        return self.verdict.verdict is EvidenceVerdict.PROMOTE_TO_PAPER

    def as_dict(self) -> dict[str, Any]:
        """Serialise the scenario result to a plain dictionary."""
        return {
            "scenario": self.scenario.as_dict(),
            "pooled": self.report.pooled.as_dict(),
            "verdict": self.verdict.as_dict(),
            "promotable": self.promotable,
        }


@dataclass(frozen=True, slots=True)
class CostSweepReport:
    """The complete sensitivity sweep: baseline plus every perturbation.

    Attributes
    ----------
    symbol: str
        Symbol evaluated.
    cv_spec: dict[str, object]
        The walk-forward configuration shared by every scenario.
    baseline: CostScenarioResult
        The unperturbed scenario (reference for the stability claim).
    scenarios: tuple[CostScenarioResult, ...]
        All scenario results including the baseline, in scenario order.
    pbo: PboResult | None
        The PBO family computed once on the baseline cost, if a variant
        family was provided.
    pbo_applied: bool
        Whether the PBO gate was applied to every scenario's verdict.
    verdict_stable: bool
        Whether every scenario agrees with the baseline on promote-vs-not.
        This is the sweep's headline answer (T1-9-1).
    """

    symbol: str
    cv_spec: dict[str, object]
    baseline: CostScenarioResult
    scenarios: tuple[CostScenarioResult, ...]
    pbo: PboResult | None
    pbo_applied: bool
    verdict_stable: bool

    def as_dict(self) -> dict[str, Any]:
        """Serialise the sweep report to a plain dictionary."""
        return {
            "symbol": self.symbol,
            "cv_spec": dict(self.cv_spec),
            "baseline": self.baseline.as_dict(),
            "scenarios": [s.as_dict() for s in self.scenarios],
            "pbo": self.pbo.as_dict() if self.pbo is not None else None,
            "pbo_applied": self.pbo_applied,
            "verdict_stable": self.verdict_stable,
        }


class CostSweep:
    """Re-run the OOS evaluator under perturbed cost rulers.

    Parameters
    ----------
    costs: EvaluationCosts | None
        Baseline cost ruler (defaults to ``EvaluationCosts.realistic()``).
    cv: WalkForwardCV | None
        Walk-forward splitter shared by every scenario (defaults to the
        evaluator's expanding 100-train / 20-test window).
    starting_equity: float
        Fresh equity per fold per scenario.
    reasoner_factory: ReasonerFactory | None
        Reasoner factory shared by every scenario (defaults to the
        deterministic ``RuleBasedSolver``).
    n_trials: int
        Multiple-testing deflation for the Deflated Sharpe (P5-001).
    variant_factories: Mapping[str, ReasonerFactory] | None
        When provided, the PBO family is computed once on the baseline cost
        and every scenario's verdict applies the PBO gate (``pbo_applied``).
        When omitted, verdicts skip the PBO gate (documented in the report).
    max_pbo: float
        PBO gate threshold (default 0.5, matching ``EvidenceEngine``).
    """

    def __init__(
        self,
        *,
        costs: EvaluationCosts | None = None,
        cv: WalkForwardCV | None = None,
        starting_equity: float = 100_000.0,
        reasoner_factory: ReasonerFactory | None = None,
        n_trials: int = 1,
        variant_factories: Mapping[str, ReasonerFactory] | None = None,
        max_pbo: float = 0.5,
    ) -> None:
        self._costs = costs or EvaluationCosts.realistic()
        self._cv = cv
        self._starting_equity = starting_equity
        self._reasoner_factory = reasoner_factory
        self._n_trials = n_trials
        self._variant_factories = dict(variant_factories) if variant_factories is not None else None
        self._max_pbo = max_pbo

    def sweep(
        self,
        events: Sequence[ObservationEvent],
        *,
        scenarios: Sequence[CostScenario] | None = None,
    ) -> CostSweepReport:
        """Evaluate ``events`` under every scenario and aggregate the verdicts."""
        family = tuple(scenarios) if scenarios is not None else default_scenarios()
        if not family:
            raise ValueError("at least one cost scenario is required")

        baseline = self._evaluate(events, family[0], pbo=None)
        pbo, pbo_applied = self._baseline_pbo(events, family[0])

        results = tuple(
            self._evaluate(events, scenario, pbo=pbo if pbo_applied else None)
            for scenario in family
        )
        return CostSweepReport(
            symbol=baseline.report.symbol,
            cv_spec=baseline.report.cv_spec,
            baseline=baseline,
            scenarios=results,
            pbo=pbo,
            pbo_applied=pbo_applied,
            verdict_stable=all(result.promotable == baseline.promotable for result in results),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        events: Sequence[ObservationEvent],
        scenario: CostScenario,
        *,
        pbo: PboResult | None,
    ) -> CostScenarioResult:
        """Run the real OOS evaluation under one scenario's cost ruler."""
        costs = EvaluationCosts(
            half_spread_pct=self._costs.half_spread_pct * scenario.half_spread_factor,
            taker_fee_pct=self._costs.taker_fee_pct * scenario.taker_fee_factor,
        )
        pipeline_fee_config = PaperFeeConfig(
            taker_fee_rate=costs.taker_fee_pct,
            impact_bps=costs.half_spread_pct * 10_000.0 * scenario.impact_factor,
        )
        evaluator = DecisionPipelineEvaluator(
            costs=costs,
            cv=self._cv,
            starting_equity=self._starting_equity,
            reasoner_factory=self._reasoner_factory,
            pipeline_fee_config=pipeline_fee_config,
            n_trials=self._n_trials,
        )
        report = evaluator.evaluate(events)
        verdict = _verdict_for_pooled(report.pooled, pbo=pbo, max_pbo=self._max_pbo)
        return CostScenarioResult(scenario=scenario, report=report, verdict=verdict)

    def _baseline_pbo(
        self,
        events: Sequence[ObservationEvent],
        baseline: CostScenario,
    ) -> tuple[PboResult | None, bool]:
        """Compute the PBO family once on the baseline cost.

        Returns (None, False) when no variant family was configured: the
        sweep then skips the PBO gate on every scenario verdict (recorded in
        the report so the limitation is never silent).
        """
        if self._variant_factories is None:
            return None, False
        if len(self._variant_factories) < 2:
            raise ValueError("at least two reasoner variants are required for PBO")
        evaluator = DecisionPipelineEvaluator(
            costs=EvaluationCosts(
                half_spread_pct=self._costs.half_spread_pct * baseline.half_spread_factor,
                taker_fee_pct=self._costs.taker_fee_pct * baseline.taker_fee_factor,
            ),
            cv=self._cv,
            starting_equity=self._starting_equity,
            reasoner_factory=self._reasoner_factory,
            pipeline_fee_config=PaperFeeConfig(
                taker_fee_rate=self._costs.taker_fee_pct * baseline.taker_fee_factor,
                impact_bps=self._costs.half_spread_pct * 10_000.0 * baseline.impact_factor,
            ),
            n_trials=self._n_trials,
        )
        variants = evaluator.evaluate_variants(events, self._variant_factories)
        return variants.pbo, True


def _verdict_for_pooled(
    pooled: PooledEvidence,
    *,
    pbo: PboResult | None,
    max_pbo: float,
) -> PassportVerdict:
    """Apply the same conservative gates the evidence engine uses (P5-003)."""
    from backend.domain.research.passport import verdict_for_evidence

    return verdict_for_evidence(pooled, pbo=pbo, max_pbo=max_pbo)
