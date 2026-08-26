# tests/application/test_autonomy_program.py
"""Tests for the composed autonomy-program orchestrator (post-queue hardening).

The program drives one candidate through the full ladder
(research -> validation -> paper -> canary -> production) in one
deterministic run, recording each stage verdict. It is a recorder and
composer only: every stage runner is injected, and nothing here can touch a
venue, an order, or live capital.
"""

from __future__ import annotations

from typing import Any

from backend.application.research.autonomy_program import (
    AutonomyProgram,
    ProgramConfig,
    run_autonomy_program,
)
from backend.domain.research.autonomy_program import (
    AutonomyProgramResult,
    ProgramStage,
    StageResult,
    StageVerdict,
)
from backend.domain.research.promotion import CandidateEvidence


def _evidence(**overrides: Any) -> CandidateEvidence:
    defaults: dict[str, Any] = {"candidate_id": "model-a"}
    defaults.update(overrides)
    return CandidateEvidence(**defaults)


def _pass(stage: ProgramStage, reason: str = "ok") -> StageRunnerLike:
    def run(candidate_id: str, evidence: CandidateEvidence) -> StageResult:
        return StageResult(
            stage=stage,
            verdict=StageVerdict.PASSED,
            reason=reason,
            evidence=_evidence(
                **{k: v for k, v in evidence.as_dict().items() if k != "candidate_id"},
                candidate_id=candidate_id,
            ),
        )

    return run


def _hold(stage: ProgramStage, reason: str = "need more evidence") -> StageRunnerLike:
    def run(candidate_id: str, evidence: CandidateEvidence) -> StageResult:
        return StageResult(stage=stage, verdict=StageVerdict.HELD, reason=reason)

    return run


def _fail(stage: ProgramStage, reason: str = "breach") -> StageRunnerLike:
    def run(candidate_id: str, evidence: CandidateEvidence) -> StageResult:
        return StageResult(stage=stage, verdict=StageVerdict.FAILED, reason=reason)

    return run


StageRunnerLike = Any

_STAGES = (
    ProgramStage.RESEARCH,
    ProgramStage.VALIDATION,
    ProgramStage.PAPER,
    ProgramStage.CANARY,
    ProgramStage.PRODUCTION,
)


def _all_pass() -> dict[ProgramStage, StageRunnerLike]:
    return {s: _pass(s) for s in _STAGES}


class TestPassThroughTheLadder:
    def test_all_steps_pass_reaches_production(self) -> None:
        result = AutonomyProgram().run("model-a", runner_for=_all_pass())
        assert result.final_environment == "production"
        assert result.earned == tuple(s.value for s in _STAGES)
        assert all(s.verdict is StageVerdict.PASSED for s in result.stages)

    def test_earned_is_in_promotion_order(self) -> None:
        result = AutonomyProgram().run("model-a", runner_for=_all_pass())
        assert result.earned == (
            "research",
            "validation",
            "paper",
            "canary",
            "production",
        )

    def test_program_id_and_candidate_recorded(self) -> None:
        result = AutonomyProgram(ProgramConfig(program_id="prog-z")).run(
            "model-a", runner_for=_all_pass()
        )
        assert result.program_id == "prog-z"
        assert result.candidate_id == "model-a"


class TestEarlyExit:
    def test_hold_stops_the_run_and_marks_later_stages_not_reached(self) -> None:
        runner_for: dict[ProgramStage, StageRunnerLike] = _all_pass()
        runner_for[ProgramStage.PAPER] = _hold(ProgramStage.PAPER)
        result = AutonomyProgram().run("model-a", runner_for=runner_for)
        assert result.final_environment == "validation"
        assert result.earned == ("research", "validation")
        held = next(s for s in result.stages if s.stage is ProgramStage.PAPER)
        assert held.verdict is StageVerdict.HELD
        later = [s for s in result.stages if s.verdict is StageVerdict.NOT_REACHED]
        assert {s.stage for s in later} == {
            ProgramStage.CANARY,
            ProgramStage.PRODUCTION,
        }

    def test_failure_stops_the_run(self) -> None:
        runner_for: dict[ProgramStage, StageRunnerLike] = _all_pass()
        runner_for[ProgramStage.CANARY] = _fail(ProgramStage.CANARY)
        result = AutonomyProgram().run("model-a", runner_for=runner_for)
        assert result.final_environment == "paper"
        assert result.earned == ("research", "validation", "paper")
        assert result.stages[-1].verdict is StageVerdict.NOT_REACHED

    def test_no_stage_reached_stays_in_research(self) -> None:
        runner_for: dict[ProgramStage, StageRunnerLike] = _all_pass()
        runner_for[ProgramStage.RESEARCH] = _fail(ProgramStage.RESEARCH)
        result = AutonomyProgram().run("model-a", runner_for=runner_for)
        assert result.final_environment == "research"
        assert result.earned == ()


class TestEvidencePropagation:
    def test_passed_stage_evidence_is_forwarded_to_next_stage(self) -> None:
        seen: list[CandidateEvidence] = []

        def research(candidate_id: str, evidence: CandidateEvidence) -> StageResult:
            return StageResult(
                stage=ProgramStage.RESEARCH,
                verdict=StageVerdict.PASSED,
                evidence=_evidence(validation_samples=400),
            )

        def validation(candidate_id: str, evidence: CandidateEvidence) -> StageResult:
            seen.append(evidence)
            return StageResult(
                stage=ProgramStage.VALIDATION,
                verdict=StageVerdict.PASSED,
                evidence=_evidence(validation_samples=900),
            )

        runner_for = _all_pass()
        runner_for[ProgramStage.RESEARCH] = research
        runner_for[ProgramStage.VALIDATION] = validation
        AutonomyProgram().run("model-a", runner_for=runner_for)
        assert seen[0].validation_samples == 400

    def test_default_entry_evidence_is_used_without_error(self) -> None:
        result = AutonomyProgram().run("model-a", runner_for=_all_pass())
        assert result.stages[0].evidence is not None


class TestMissingRunner:
    def test_skipped_stage_is_recorded_and_chain_continues(self) -> None:
        runner_for = _all_pass()
        del runner_for[ProgramStage.VALIDATION]
        result = AutonomyProgram().run("model-a", runner_for=runner_for)
        skipped = next(s for s in result.stages if s.stage is ProgramStage.VALIDATION)
        assert skipped.verdict is StageVerdict.SKIPPED
        assert skipped.reason == "no runner supplied for this stage"
        # Skipped does not break the chain; later stages still run.
        assert result.final_environment == "production"


class TestSerialization:
    def test_result_as_dict(self) -> None:
        result = AutonomyProgram().run("model-a", runner_for=_all_pass())
        payload = result.as_dict()
        assert payload["candidate_id"] == "model-a"
        assert payload["final_environment"] == "production"
        assert len(payload["stages"]) == 5
        assert payload["stages"][0]["stage"] == "research"
        assert payload["stages"][0]["verdict"] == "passed"

    def test_stage_result_as_dict_round_trips(self) -> None:
        sr = StageResult(
            stage=ProgramStage.PAPER,
            verdict=StageVerdict.PASSED,
            reason="earned it",
            evidence=_evidence(paper_days_deployed=30),
        )
        payload = sr.as_dict()
        assert payload["stage"] == "paper"
        assert payload["verdict"] == "passed"
        assert payload["evidence"]["paper_days_deployed"] == 30

    def test_convenience_function_wires_the_program(self) -> None:
        result = run_autonomy_program("model-a", runner_for=_all_pass())
        assert isinstance(result, AutonomyProgramResult)

    def test_same_input_same_result(self) -> None:
        def run() -> dict[str, Any]:
            return (
                AutonomyProgram(ProgramConfig(program_id="prog-x"))
                .run("model-a", runner_for=_all_pass())
                .as_dict()
            )

        assert run() == run()
