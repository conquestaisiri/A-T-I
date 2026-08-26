# backend/domain/research/autonomy_program.py
"""Composed autonomy-program contracts (post-queue hardening).

The ladder exists as independent, tested harnesses: research loop (P4-002),
promotion (P4-001), paper autonomy (P4-004), canary (P4-003), gradual
scaling (P4-005). This contract is the *single coherent program* that drives
a candidate through the whole ladder in one deterministic run — the
research-to-paper pipeline the audit calls the system's most important next
move — producing a composable record of every stage it passed or failed.

Principles
----------
- **One run, one record.** The program reports each stage's verdict and the
  candidate's cumulative evidence, so the operator can replay exactly how a
  candidate reached (or failed to reach) each rung.
- **The program never executes.** Stage runners are injected; the program is
  purely an orchestrator and recorder. No venue, no orders, no money.
- **Live touch is impossible from here.** The canary stage is exposed only
  as a gate check over earned evidence; starting a real canary still requires
  the operator's explicit authorization in ``CanaryHarness``.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

from backend.domain.research.promotion import CandidateEvidence


class ProgramStage(enum.StrEnum):
    """The ladder rungs a candidate may traverse in one program run."""

    RESEARCH = "research"
    VALIDATION = "validation"
    PAPER = "paper"
    CANARY = "canary"
    PRODUCTION = "production"


class StageVerdict(enum.StrEnum):
    """How one stage resolved for the candidate."""

    PASSED = "passed"
    HELD = "held"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_REACHED = "not_reached"


@dataclass(frozen=True, slots=True)
class StageResult:
    """Outcome of one ladder stage in a program run."""

    stage: ProgramStage
    verdict: StageVerdict
    reason: str = ""
    evidence: CandidateEvidence | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage.value,
            "verdict": self.verdict.value,
            "reason": self.reason,
            "evidence": self.evidence.as_dict() if self.evidence else None,
        }


@dataclass(frozen=True, slots=True)
class AutonomyProgramResult:
    """Full record of one composed autonomy-program run."""

    program_id: str
    candidate_id: str
    stages: tuple[StageResult, ...] = ()
    final_environment: str = ProgramStage.RESEARCH.value
    notes: tuple[str, ...] = ()
    _metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def earned(self) -> tuple[str, ...]:
        """Stages the candidate passed in promotion order."""
        return tuple(s.stage.value for s in self.stages if s.verdict is StageVerdict.PASSED)

    def as_dict(self) -> dict[str, Any]:
        return {
            "program_id": self.program_id,
            "candidate_id": self.candidate_id,
            "final_environment": self.final_environment,
            "earned": list(self.earned),
            "stages": [s.as_dict() for s in self.stages],
            "notes": list(self.notes),
        }
