# backend/domain/research/hypothesis.py
"""Autonomous research-loop contracts (task P4-002).

The research loop is the agent's body: it turns an open question into
structured hypotheses, runs experiments, weighs the evidence, and hands
promising candidates to the controlled-promotion pipeline (P4-001). The
loop itself can never deploy anything — it *produces evidence*; promotion
is a separate, gated decision.

Principles
----------
- **Hypotheses are structured claims.** A :class:`Hypothesis` carries its
  claim, its causal mechanism, and the concrete feature/parameter plan to
  test it — so the same claim from an AI or a rule source is comparable.
- **Evidence aggregates honestly.** A hypothesis is judged by its best
  experiment against the best applicable baseline; a hypothesis with too
  little evidence is ``"inconclusive"``, never ``"promising"``.
- **The loop cannot deploy itself.** The cycle ends in a
  :class:`PromotionHandoff` of *evidence only*. Entering production still
  requires the promotion gates (paper/canary windows, sharpe, drawdown), so
  an experiment that looks good cannot promote itself.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


class HypothesisSource(enum.StrEnum):
    """Where a hypothesis came from.

    RULE hypotheses are the deterministic, offline baseline generator.
    AI hypotheses come from the reasoning layer when an AI source is wired
    (still subject to the same filters and evidence rules).
    """

    RULE = "rule"
    AI = "ai"


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """One structured, testable claim.

    Attributes
    ----------
    hypothesis_id: str
        Unique identifier (caller-assigned).
    claim: str
        The falsifiable claim, in one sentence.
    mechanism: str
        Why the claim might hold (the causal story).
    feature_plan: tuple[str, ...] | None
        The feature keys the experiment should expose, or None for a
        strategy-level claim.
    params: Mapping[str, Any]
        Concrete parameters for the experiment (empty for a pure feature
        claim).
    source: HypothesisSource
        Where the hypothesis came from.
    """

    hypothesis_id: str
    claim: str
    mechanism: str
    feature_plan: tuple[str, ...] | None = None
    params: Mapping[str, Any] = field(default_factory=dict)
    source: HypothesisSource = HypothesisSource.RULE

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "claim": self.claim,
            "mechanism": self.mechanism,
            "feature_plan": list(self.feature_plan) if self.feature_plan else None,
            "params": dict(self.params),
            "source": self.source.value,
        }


@dataclass(frozen=True, slots=True)
class ExperimentOutcome:
    """Result of one experiment run for a hypothesis.

    ``improvement_bps`` is the net edge over the best applicable baseline in
    basis points; ``samples`` the number of out-of-sample observations;
    ``ok`` False when the run failed (failure is preserved, never dropped).
    """

    experiment_id: str
    hypothesis_id: str
    improvement_bps: float
    sharpe: float
    samples: int
    ok: bool = True
    failure_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "hypothesis_id": self.hypothesis_id,
            "improvement_bps": self.improvement_bps,
            "sharpe": self.sharpe,
            "samples": self.samples,
            "ok": self.ok,
            "failure_reason": self.failure_reason,
        }


class EvidenceVerdict(enum.StrEnum):
    """Honest summary of a hypothesis's evidence so far."""

    PROMISING = "promising"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class EvidenceSummary:
    """Aggregated evidence for one hypothesis after a research cycle.

    ``verdict`` follows strict rules: PROMISING requires at least one passed
    experiment whose sharpe clears the floor and whose net improvement is
    non-negative; REFUTED requires every experiment to fail the bar; anything
    with too little or split evidence is INCONCLUSIVE.
    """

    hypothesis_id: str
    verdict: EvidenceVerdict
    best_experiment_id: str | None
    best_improvement_bps: float
    best_sharpe: float
    samples: int
    experiment_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "verdict": self.verdict.value,
            "best_experiment_id": self.best_experiment_id,
            "best_improvement_bps": self.best_improvement_bps,
            "best_sharpe": self.best_sharpe,
            "samples": self.samples,
            "experiment_count": self.experiment_count,
        }


@dataclass(frozen=True, slots=True)
class CandidateInsight:
    """A hypothesis the loop surfaced as evidence for later promotion."""

    hypothesis: Hypothesis
    evidence: EvidenceSummary

    def as_dict(self) -> dict[str, Any]:
        return {
            "hypothesis": self.hypothesis.as_dict(),
            "evidence": self.evidence.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class CycleReport:
    """One full research cycle's output.

    ``insights`` are the hypotheses judged PROMISING. ``handoffs`` carry the
    promotion evidence (P4-001) the operator or a later stage may feed into
    ``PromotionEngine.evaluate`` — the loop itself does not promote.
    """

    insights: tuple[CandidateInsight, ...]
    rejected: tuple[str, ...]
    failed: tuple[ExperimentOutcome, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "insights": [i.as_dict() for i in self.insights],
            "rejected": list(self.rejected),
            "failed": [f.as_dict() for f in self.failed],
        }
