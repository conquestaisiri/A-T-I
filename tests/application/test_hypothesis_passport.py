"""Tests for hypothesis -> passport birth records (T3-21-1).

Birth records must enter the ledger before any evidence exists — RESEARCH
status, OBSERVE verdict, empty payload, claim text as hypothesis, source
as model, lineage to the best experiment — and must be saved once, never
overwritten.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.research.hypothesis_passport import (
    HypothesisBirthService,
    birth_from_insight,
    passport_from_hypothesis,
)
from backend.domain.research.hypothesis import (
    CandidateInsight,
    EvidenceSummary,
    Hypothesis,
    HypothesisSource,
)
from backend.domain.research.hypothesis import (
    EvidenceVerdict as HypothesisVerdict,
)
from backend.domain.research.passport import EvidenceVerdict, PassportStatus
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import SqlitePassportRepository


def hypothesis(
    *,
    source: HypothesisSource = HypothesisSource.RULE,
    feature_plan: tuple[str, ...] | None = ("trend", "momentum"),
) -> Hypothesis:
    return Hypothesis(
        hypothesis_id="hyp-0-1",
        claim="momentum continuation in trending regimes persists over one horizon",
        mechanism="regime-conditional momentum filters flat entry/exit noise",
        feature_plan=feature_plan,
        source=source,
    )


def insight(hyp: Hypothesis, *, best_experiment_id: str = "exp-7") -> CandidateInsight:
    return CandidateInsight(
        hypothesis=hyp,
        evidence=EvidenceSummary(
            hypothesis_id=hyp.hypothesis_id,
            verdict=HypothesisVerdict.PROMISING,
            best_experiment_id=best_experiment_id,
            best_improvement_bps=12.0,
            best_sharpe=1.2,
            samples=500,
            experiment_count=1,
        ),
    )


NOW = datetime(2026, 8, 17, 10, 0, 0, tzinfo=UTC)


class TestProjection:
    def test_birth_record_is_research_without_evidence(self) -> None:
        passport = passport_from_hypothesis(
            hypothesis(),
            passport_id="STRAT-000001",
            dataset_id="btcusdt",
            dataset_version=1,
            created_at=NOW,
        )
        assert passport.status is PassportStatus.RESEARCH
        assert passport.verdict.verdict is EvidenceVerdict.OBSERVE
        assert passport.evidence == {}
        assert passport.trial_count == 0

    def test_claim_text_becomes_hypothesis(self) -> None:
        passport = passport_from_hypothesis(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        assert (
            passport.hypothesis
            == "momentum continuation in trending regimes persists over one horizon"
        )

    def test_source_becomes_model_and_plan_becomes_features(self) -> None:
        passport = passport_from_hypothesis(
            hypothesis(source=HypothesisSource.AI),
            passport_id="STRAT-000001",
            dataset_id="btcusdt",
            dataset_version=1,
        )
        assert passport.model == "ai"
        assert passport.features == ("trend", "momentum")

    def test_no_feature_plan_yields_empty_features(self) -> None:
        passport = passport_from_hypothesis(
            hypothesis(feature_plan=None),
            passport_id="STRAT-000001",
            dataset_id="btcusdt",
            dataset_version=1,
        )
        assert passport.features == ()

    def test_insight_birth_carries_experiment_lineage(self) -> None:
        passport = birth_from_insight(
            insight(hypothesis()),
            passport_id="STRAT-000001",
            dataset_id="btcusdt",
            dataset_version=1,
            created_at=NOW,
        )
        assert passport.experiment_id == "exp-7"
        # the evidence summary is not copied into the payload
        assert passport.evidence == {}

    def test_plain_birth_has_no_lineage(self) -> None:
        passport = passport_from_hypothesis(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        assert passport.experiment_id is None

    def test_serialisation_roundtrip(self) -> None:
        original = passport_from_hypothesis(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        rebuilt = type(original).from_dict(original.as_dict())
        assert rebuilt.as_dict() == original.as_dict()

    def test_invalid_args_rejected(self) -> None:
        with pytest.raises(ValueError):
            passport_from_hypothesis(
                hypothesis(), passport_id="", dataset_id="d", dataset_version=1
            )
        with pytest.raises(ValueError):
            passport_from_hypothesis(
                hypothesis(), passport_id="S", dataset_id="", dataset_version=1
            )
        with pytest.raises(ValueError):
            passport_from_hypothesis(
                hypothesis(), passport_id="S", dataset_id="d", dataset_version=0
            )


class TestBirthService:
    def test_birth_persists_through_ledger(self, tmp_path) -> None:
        store = SqlitePassportRepository(Database(tmp_path / "birth.db"))
        service = HypothesisBirthService(store)
        born = service.birth(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        stored = store.load_passport("STRAT-000001")
        assert stored is not None
        assert stored.as_dict() == born.as_dict()
        assert stored.status is PassportStatus.RESEARCH

    def test_duplicate_birth_refused(self, tmp_path) -> None:
        store = SqlitePassportRepository(Database(tmp_path / "birth2.db"))
        service = HypothesisBirthService(store)
        service.birth(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        with pytest.raises(ValueError):
            service.birth(
                hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
            )
