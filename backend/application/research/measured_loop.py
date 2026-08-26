# backend/application/research/measured_loop.py
"""Measured research feedback loop (task T3-30-1).

The loop that closes the research->evidence circuit: iteration after
iteration, a hypothesis is evaluated and, when a report comes back, lands
as a passport update on the immutable ledger (evidence engine, P5-003c).
Loop quality is then measured the only way that is honest: **passport
survival** — how many of the loop's passports are still alive on the
ledger at measurement time, with the verdict mix underneath.

Design rules
------------
- **Iterations are recorded, misses included.** A hypothesis that produced
  no report, an evaluation that errored, or a passport the ledger refused
  is a record with ``passport_id=None`` and the reason. The loop never
  hides its failures behind a success count.
- **The ledger is the truth at measurement time.** Survival is read from
  the store when quality is computed, not cached at issue time: if the
  death system (T3-26/28) retired a loop passport, the loop's survival
  rate shows it. The loop is a producer; the ledger decides life and death.
- **Quality is the loop's own score.** Only passports this loop issued
  count (optionally the last ``window`` iterations) — the rest of the
  population is not the loop's credit.
- **Nothing issued -> no rate.** ``survival_rate`` is None with the reason.
- **No promotion happens here.** The loop issues passports; the verdict
  gates (``verdict_for_evidence``) decide what the passport is. Library-
  only: nothing in the live path imports this loop.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from backend.application.research.decision_pipeline_evaluator import (
    OutOfSampleReport,
)
from backend.application.research.evidence_engine import EvidenceEngine
from backend.domain.research.hypothesis import Hypothesis
from backend.domain.research.measured_loop import (
    LoopIterationRecord,
    LoopQuality,
    LoopReport,
)
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
)

# evaluate_fn(hypothesis) -> the OOS report, or None when the hypothesis is
# not evaluable (the iteration is then recorded as a miss, never fabricated).
EvaluateFn = Callable[[Hypothesis], OutOfSampleReport | None]


@dataclass(frozen=True, slots=True)
class MeasuredLoopConfig:
    """Context and bounds of one measured loop run.

    Attributes
    ----------
    run_id: str
        Identifies the run; passport ids derive from it
        (``STRAT-<run_id>-<iteration>``) so repeated runs cannot collide
        with the immutable ledger.
    dataset_id, dataset_version: str, int
        The frozen dataset the hypotheses are evaluated on.
    features: tuple[str, ...]
        Feature keys for the issued passports (a hypothesis carrying its
        own ``feature_plan`` wins over this default).
    model: str
        The reasoner/scorer name recorded on the passports.
    trial_count: int
        Multiple-testing count recorded on the passports (P5-001 input).
    experiment_id: str | None
        Lineage: the registry experiment the passports derive from.
    max_iterations: int
        Safety bound on iterations per run (prevents unbounded loops on
        wiring mistakes).
    survival_window: int | None
        The quality measure's window: only the last ``survival_window``
        iterations' passports count; None = the whole run.
    """

    run_id: str = "L1"
    dataset_id: str = "btcusdt"
    dataset_version: int = 1
    features: tuple[str, ...] = ()
    model: str = "RuleBasedSolver"
    trial_count: int = 50
    experiment_id: str | None = None
    max_iterations: int = 100
    survival_window: int | None = None


class MeasuredResearchLoop:
    """Run hypotheses through evaluation into passports and measure survival.

    Parameters
    ----------
    engine: EvidenceEngine
        The passport ledger seam (immutable issue, verdict gates).
    evaluate_fn: EvaluateFn
        The injected evaluation callable (over the real OOS evaluator or a
        fake): returns the report or None.
    config: MeasuredLoopConfig | None
        Run context and bounds.
    """

    def __init__(
        self,
        engine: EvidenceEngine,
        evaluate_fn: EvaluateFn,
        config: MeasuredLoopConfig | None = None,
    ) -> None:
        if config is not None and config.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        self._engine = engine
        self._evaluate = evaluate_fn
        self._config = config or MeasuredLoopConfig()
        self._issued: list[str] = []

    def run(self, hypotheses: Sequence[Hypothesis]) -> LoopReport:
        """Run the loop: evaluate each hypothesis, land passports, measure.

        Returns the full LoopReport (every iteration record plus the
        survival-based quality measure over this run's passports).
        """
        records: list[LoopIterationRecord] = []
        self._issued = []
        for iteration, hypothesis in enumerate(hypotheses[: self._config.max_iterations], start=1):
            record = self._iterate(iteration, hypothesis)
            records.append(record)
            if record.passport_id is not None:
                self._issued.append(record.passport_id)
        return LoopReport(records=tuple(records), quality=self.quality())

    def quality(self, window: int | None = None) -> LoopQuality:
        """Measure loop quality from the ledger's *current* state.

        Re-read every passport this loop issued (fresh from the store, so
        death-system retirements since issue count) and compute the
        survival rate and verdict mix. ``window`` limits the count to the
        last ``window`` issued passports; None = the whole run. When
        nothing was issued, the survival rate is None with the reason.
        """
        window = window if window is not None else self._config.survival_window
        ids = self._issued[-window:] if window is not None and window > 0 else self._issued
        passports = [p for p in (self._engine.passport(pid) for pid in ids) if p is not None]

        alive = sum(1 for p in passports if p.status is not PassportStatus.RETIRED)
        dead = len(passports) - alive
        promoted = sum(
            1 for p in passports if p.verdict.verdict is EvidenceVerdict.PROMOTE_TO_PAPER
        )
        observed = sum(1 for p in passports if p.verdict.verdict is EvidenceVerdict.OBSERVE)
        rejected = sum(1 for p in passports if p.verdict.verdict is EvidenceVerdict.REJECT)

        if not passports:
            return LoopQuality(
                iterations_run=len(self._issued),
                passports_issued=0,
                alive=0,
                dead=0,
                survival_rate=None,
                promoted=0,
                observed=0,
                rejected=0,
                window=window,
                unavailable_reason=(
                    "no passports issued by this loop run: a survival rate "
                    "over zero strategies would be fabricated"
                ),
            )
        return LoopQuality(
            iterations_run=len(self._issued),
            passports_issued=len(passports),
            alive=alive,
            dead=dead,
            survival_rate=alive / (alive + dead),
            promoted=promoted,
            observed=observed,
            rejected=rejected,
            window=window,
        )

    # -- helpers ------------------------------------------------------------

    def _iterate(self, iteration: int, hypothesis: Hypothesis) -> LoopIterationRecord:
        """One iteration: evaluate the hypothesis, land the passport."""
        try:
            report = self._evaluate(hypothesis)
        except Exception as exc:  # noqa: BLE001 - the loop must record, not crash
            return LoopIterationRecord(
                iteration=iteration,
                hypothesis_id=hypothesis.hypothesis_id,
                passport_id=None,
                verdict=None,
                status=None,
                reason=f"evaluation error: {exc}",
            )
        if report is None:
            return LoopIterationRecord(
                iteration=iteration,
                hypothesis_id=hypothesis.hypothesis_id,
                passport_id=None,
                verdict=None,
                status=None,
                reason="not evaluable: no report produced",
            )
        passport_id = f"STRAT-{self._config.run_id}-{iteration:03d}"
        try:
            passport = self._engine.issue_passport(
                passport_id=passport_id,
                hypothesis=hypothesis.claim,
                dataset_id=self._config.dataset_id,
                dataset_version=self._config.dataset_version,
                features=hypothesis.feature_plan or self._config.features,
                model=self._config.model,
                trial_count=self._config.trial_count,
                report=report,
                experiment_id=self._config.experiment_id,
            )
        except ValueError as exc:
            return LoopIterationRecord(
                iteration=iteration,
                hypothesis_id=hypothesis.hypothesis_id,
                passport_id=None,
                verdict=None,
                status=None,
                reason=f"passport refused: {exc}",
            )
        return LoopIterationRecord(
            iteration=iteration,
            hypothesis_id=hypothesis.hypothesis_id,
            passport_id=passport.passport_id,
            verdict=passport.verdict.verdict.value,
            status=passport.status.value,
            reason="passport issued",
        )


__all__ = ["MeasuredLoopConfig", "MeasuredResearchLoop"]
