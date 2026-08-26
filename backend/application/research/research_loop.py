# backend/application/research/research_loop.py
"""Autonomous research loop (task P4-002).

The loop is the agent's experiment cycle: generate structured hypotheses,
run experiments against the research factory, weigh the evidence, and hand
promising candidates to the controlled-promotion pipeline (P4-001) as
*evidence only*.

Design rules
------------
- **Hypotheses are structured and comparable.** The rule-based
  :class:`HypothesisGenerator` is deterministic and seeded; an AI source can
  be injected through the same interface and is judged by the same evidence
  rules.
- **Novelty is enforced.** A hypothesis whose claim was already studied in
  the registry is rejected as a duplicate, so the loop spends its budget on
  new questions (a "seed hypothesis" from elsewhere is new here).
- **Verdicts are honest.** PROMISING demands at least one passed experiment
  clearing the sharpe floor with non-negative net improvement; REFUTED means
  every experiment failed the bar; otherwise INCONCLUSIVE — a hypothesis is
  never called promising on weak evidence.
- **The loop cannot deploy itself.** The cycle emits insights and promotion
  evidence; running a live canary or entering production is entirely up to
  :class:`~backend.application.research.promotion_engine.PromotionEngine`
  and its gates. There is no code path here toward live execution.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.application.interfaces.experiment_store import ExperimentStore
from backend.domain.research.experiment import ExperimentStatus
from backend.domain.research.hypothesis import (
    CandidateInsight,
    CycleReport,
    EvidenceSummary,
    EvidenceVerdict,
    ExperimentOutcome,
    Hypothesis,
    HypothesisSource,
)

logger = logging.getLogger(__name__)

# Each hypothesis source produces experiments via a runner; the registry's
# novelty filter rejects already-studied claims.
ExperimentRunner = Callable[[Hypothesis], ExperimentOutcome]


@dataclass(frozen=True, slots=True)
class ResearchLoopConfig:
    """Budget and evidence bars for one research cycle."""

    experiments_per_hypothesis: int = 1
    max_hypotheses_per_cycle: int = 4
    promising_sharpe_min: float = 0.5
    promising_improvement_bps_min: float = 0.0
    seed: int = 0


class HypothesisGenerator:
    """Deterministic, seeded generator of rule-based hypotheses.

    ``families`` is a catalog of (claim, mechanism, feature_plan); each cycle
    samples from it in a fixed order so tests are reproducible. AI-sourced
    hypotheses (``source="ai"``) can be passed via :meth:`generate` with
    ``override_source``, or wired by the composition root by feeding the loop
    an external generator.
    """

    _DEFAULT_FAMILIES: tuple[tuple[str, str, tuple[str, ...] | None], ...] = (
        (
            "momentum continuation in trending regimes persists over one horizon",
            "regime-conditional momentum: trend persistence filters flat entry/exit noise",
            ("closed_price", "regime_trend", "momentum_ratio"),
        ),
        (
            "mean reversion accelerates after volatility spikes",
            "volatility-spike overshoot triggers reversal toward the anchored mean",
            ("volatility", "book_imbalance", "mean_reversion_signal"),
        ),
        (
            "liquidity imbalance at the top of book predicts short-horizon direction",
            "order-flow imbalance shifts the marginal price before the consolidated tape",
            ("book_imbalance", "order_flow", "micro_price"),
        ),
    )

    def __init__(
        self,
        seed: int = 0,
        families: Sequence[tuple[str, str, tuple[str, ...] | None]] | None = None,
    ) -> None:
        self._seed = seed
        self._families = tuple(families) if families is not None else self._DEFAULT_FAMILIES
        self._cursor = 0

    def generate(
        self, count: int, *, source: HypothesisSource = HypothesisSource.RULE
    ) -> tuple[Hypothesis, ...]:
        """Return ``count`` hypotheses in deterministic catalog order.

        ``count`` zero or negative yields an empty tuple. The generator cycles
        the catalog, so repeated calls stay reproducible.
        """
        if count <= 0:
            return ()
        hypotheses: list[Hypothesis] = []
        for _ in range(count):
            claim, mechanism, feature_plan = self._families[self._cursor % len(self._families)]
            self._cursor += 1
            hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"hyp-{self._seed}-{self._cursor}",
                    claim=claim,
                    mechanism=mechanism,
                    feature_plan=feature_plan,
                    source=source,
                )
            )
        return tuple(hypotheses)


def _summarize(
    hypothesis_id: str,
    outcomes: Sequence[ExperimentOutcome],
    *,
    sharpe_floor: float,
    improvement_floor: float,
) -> EvidenceSummary:
    """Aggregate experiment outcomes into a strict, honest verdict.

    PROMISING needs at least one passed experiment clearing both bars.
    REFUTED needs every *completed* experiment to fail both bars (uniformly
    against). Failed runs alone, or split evidence where experiments clear
    one bar but not the other, is INCONCLUSIVE — never promising.
    """
    completed = [o for o in outcomes if o.ok]
    passed = [
        o for o in completed if o.sharpe >= sharpe_floor and o.improvement_bps >= improvement_floor
    ]
    if passed:
        verdict = EvidenceVerdict.PROMISING
    elif completed and all(
        o.sharpe < sharpe_floor and o.improvement_bps < improvement_floor for o in completed
    ):
        verdict = EvidenceVerdict.REFUTED
    else:
        verdict = EvidenceVerdict.INCONCLUSIVE

    best_overall: ExperimentOutcome | None = None
    for outcome in completed:
        if best_overall is None or outcome.improvement_bps > best_overall.improvement_bps:
            best_overall = outcome
    samples = sum(o.samples for o in completed)
    return EvidenceSummary(
        hypothesis_id=hypothesis_id,
        verdict=verdict,
        best_experiment_id=best_overall.experiment_id if best_overall else None,
        best_improvement_bps=best_overall.improvement_bps if best_overall else 0.0,
        best_sharpe=best_overall.sharpe if best_overall else 0.0,
        samples=samples,
        experiment_count=len(outcomes),
    )


class ResearchLoop:
    """Runs one agent research cycle: generate, test, weigh, hand off."""

    def __init__(
        self,
        registry: ExperimentStore,
        runner: ExperimentRunner,
        config: ResearchLoopConfig | None = None,
        generator: HypothesisGenerator | None = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._config = config or ResearchLoopConfig()
        self._generator = generator or HypothesisGenerator(seed=self._config.seed)

    def run_cycle(
        self,
        count: int | None = None,
        *,
        hypotheses: Sequence[Hypothesis] | None = None,
    ) -> CycleReport:
        """Generate (or accept) hypotheses, run them, weigh evidence, report.

        Returns
        -------
        CycleReport
            Winning insights, rejected/duplicate hypothesis ids, and preserved
            failed outcomes. The loop never promotes; it only surfaces evidence.
        """
        budget = count if count is not None else self._config.max_hypotheses_per_cycle
        candidates = tuple(hypotheses) if hypotheses else self._generator.generate(budget)

        insights: list[CandidateInsight] = []
        rejected: list[str] = []
        failed: list[ExperimentOutcome] = []

        for hypothesis in candidates:
            if self._studied(hypothesis):
                rejected.append(hypothesis.hypothesis_id)
                logger.info(
                    "Research loop: %s already studied; discarded", hypothesis.hypothesis_id
                )
                continue

            outcomes: list[ExperimentOutcome] = []
            for _ in range(self._config.experiments_per_hypothesis):
                outcome = self._runner(hypothesis)
                outcomes.append(outcome)
                if not outcome.ok:
                    failed.append(outcome)
                    logger.warning(
                        "Research loop: experiment %s failed: %s",
                        outcome.experiment_id,
                        outcome.failure_reason,
                    )

            if not outcomes:
                continue
            summary = _summarize(
                hypothesis.hypothesis_id,
                outcomes,
                sharpe_floor=self._config.promising_sharpe_min,
                improvement_floor=self._config.promising_improvement_bps_min,
            )
            if summary.verdict is EvidenceVerdict.PROMISING:
                insights.append(CandidateInsight(hypothesis=hypothesis, evidence=summary))

        return CycleReport(
            insights=tuple(insights),
            rejected=tuple(rejected),
            failed=tuple(failed),
        )

    def _studied(self, hypothesis: Hypothesis) -> bool:
        """Whether the registry already contains this hypothesis's claim."""
        claimed = hypothesis.claim.strip().lower()
        for record in self._registry.list(status=ExperimentStatus.DONE):
            existing = str(record.hypothesis).strip().lower()
            if existing == claimed:
                return True
            # Shared prefix is a softer duplicate signal.
            min_len = min(len(claimed), len(existing))
            if min_len >= 24 and claimed[:min_len] == existing[:min_len]:
                return True
        return False


def run_research_cycle(
    registry: ExperimentStore,
    runner: ExperimentRunner,
    count: int | None = None,
    *,
    config: ResearchLoopConfig | None = None,
) -> CycleReport:
    """Module-level convenience: run one research cycle end to end."""
    return ResearchLoop(registry, runner, config=config).run_cycle(count=count)


def generate_hypotheses(count: int, *, seed: int = 0) -> tuple[Hypothesis, ...]:
    """Module-level convenience: generate ``count`` deterministic hypotheses."""
    return HypothesisGenerator(seed=seed).generate(count)
