# backend/domain/research/passport.py
"""Strategy passport contracts (task P5-003, evidence engine).

Every evaluated strategy carries one auditable record — the passport — that
reproduces exactly how the strategy reached (or failed to reach) each rung:
hypothesis, data, features, model, trial count, train/validation/locked-test
periods, cost ruler, pooled out-of-sample evidence (P1-009), PBO/Deflated
Sharpe (P5-001), the evidence verdict, and its lifecycle status.

Why this object exists (per docs/ATI_Strategic_Review.md, Evidence Engine and
Strategy Passport sections):
- promotion must never rest on "AI thinks this is good" but on "here is the
  evidence";
- the autonomy system becomes auditable: every promotion/demotion/retirement
  has a recorded reason against recorded numbers;
- "as difficult to fool as possible": the verdict rules here are explicit,
  conservative, and unit-tested; a passport can always be reproduced from the
  numbers that produced it.

The verdict is deliberately capped at PROMOTE_TO_PAPER: nothing in research
evidence can promote a strategy straight to live. Paper/canary/live evidence
appends to the passport later (T3-24/25/27 lifecycle records).
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.pbo import PboResult


class PassportStatus(enum.StrEnum):
    """Lifecycle status of a strategy passport.

    RESEARCH -> CANDIDATE -> PAPER -> CANARY -> LIVE, with RETIRED as the
    terminal death state (Strategy Death System, review Phase 3). A passport
    never returns to an earlier stage; demotions move toward RETIRED.
    """

    RESEARCH = "research"  # hypothesis under investigation
    CANDIDATE = "candidate"  # evaluated; evidence gathered and verdict issued
    PAPER = "paper"  # paper campaign running (T3-24)
    CANARY = "canary"  # canary deployment running (T3-25)
    LIVE = "live"  # live allocation (requires all evidence gates)
    RETIRED = "retired"  # dead: demoted/retired, terminal


class EvidenceVerdict(enum.StrEnum):
    """The evidence engine's verdict on one passport's research evidence.

    REJECT: the pooled evidence is not good enough — the candidate is dead
    (or must change hypothesis and be re-evaluated as a new passport).
    OBSERVE: evidence insufficient to decide (e.g. too few folds for a
    Deflated Sharpe) — do not promote, do not kill; gather more evidence.
    PROMOTE_TO_PAPER: the evidence survived the conservative gates; the
    candidate may enter a paper campaign. Never higher than paper.
    """

    REJECT = "reject"
    OBSERVE = "observe"
    PROMOTE_TO_PAPER = "promote_to_paper"


@dataclass(frozen=True, slots=True)
class PassportVerdict:
    """Verdict plus the reasons that produced it (audit trail)."""

    verdict: EvidenceVerdict
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict.value,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class StrategyPassport:
    """One immutable evidence record for one evaluated strategy.

    Attributes
    ----------
    passport_id: str
        Unique, caller-assigned identifier (e.g. "STRAT-000184").
    created_at: datetime
        When the passport was created (aware UTC).
    hypothesis: str
        The claim being investigated (Strategic Review passport field).
    dataset_id, dataset_version: str, int
        The frozen dataset/version the evidence was produced on (P1-001).
    features: tuple[str, ...]
        The feature keys the evaluated pipeline consumed.
    model: str
        The reasoner/scorer name evaluated (rules-based solver by default).
    trial_count: int
        Number of experiments/trials that competed to produce this candidate
        (P5-001 multiple-testing deflation input).
    train_period, validation_period, test_period: tuple[str, str] | None
        ISO-8601 [start, end] periods, as recorded by the evaluation.
    cost_model: Mapping[str, Any]
        The exact cost ruler used (half spread, taker fee), for audit.
    evidence: Mapping[str, Any]
        The pooled out-of-sample evidence (PooledEvidence.as_dict()) plus,
        when a variant family was evaluated, the PBO family result.
    verdict: PassportVerdict
        The evidence verdict and its reasons (never promoted past paper).
    status: PassportStatus
        Lifecycle status; RESEARCH on creation.
    experiment_id: str | None
        Lineage: the registry experiment (P1-005) this passport derives from.
    paper_evidence, live_evidence: Mapping[str, Any]
        Filled by later lifecycle records (T3-24/25); empty until present.
    promotion_requirements, rollback_requirements: tuple[str, ...]
        The explicit conditions under which the strategy may advance or must
        be demoted — recorded here so the death system is auditable.
    last_review: datetime | None
        When the passport was last reviewed/re-evaluated.
    """

    passport_id: str
    created_at: datetime
    hypothesis: str
    dataset_id: str
    dataset_version: int
    features: tuple[str, ...]
    model: str
    trial_count: int
    train_period: tuple[str, str] | None = None
    validation_period: tuple[str, str] | None = None
    test_period: tuple[str, str] | None = None
    cost_model: Mapping[str, Any] = field(default_factory=dict)
    evidence: Mapping[str, Any] = field(default_factory=dict)
    verdict: PassportVerdict = PassportVerdict(EvidenceVerdict.OBSERVE)
    status: PassportStatus = PassportStatus.RESEARCH
    experiment_id: str | None = None
    paper_evidence: Mapping[str, Any] = field(default_factory=dict)
    live_evidence: Mapping[str, Any] = field(default_factory=dict)
    promotion_requirements: tuple[str, ...] = ()
    rollback_requirements: tuple[str, ...] = ()
    last_review: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the passport to a plain dictionary (round-trips)."""
        return {
            "passport_id": self.passport_id,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "hypothesis": self.hypothesis,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "features": list(self.features),
            "model": self.model,
            "trial_count": self.trial_count,
            "train_period": list(self.train_period) if self.train_period else None,
            "validation_period": list(self.validation_period) if self.validation_period else None,
            "test_period": list(self.test_period) if self.test_period else None,
            "cost_model": dict(self.cost_model),
            "evidence": dict(self.evidence),
            "verdict": self.verdict.as_dict(),
            "status": self.status.value,
            "experiment_id": self.experiment_id,
            "paper_evidence": dict(self.paper_evidence),
            "live_evidence": dict(self.live_evidence),
            "promotion_requirements": list(self.promotion_requirements),
            "rollback_requirements": list(self.rollback_requirements),
            "last_review": (
                self.last_review.isoformat(timespec="milliseconds")
                if self.last_review is not None
                else None
            ),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> StrategyPassport:
        """Reconstruct a passport from :meth:`as_dict` output."""
        return cls(
            passport_id=str(data["passport_id"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            hypothesis=str(data["hypothesis"]),
            dataset_id=str(data["dataset_id"]),
            dataset_version=int(data["dataset_version"]),
            features=tuple(str(f) for f in data["features"]),
            model=str(data["model"]),
            trial_count=int(data["trial_count"]),
            train_period=_optional_period(data.get("train_period")),
            validation_period=_optional_period(data.get("validation_period")),
            test_period=_optional_period(data.get("test_period")),
            cost_model=dict(data.get("cost_model") or {}),
            evidence=dict(data.get("evidence") or {}),
            verdict=_verdict_from_dict(data.get("verdict") or {}),
            status=PassportStatus(str(data["status"])),
            experiment_id=(
                str(data["experiment_id"]) if data.get("experiment_id") is not None else None
            ),
            paper_evidence=dict(data.get("paper_evidence") or {}),
            live_evidence=dict(data.get("live_evidence") or {}),
            promotion_requirements=tuple(
                str(r) for r in (data.get("promotion_requirements") or [])
            ),
            rollback_requirements=tuple(str(r) for r in (data.get("rollback_requirements") or [])),
            last_review=(
                datetime.fromisoformat(str(data["last_review"]))
                if data.get("last_review") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class PassportLifecycleEvent:
    """One append-only lifecycle change to a passport (audit trail).

    A passport record is immutable; every status change or evidence
    replacement is a new event appended to the passport's ledger, so the
    operator can replay exactly how a strategy died or advanced.
    """

    passport_id: str
    event_type: str  # e.g. "status_change" | "evidence_update"
    occurred_at: datetime
    from_status: PassportStatus | None = None
    to_status: PassportStatus | None = None
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "passport_id": self.passport_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at.isoformat(timespec="milliseconds"),
            "from_status": self.from_status.value if self.from_status else None,
            "to_status": self.to_status.value if self.to_status else None,
            "reason": self.reason,
        }


def verdict_for_evidence(
    pooled: PooledEvidence,
    *,
    pbo: PboResult | None = None,
    max_pbo: float = 0.5,
    min_positive_fold_rate: float = 0.5,
    min_beats_buy_and_hold_rate: float = 0.5,
    max_mean_drawdown_pct: float = -25.0,
) -> PassportVerdict:
    """Conservative, explicit evidence verdict from pooled OOS evidence.

    The gates, in order (first failing gate decides; all reasons recorded):
    1. PBO (P5-001) above ``max_pbo`` -> REJECT (selection bias too high).
    2. Deflated Sharpe below zero -> REJECT (no positive risk-adjusted
       return after pricing for multiple testing).
    3. Positive-fold rate below threshold -> REJECT.
    4. Beats-buy-and-hold rate below threshold -> REJECT.
    5. Mean max drawdown deeper than ``max_mean_drawdown_pct`` -> REJECT.
    6. Deflated Sharpe unknown (too few folds) -> OBSERVE: never promote on
       insufficient evidence, but never kill on missing evidence either.
    7. Otherwise -> PROMOTE_TO_PAPER (never higher than paper).

    Every gate names the number that failed it, so the verdict is auditable.
    """
    reasons: list[str] = []

    if pbo is not None and pbo.pbo > max_pbo:
        reasons.append(
            f"PBO {pbo.pbo:.4f} exceeds {max_pbo:.2f}: "
            "selecting this candidate on past folds is overfitting"
        )
        return PassportVerdict(EvidenceVerdict.REJECT, tuple(reasons))

    if pooled.deflated_sharpe is not None:
        if pooled.deflated_sharpe <= 0.0:
            reasons.append(
                f"deflated Sharpe {pooled.deflated_sharpe:.4f} is not positive "
                f"after {pooled.n_folds} folds"
            )
            return PassportVerdict(EvidenceVerdict.REJECT, tuple(reasons))
    else:
        reasons.append(
            "deflated Sharpe unavailable (too few folds for DSR); evidence insufficient to promote"
        )
        return PassportVerdict(EvidenceVerdict.OBSERVE, tuple(reasons))

    if pooled.positive_fold_rate < min_positive_fold_rate:
        reasons.append(
            f"positive-fold rate {pooled.positive_fold_rate:.3f} below {min_positive_fold_rate:.2f}"
        )
        return PassportVerdict(EvidenceVerdict.REJECT, tuple(reasons))

    if pooled.beats_buy_and_hold_rate < min_beats_buy_and_hold_rate:
        reasons.append(
            f"beats-buy-and-hold rate {pooled.beats_buy_and_hold_rate:.3f} below "
            f"{min_beats_buy_and_hold_rate:.2f}"
        )
        return PassportVerdict(EvidenceVerdict.REJECT, tuple(reasons))

    if pooled.mean_max_drawdown_pct < max_mean_drawdown_pct:
        reasons.append(
            f"mean max drawdown {pooled.mean_max_drawdown_pct:.3f}% deeper than "
            f"{max_mean_drawdown_pct:.1f}%"
        )
        return PassportVerdict(EvidenceVerdict.REJECT, tuple(reasons))

    reasons.append(
        f"passed all evidence gates on {pooled.n_folds} out-of-sample folds "
        f"(DSR {pooled.deflated_sharpe:.4f}, positive-fold rate "
        f"{pooled.positive_fold_rate:.3f})"
    )
    return PassportVerdict(EvidenceVerdict.PROMOTE_TO_PAPER, tuple(reasons))


def _optional_period(value: Any) -> tuple[str, str] | None:
    if not value:
        return None
    items = list(value)
    return (str(items[0]), str(items[1]))


def _verdict_from_dict(data: Mapping[str, Any]) -> PassportVerdict:
    return PassportVerdict(
        verdict=EvidenceVerdict(str(data["verdict"])),
        reasons=tuple(str(r) for r in data.get("reasons") or []),
    )


__all__ = [
    "PassportStatus",
    "EvidenceVerdict",
    "PassportVerdict",
    "StrategyPassport",
    "PassportLifecycleEvent",
    "verdict_for_evidence",
]
