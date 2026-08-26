# backend/application/research/hypothesis_passport.py
"""Hypothesis pool -> candidate passport birth records (task T3-21-1).

The research loop (P4-002) surfaces promising hypotheses; this module is
the seam that turns one hypothesis into a *candidate passport birth
record*: a RESEARCH-status passport with no evidence yet, so the claim
enters the same auditable ledger every evaluated strategy lives in —
before any experiment has run, not after.

Honesty rules
-------------
- **Born without evidence, never born promising.** The birth passport
  carries an empty evidence payload and the OBSERVE verdict; status is
  RESEARCH. A claim is not a result, and the ledger must not be able to
  confuse the two.
- **The claim is the hypothesis.** ``hypothesis`` is the hypothesis's
  claim text verbatim; ``model`` is the hypothesis source (rule/ai) and
  ``features`` the hypothesis's feature plan, so the birth record is
  traceable to the exact claim that produced it.
- **Lineage is preserved.** When the birth comes from a
  :class:`CandidateInsight`, its best experiment id is recorded as the
  passport's ``experiment_id``.
- **A birth record names its test ground.** ``dataset_id``/``dataset_version``
  are required: a passport that cannot say which frozen dataset the claim
  will be tested on would be a record without a test plan.
- **Library-only (Tier 3).** Nothing here is wired into the live path.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.application.interfaces.passport_store import PassportStore
from backend.domain.research.hypothesis import CandidateInsight, Hypothesis
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
)


def passport_from_hypothesis(
    hypothesis: Hypothesis,
    *,
    passport_id: str,
    dataset_id: str,
    dataset_version: int,
    created_at: datetime | None = None,
    experiment_id: str | None = None,
) -> StrategyPassport:
    """Project one hypothesis into a candidate passport birth record.

    The projection is pure: it never touches a store, and it never invents
    evidence — the payload is empty and the verdict OBSERVE until an actual
    evaluation exists.
    """
    if not passport_id:
        raise ValueError("passport_id must be non-empty")
    if not dataset_id:
        raise ValueError("dataset_id must be non-empty")
    if dataset_version < 1:
        raise ValueError("dataset_version must be >= 1")

    return StrategyPassport(
        passport_id=passport_id,
        created_at=created_at or datetime.now(UTC),
        hypothesis=hypothesis.claim,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        features=tuple(hypothesis.feature_plan) if hypothesis.feature_plan else (),
        model=hypothesis.source.value,
        trial_count=0,
        evidence={},
        verdict=PassportVerdict(EvidenceVerdict.OBSERVE),
        status=PassportStatus.RESEARCH,
        experiment_id=experiment_id,
    )


def birth_from_insight(
    insight: CandidateInsight,
    *,
    passport_id: str,
    dataset_id: str,
    dataset_version: int,
    created_at: datetime | None = None,
) -> StrategyPassport:
    """Project one promising loop insight into a passport birth record.

    The insight's best experiment becomes the passport's lineage
    (``experiment_id``); the evidence summary itself is deliberately NOT
    copied into the payload — the pooled evidence block belongs to the
    passport of the evaluated strategy, and this is the *hypothesis's*
    birth record.
    """
    return passport_from_hypothesis(
        insight.hypothesis,
        passport_id=passport_id,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        created_at=created_at,
        experiment_id=insight.evidence.best_experiment_id,
    )


class HypothesisBirthService:
    """Persists hypothesis birth records through the passport ledger.

    The store enforces immutability: a birth record is saved once, and a
    duplicate ``passport_id`` raises. Advisory only — saving a birth record
    promotes nothing.
    """

    def __init__(self, store: PassportStore) -> None:
        self._store = store

    def birth(
        self,
        hypothesis: Hypothesis,
        *,
        passport_id: str,
        dataset_id: str,
        dataset_version: int,
    ) -> StrategyPassport:
        """Persist a birth record for ``hypothesis`` and return it."""
        passport = passport_from_hypothesis(
            hypothesis,
            passport_id=passport_id,
            dataset_id=dataset_id,
            dataset_version=dataset_version,
        )
        self._store.save_passport(passport)
        return passport


__all__ = ["HypothesisBirthService", "birth_from_insight", "passport_from_hypothesis"]
