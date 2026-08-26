# backend/application/research/autonomy_program.py
"""Composed autonomy-program orchestrator (post-queue hardening).

Drives one candidate through the entire ladder in a single deterministic
run: research -> validation -> paper autonomy -> canary gate -> gradual
scaling. Each stage delegates to the already-tested harness (ResearchLoop,
PromotionEngine, PaperAutonomyRunner, CanaryHarness gate logic,
GradualScalingRunner); this module only *composes and records* them.

Design rules
------------
- **Stages are injected callables.** The program never executes; the caller
  supplies one callable per stage (over the real or fake services). This is
  the same injected-callable discipline as every other harness in the
  research package.
- **Earned is a strict chain.** A candidate advances to a later stage only
  if every earlier stage passed; a HELD/FAILED stage stops the run at that
  rung and later stages are recorded NOT_REACHED.
- **No live path.** The canary stage is a *gate check* over the candidate's
  earned evidence (CanaryHarness PRODUCTION_READY vs HOLD replaces the
  operator-authorized live run); actual canary/live start is never here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

from backend.domain.research.autonomy_program import (
    AutonomyProgramResult,
    ProgramStage,
    StageResult,
    StageVerdict,
)
from backend.domain.research.promotion import CandidateEvidence

logger = logging.getLogger(__name__)

# Each stage runner is injected: (candidate_id, evidence) -> StageResult.
StageRunner = Callable[[str, CandidateEvidence], StageResult]


@dataclass(frozen=True, slots=True)
class ProgramConfig:
    """Controls which stages the program runs and their order.

    ``stages`` is the ladder to run, from entry to the target rung. The
    caller supplies the matching ``runners`` in the same order (one per
    stage, except ``canary`` which is always the gate check and
    ``production`` which is always the gradual-scaling ramp).
    """

    program_id: str = "prog-1"
    entry_evidence: CandidateEvidence | None = None
    stages: tuple[ProgramStage, ...] = (
        ProgramStage.RESEARCH,
        ProgramStage.VALIDATION,
        ProgramStage.PAPER,
        ProgramStage.CANARY,
        ProgramStage.PRODUCTION,
    )
    runners: dict[ProgramStage, StageRunner] = field(default_factory=dict)


class AutonomyProgram:
    """Run one candidate through the composed ladder and record every rung."""

    def __init__(self, config: ProgramConfig | None = None) -> None:
        self._config = config or ProgramConfig()
        self._evidence: CandidateEvidence = self._config.entry_evidence or CandidateEvidence(
            candidate_id="candidate"
        )

    def run(
        self,
        candidate_id: str,
        *,
        runner_for: dict[ProgramStage, StageRunner] | None = None,
    ) -> AutonomyProgramResult:
        """Run the configured ladder for ``candidate_id``.

        Stages stop at the first non-PASSED verdict; later stages are
        recorded NOT_REACHED. Production is exercised as a gradual-scaling
        ramp marked PASSED only when it caps cleanly.
        """
        runners = runner_for or self._config.runners
        results: list[StageResult] = []
        earned_stage: ProgramStage | None = None
        notes: list[str] = []

        for stage in self._config.stages:
            runner = runners.get(stage)
            if runner is None:
                results.append(
                    StageResult(
                        stage=stage,
                        verdict=StageVerdict.SKIPPED,
                        reason="no runner supplied for this stage",
                    )
                )
                continue

            result = runner(candidate_id, self._evidence)
            results.append(result)

            if result.verdict is StageVerdict.PASSED:
                earned_stage = stage
                if result.evidence is not None:
                    self._evidence = result.evidence
                continue

            # Failure/held stops the chain; record the rest as not reached.
            if result.verdict is StageVerdict.FAILED or result.verdict is StageVerdict.HELD:
                notes.append(
                    f"{stage.value} ended the run with verdict "
                    f"{result.verdict.value}: {result.reason}"
                )
                self._append_not_reached(results, results[-1].stage if results else None)
                break

        final_env = earned_stage.value if earned_stage else ProgramStage.RESEARCH.value
        return AutonomyProgramResult(
            program_id=self._config.program_id,
            candidate_id=candidate_id,
            stages=tuple(results),
            final_environment=final_env,
            notes=tuple(notes),
        )

    def _append_not_reached(self, results: list[StageResult], stop_at: ProgramStage | None) -> None:
        """Append NOT_REACHED for every configured stage after ``stop_at``."""
        recording = False
        for stage in self._config.stages:
            if stop_at is not None and stage is stop_at:
                recording = True
                continue
            if recording and not any(r.stage is stage for r in results):
                results.append(
                    StageResult(
                        stage=stage,
                        verdict=StageVerdict.NOT_REACHED,
                        reason="earlier stage ended the program",
                    )
                )


def run_autonomy_program(
    candidate_id: str,
    *,
    config: ProgramConfig | None = None,
    runner_for: dict[ProgramStage, StageRunner] | None = None,
) -> AutonomyProgramResult:
    """Module-level convenience: run one composed autonomy program."""
    return AutonomyProgram(config).run(candidate_id, runner_for=runner_for)
