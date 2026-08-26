# backend/domain/research/promotion.py
"""Controlled model-promotion contracts (task P4-001).

A candidate model/strategy earns its way up a fixed chain of environments:
``research -> validation -> paper -> canary -> production``. Promotion to the
next environment is granted only when the candidate's cumulative evidence
clears that environment's gate; rollback is an automatic, deterministic
consequence of breaching a stay requirement while deployed.

Principles
----------
- **Gates exist and are ordered.** There is one promotion gate per
  environment, evaluated cumulatively: an application to promote into a later
  environment re-checks every earlier gate, so evidence can neither go stale
  nor be leapfrogged.
- **Unknown is not promoted.** Insufficient evidence means "stay", not
  "promote": every gate fails closed.
- **Rollback is automatic and tested.** While deployed, a candidate that
  breaches drawdown, underperformance, or operational-error limits is demoted
  one environment by ``PromotionEngine.rollback_required`` — no human
  intervention required, no way to "push through".
- **Production is earned, not granted.** Reaching production requires the
  most time and the lowest realised deviation; performance in canary is judged
  against exactly the limits a live environment must respect.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Any


class ModelEnvironment(enum.StrEnum):
    """Stages a candidate model can inhabit, in promotion order."""

    RESEARCH = "research"
    VALIDATION = "validation"
    PAPER = "paper"
    CANARY = "canary"
    PRODUCTION = "production"


# Promotion order. RESEARCH is the entry point; PRODUCTION the only terminal.
ENVIRONMENT_CHAIN: tuple[ModelEnvironment, ...] = (
    ModelEnvironment.RESEARCH,
    ModelEnvironment.VALIDATION,
    ModelEnvironment.PAPER,
    ModelEnvironment.CANARY,
    ModelEnvironment.PRODUCTION,
)


def next_environment(environment: ModelEnvironment) -> ModelEnvironment | None:
    """The environment immediately after ``environment``, or None at PRODUCTION."""
    try:
        index = ENVIRONMENT_CHAIN.index(environment)
    except ValueError as exc:
        raise ValueError(f"unknown environment {environment!r}") from exc
    if index + 1 >= len(ENVIRONMENT_CHAIN):
        return None
    return ENVIRONMENT_CHAIN[index + 1]


def previous_environment(environment: ModelEnvironment) -> ModelEnvironment | None:
    """The environment immediately before ``environment``, or None at RESEARCH."""
    try:
        index = ENVIRONMENT_CHAIN.index(environment)
    except ValueError as exc:
        raise ValueError(f"unknown environment {environment!r}") from exc
    if index == 0:
        return None
    return ENVIRONMENT_CHAIN[index - 1]


@dataclass(frozen=True, slots=True)
class PromotionConfig:
    """Gate and stay limits for controlled promotion.

    Attributes
    ----------
    validation_samples_min:
        Minimum out-of-sample samples validation must show before paper.
    validation_sharpe_min:
        Minimum out-of-sample Sharpe ratio before paper.
    paper_period_days_min:
        Minimum calendar days in paper before canary.
    paper_sharpe_min:
        Minimum realised Sharpe in paper before canary.
    canary_period_days_min:
        Minimum calendar days in canary before production.
    max_drawdown_pct:
        Stay limit: realised drawdown above this demotes the candidate.
    max_underperformance_bps:
        Stay limit: realised return below expected (per basis point
        threshold) demotes the candidate.
    max_failed_orders_pct:
        Stay limit: fraction of rejected/faulted orders above this demotes
        the candidate (operational health).
    """

    validation_samples_min: int = 200
    validation_sharpe_min: float = 0.5
    paper_period_days_min: int = 14
    paper_sharpe_min: float = 0.3
    canary_period_days_min: int = 7
    max_drawdown_pct: float = 10.0
    max_underperformance_bps: float = 25.0
    max_failed_orders_pct: float = 5.0


@dataclass(frozen=True, slots=True)
class CandidateEvidence:
    """Cumulative performance evidence one candidate has accumulated.

    Counters are monotonic within an environment (days deployed only grow).
    Fields not yet applicable to the candidate's environment are reported as
    ``None`` and never used against it.
    """

    candidate_id: str
    validation_samples: int | None = None
    validation_sharpe: float | None = None
    paper_days_deployed: int | None = None
    paper_sharpe: float | None = None
    canary_days_deployed: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "validation_samples": self.validation_samples,
            "validation_sharpe": self.validation_sharpe,
            "paper_days_deployed": self.paper_days_deployed,
            "paper_sharpe": self.paper_sharpe,
            "canary_days_deployed": self.canary_days_deployed,
        }


@dataclass(frozen=True, slots=True)
class PromotionRequest:
    """A single application to promote a candidate one environment."""

    candidate_id: str
    environment: ModelEnvironment  # the environment being promoted INTO
    evidence: CandidateEvidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "environment": self.environment.value,
            "evidence": self.evidence.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Outcome of one promotion gate.

    ``required`` names every condition the gate demands; ``satisfied`` names
    the subset actually met. ``allowed`` is True only when all are satisfied.
    """

    candidate_id: str
    environment: ModelEnvironment
    allowed: bool
    required: tuple[str, ...]
    satisfied: tuple[str, ...]

    @property
    def reasons(self) -> tuple[str, ...]:
        """Missing conditions when denied (empty when allowed)."""
        if self.allowed:
            return ()
        return tuple(
            requirement for requirement in self.required if requirement not in self.satisfied
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "environment": self.environment.value,
            "allowed": self.allowed,
            "required": list(self.required),
            "satisfied": list(self.satisfied),
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class DeploymentMonitor:
    """Live performance snapshot of one deployed candidate."""

    candidate_id: str
    environment: ModelEnvironment
    drawdown_pct: float
    underperformance_bps: float = 0.0
    failed_orders_pct: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "environment": self.environment.value,
            "drawdown_pct": self.drawdown_pct,
            "underperformance_bps": self.underperformance_bps,
            "failed_orders_pct": self.failed_orders_pct,
        }


@dataclass(frozen=True, slots=True)
class RollbackDecision:
    """Outcome of the automatic stay-or-demote check."""

    candidate_id: str
    rollback: bool
    to_environment: ModelEnvironment | None
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "rollback": self.rollback,
            "to_environment": self.to_environment.value if self.to_environment else None,
            "reasons": list(self.reasons),
        }
