# backend/domain/research/strategy_population.py
"""Strategy population registry contracts (task T2-12-1).

The passport store (P5-003) is the durable seed of the population: every
evaluated strategy already carries a passport. This module is the read-side
projection an operator (or later, the promotion system) uses to answer
"what strategies exist, in what state, with what evidence?" — and, once at
least ``min_ladder_candidates`` real candidates exist, a competition ladder
that ranks them by their pooled out-of-sample evidence.

Honesty invariants
------------------
- **The registry is a projection, not a new store.** Members are derived
  from passports at read time; there is no second copy of evidence that
  could drift from the ledger.
- **A "real candidate" means evaluated evidence.** A passport counts toward
  the ladder only when its evidence carries a pooled block with at least one
  fold (an actual OOS run), regardless of the verdict: REJECTed candidates
  still competed and remain visible, ranked honestly by the same rule as
  everyone else.
- **The ladder is gated.** With fewer than ``min_ladder_candidates`` real
  candidates the ladder is None and the reason says so — a one- or
  two-strategy "competition" would be fabricated, so nothing is fabricated.
- **The ladder is advisory.** Ranking by evidence never changes a passport's
  verdict or status; promotion stays behind its own gates
  (``verdict_for_evidence``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
    StrategyPassport,
)


@dataclass(frozen=True, slots=True)
class PopulationMember:
    """One strategy's registry row, projected from its passport.

    Attributes
    ----------
    passport_id: str
        The passport id (the population key).
    created_at: datetime
        When the passport was issued (aware UTC).
    hypothesis: str
        The claim under investigation.
    model: str
        The reasoner/scorer name evaluated.
    dataset_id, dataset_version: str, int
        The frozen dataset/version the evidence was produced on.
    status: PassportStatus
        Lifecycle status (RESEARCH/CANDIDATE/.../RETIRED).
    verdict: EvidenceVerdict
        The evidence engine's verdict on this candidate.
    n_folds: int
        Number of out-of-sample folds in the pooled evidence (0 = none yet).
    mean_excess_return_pct: float | None
        Pooled mean net excess return (None when not yet evaluated).
    deflated_sharpe: float | None
        Pooled Deflated Sharpe (None when inestimable/not yet evaluated).
    positive_fold_rate: float | None
        Pooled positive-fold rate (None when not yet evaluated).
    beats_buy_and_hold_rate: float | None
        Pooled beats-buy-and-hold rate (None when not yet evaluated).
    regime_robustness_score: float | None
        The regime-conditioned robustness score from T2-11-1, when the
        passport carries regime evidence (advisory; never the rank key).
    """

    passport_id: str
    created_at: datetime
    hypothesis: str
    model: str
    dataset_id: str
    dataset_version: int
    status: PassportStatus
    verdict: EvidenceVerdict
    n_folds: int
    mean_excess_return_pct: float | None
    deflated_sharpe: float | None
    positive_fold_rate: float | None
    beats_buy_and_hold_rate: float | None
    regime_robustness_score: float | None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the member to a plain dictionary (round-trips)."""
        return {
            "passport_id": self.passport_id,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "hypothesis": self.hypothesis,
            "model": self.model,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "status": self.status.value,
            "verdict": self.verdict.value,
            "n_folds": self.n_folds,
            "mean_excess_return_pct": self.mean_excess_return_pct,
            "deflated_sharpe": self.deflated_sharpe,
            "positive_fold_rate": self.positive_fold_rate,
            "beats_buy_and_hold_rate": self.beats_buy_and_hold_rate,
            "regime_robustness_score": self.regime_robustness_score,
        }


@dataclass(frozen=True, slots=True)
class LadderEntry:
    """One rank in the competition ladder (advisory population view)."""

    rank: int
    member: PopulationMember

    def as_dict(self) -> dict[str, Any]:
        return {"rank": self.rank, **self.member.as_dict()}


@dataclass(frozen=True, slots=True)
class CompetitionLadder:
    """Ranked real candidates by pooled mean excess return, highest first.

    Ties are broken by Deflated Sharpe (higher first), then by passport id
    (lexicographic) so the ordering is fully deterministic. The rank rule is
    fixed and documented: mean excess over the costed buy-and-hold baseline,
    the same frame the verdict gates grade.
    """

    entries: tuple[LadderEntry, ...]

    def as_dict(self) -> dict[str, Any]:
        return {"entries": [entry.as_dict() for entry in self.entries]}


@dataclass(frozen=True, slots=True)
class DatasetSlice:
    """One (dataset, version) slice of the population.

    Attributes
    ----------
    dataset_id: str
        The frozen dataset id.
    dataset_version: int
        The frozen dataset version.
    n: int
        How many members were evaluated on this slice.
    """

    dataset_id: str
    dataset_version: int
    n: int

    def as_dict(self) -> dict[str, Any]:
        return {"dataset_id": self.dataset_id, "dataset_version": self.dataset_version, "n": self.n}


@dataclass(frozen=True, slots=True)
class CompositionView:
    """Population-level composition: what the population is made of.

    Attributes
    ----------
    total: int
        All passport members, evaluated or not.
    environment_counts: dict[str, int]
        Members per lifecycle status (RESEARCH/CANDIDATE/PAPER/CANARY/
        LIVE/RETIRED), zero-inclusive for the statuses that exist.
    verdict_counts: dict[str, int]
        Members per evidence verdict (REJECT/OBSERVE/PROMOTE_TO_PAPER).
    dataset_breakdown: tuple[DatasetSlice, ...]
        Members per frozen dataset slice, sorted by (dataset_id,
        dataset_version).
    """

    total: int
    environment_counts: dict[str, int]
    verdict_counts: dict[str, int]
    dataset_breakdown: tuple[DatasetSlice, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "environment_counts": self.environment_counts,
            "verdict_counts": self.verdict_counts,
            "dataset_breakdown": [slice_.as_dict() for slice_ in self.dataset_breakdown],
        }


@dataclass(frozen=True, slots=True)
class PopulationComposition:
    """The composition view, gated like the ladder.

    Attributes
    ----------
    view: CompositionView | None
        The composition when at least ``min_composition_candidates`` real
        candidates exist; None otherwise.
    unavailable_reason: str
        Why the view is None (empty when the view is present).
    min_composition_candidates: int
        The gate that was applied.
    """

    view: CompositionView | None
    unavailable_reason: str = ""
    min_composition_candidates: int = 5

    def as_dict(self) -> dict[str, Any]:
        return {
            "view": self.view.as_dict() if self.view is not None else None,
            "unavailable_reason": self.unavailable_reason,
            "min_composition_candidates": self.min_composition_candidates,
        }


@dataclass(frozen=True, slots=True)
class StrategyPopulation:
    """The full population registry: every passport plus the optional ladder.

    Attributes
    ----------
    members: tuple[PopulationMember, ...]
        Every passport projected to a member row, in issue order (oldest
        first, ties by passport id).
    ladder: CompetitionLadder | None
        The competition ladder when at least ``min_ladder_candidates`` real
        candidates exist; None otherwise.
    ladder_unavailable_reason: str
        Why the ladder is None (empty when the ladder is present).
    min_ladder_candidates: int
        The ladder gate that was applied.
    """

    members: tuple[PopulationMember, ...]
    ladder: CompetitionLadder | None
    ladder_unavailable_reason: str = ""
    min_ladder_candidates: int = 3

    def as_dict(self) -> dict[str, Any]:
        """Serialise the registry to a plain dictionary."""
        return {
            "members": [member.as_dict() for member in self.members],
            "ladder": self.ladder.as_dict() if self.ladder is not None else None,
            "ladder_unavailable_reason": self.ladder_unavailable_reason,
            "min_ladder_candidates": self.min_ladder_candidates,
        }


__all__ = [
    "CompetitionLadder",
    "CompositionView",
    "DatasetSlice",
    "LadderEntry",
    "PopulationComposition",
    "PopulationMember",
    "StrategyPopulation",
    "member_from_passport",
]


def member_from_passport(passport: StrategyPassport) -> PopulationMember:
    """Project one passport into a registry member row.

    The pooled evidence is read from the passport's own evidence payload
    (``evidence["pooled"]``), never re-computed, so the registry can never
    disagree with the ledger. Regime robustness comes from the T2-11-1
    regime evidence block when present.
    """
    pooled = dict(passport.evidence.get("pooled") or {})
    regime = dict(passport.evidence.get("regime_evidence") or {})
    return PopulationMember(
        passport_id=passport.passport_id,
        created_at=passport.created_at,
        hypothesis=passport.hypothesis,
        model=passport.model,
        dataset_id=passport.dataset_id,
        dataset_version=passport.dataset_version,
        status=passport.status,
        verdict=passport.verdict.verdict,
        n_folds=int(pooled.get("n_folds", 0)),
        mean_excess_return_pct=_opt_float(pooled.get("mean_excess_return_pct")),
        deflated_sharpe=_opt_float(pooled.get("deflated_sharpe")),
        positive_fold_rate=_opt_float(pooled.get("positive_fold_rate")),
        beats_buy_and_hold_rate=_opt_float(pooled.get("beats_buy_and_hold_rate")),
        regime_robustness_score=_opt_float(regime.get("robustness_score")),
    )


def _opt_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as _:
        return None
