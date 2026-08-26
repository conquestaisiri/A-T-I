"""Tests for experiment <-> passport provenance linkage (T3-22-1).

The linkage must be verifiable end to end: a passport names its parent
experiment, the parent is retrievable from the registry, children are
derived from the ledger (never a second copy), and dangling or missing
lineage is reported explicitly — never dropped, never guessed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.research.hypothesis_passport import HypothesisBirthService
from backend.application.research.passport_provenance import PassportProvenanceService
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)
from backend.domain.research.hypothesis import Hypothesis
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.experiment_repository import SqliteExperimentRepository
from backend.infrastructure.sqlite.passport_repository import SqlitePassportRepository


def experiment(experiment_id: str = "exp-1") -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        created_at=datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC),
        hypothesis="momentum feature adds signal after costs",
        dataset_id="binance-btcusdt",
        dataset_version=1,
        group=ExperimentGroup.TUNING,
        scorer_name="threshold",
        features=("trend", "momentum"),
        label_definition={"kind": "fixed_horizon", "horizon": 5},
        cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
        metrics={},
        status=ExperimentStatus.RUNNING,
    )


def hypothesis() -> Hypothesis:
    return Hypothesis(
        hypothesis_id="hyp-0-1",
        claim="momentum continuation in trending regimes persists over one horizon",
        mechanism="regime-conditional momentum filters flat entry/exit noise",
        feature_plan=("trend", "momentum"),
    )


def service(tmp_path) -> PassportProvenanceService:
    return PassportProvenanceService(
        SqlitePassportRepository(Database(tmp_path / "p.db")),
        SqliteExperimentRepository(Database(tmp_path / "e.db")),
    )


class TestPassportToExperiment:
    def test_linked_passport_finds_parent(self, tmp_path) -> None:
        svc = service(tmp_path)
        registry = SqliteExperimentRepository(Database(tmp_path / "e.db"))
        registry.save(experiment("exp-1"))
        HypothesisBirthService(SqlitePassportRepository(Database(tmp_path / "p.db"))).birth(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        # stamp lineage: issue-time wiring sets the passport's experiment id
        store = SqlitePassportRepository(Database(tmp_path / "p.db"))
        born = store.load_passport("STRAT-000001")
        assert born is not None
        store.replace_passport(type(born).from_dict({**born.as_dict(), "experiment_id": "exp-1"}))

        provenance = svc.provenance("STRAT-000001")
        assert provenance.link_ok is True
        assert provenance.experiment_id == "exp-1"
        assert provenance.reason == ""
        assert provenance.parent is not None
        assert provenance.parent.hypothesis == "momentum feature adds signal after costs"
        assert svc.linked_experiment_status("STRAT-000001") is ExperimentStatus.RUNNING

    def test_dangling_lineage_reported_not_dropped(self, tmp_path) -> None:
        svc = service(tmp_path)
        HypothesisBirthService(SqlitePassportRepository(Database(tmp_path / "p.db"))).birth(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        store = SqlitePassportRepository(Database(tmp_path / "p.db"))
        born = store.load_passport("STRAT-000001")
        assert born is not None
        store.replace_passport(
            type(born).from_dict({**born.as_dict(), "experiment_id": "exp-gone"})
        )

        provenance = svc.provenance("STRAT-000001")
        assert provenance.link_ok is False
        assert provenance.experiment_id == "exp-gone"
        assert "not found in the registry" in provenance.reason
        assert provenance.parent is None

    def test_no_lineage_stated_as_no_lineage(self, tmp_path) -> None:
        svc = service(tmp_path)
        HypothesisBirthService(SqlitePassportRepository(Database(tmp_path / "p.db"))).birth(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        provenance = svc.provenance("STRAT-000001")
        assert provenance.link_ok is False
        assert provenance.reason == "passport carries no experiment lineage"

    def test_unknown_passport_refused(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="unknown passport"):
            service(tmp_path).provenance("STRAT-999")


class TestExperimentToPassports:
    def test_children_derived_from_ledger(self, tmp_path) -> None:
        svc = service(tmp_path)
        store = SqlitePassportRepository(Database(tmp_path / "p.db"))
        births = HypothesisBirthService(store)
        births.birth(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        births.birth(
            hypothesis(), passport_id="STRAT-000002", dataset_id="btcusdt", dataset_version=1
        )
        births.birth(
            hypothesis(), passport_id="STRAT-000003", dataset_id="btcusdt", dataset_version=1
        )
        for passport_id, experiment_id in (
            ("STRAT-000001", "exp-1"),
            ("STRAT-000003", "exp-1"),
        ):
            born = store.load_passport(passport_id)
            assert born is not None
            store.replace_passport(
                type(born).from_dict({**born.as_dict(), "experiment_id": experiment_id})
            )

        children = svc.children("exp-1")
        assert children.child_passport_ids == ("STRAT-000001", "STRAT-000003")
        assert svc.children("exp-none").child_passport_ids == ()

    def test_serialisation_roundtrip(self, tmp_path) -> None:
        svc = service(tmp_path)
        SqliteExperimentRepository(Database(tmp_path / "e.db")).save(experiment("exp-1"))
        store = SqlitePassportRepository(Database(tmp_path / "p.db"))
        births = HypothesisBirthService(store)
        births.birth(
            hypothesis(), passport_id="STRAT-000001", dataset_id="btcusdt", dataset_version=1
        )
        born = store.load_passport("STRAT-000001")
        assert born is not None
        store.replace_passport(type(born).from_dict({**born.as_dict(), "experiment_id": "exp-1"}))

        payload = svc.provenance("STRAT-000001").as_dict()
        assert payload["link_ok"] is True
        assert payload["experiment_id"] == "exp-1"
        assert payload["parent"]["experiment_id"] == "exp-1"
        assert set(payload) == {"passport_id", "experiment_id", "link_ok", "reason", "parent"}
