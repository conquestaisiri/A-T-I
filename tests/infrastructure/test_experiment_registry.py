"""Tests for the research experiment registry (P1-005).

The registry must guarantee:

1. Every experiment has immutable metadata — re-registering an id fails and
   never overwrites; results attach without rewriting the config.
2. Failed experiments are preserved — a FAILED record is retrievable and never
   deleted.
3. Final test data is protected — once a dataset is claimed FINAL_TEST, no
   TUNING/VALIDATION experiment on it can be registered.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.research.experiment_registry import ExperimentRegistry
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.experiment_repository import SqliteExperimentRepository


def make_record(
    experiment_id: str = "exp-1",
    dataset: str = "binance-btcusdt",
    group: ExperimentGroup = ExperimentGroup.TUNING,
    status: ExperimentStatus = ExperimentStatus.RUNNING,
) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        created_at=datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC),
        hypothesis="momentum feature adds signal after costs",
        dataset_id=dataset,
        dataset_version=1,
        group=group,
        scorer_name="threshold",
        features=("trend", "momentum"),
        label_definition={"kind": "fixed_horizon", "horizon": 5},
        cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
        metrics={},
        status=status,
    )


@pytest.fixture
def registry(tmp_path) -> ExperimentRegistry:
    store = SqliteExperimentRepository(Database(tmp_path / "experiments.db"))
    return ExperimentRegistry(store)


@pytest.fixture
def store(tmp_path) -> SqliteExperimentRepository:
    return SqliteExperimentRepository(Database(tmp_path / "experiments2.db"))


class TestImmutability:
    def test_register_saves(self, registry):
        registry.register(make_record())
        loaded = registry.get("exp-1")
        assert loaded is not None
        assert loaded.hypothesis == "momentum feature adds signal after costs"
        assert loaded.group is ExperimentGroup.TUNING

    def test_duplicate_id_is_rejected_without_overwrite(self, store):
        a = make_record(experiment_id="dup")
        store.save(a)
        # A different record with the same id must not overwrite the first.
        with pytest.raises(ValueError):
            store.save(make_record(experiment_id="dup", dataset="other"))
        stored = store.get("dup")
        assert stored is not None
        assert stored.dataset_id == "binance-btcusdt"

    def test_registry_rejects_duplicate(self, registry):
        registry.register(make_record())
        with pytest.raises(ValueError):
            registry.register(make_record())

    def test_result_attaches_without_rewriting_config(self, registry):
        registry.register(make_record())
        done = registry.complete("exp-1", metrics={"f1": 0.61, "accuracy": 0.70})
        assert done.status is ExperimentStatus.DONE
        assert done.metrics["f1"] == 0.61
        # Config is byte-identical to what was registered.
        assert done.hypothesis == "momentum feature adds signal after costs"
        assert done.features == ("trend", "momentum")

    def test_round_trip_via_dict(self, store):
        original = make_record()
        store.save(original)
        loaded = store.get("exp-1")
        assert loaded.as_dict() == original.as_dict()

    def test_terminal_record_cannot_be_saved(self, store):
        with pytest.raises(ValueError):
            store.save(make_record(status=ExperimentStatus.DONE))


class TestFailedPreservation:
    def test_failed_experiment_is_preserved(self, registry):
        registry.register(make_record(experiment_id="fail-1"))
        failed = registry.fail("fail-1", reason="scorer raised on missing feature")
        assert failed.status is ExperimentStatus.FAILED
        assert failed.failure_reason == "scorer raised on missing feature"
        loaded = registry.get("fail-1")
        assert loaded is not None
        assert loaded.status is ExperimentStatus.FAILED

    def test_failed_with_partial_metrics(self, registry):
        registry.register(make_record(experiment_id="fail-2"))
        failed = registry.fail(
            "fail-2",
            reason="data window too short",
            metrics={"samples_seen": 3},
        )
        assert failed.metrics == {"samples_seen": 3}
        assert failed.status is ExperimentStatus.FAILED

    def test_list_filters_by_status(self, registry):
        registry.register(make_record(experiment_id="a"))
        registry.register(make_record(experiment_id="b"))
        registry.complete("a", metrics={})
        registry.fail("b", reason="boom")
        done = registry.list(status=ExperimentStatus.DONE)
        failed = registry.list(status=ExperimentStatus.FAILED)
        assert [r.experiment_id for r in done] == ["a"]
        assert [r.experiment_id for r in failed] == ["b"]
        assert len(registry.list()) == 2

    def test_no_backward_transition(self, registry):
        registry.register(make_record(experiment_id="x"))
        registry.fail("x", reason="boom")
        with pytest.raises(ValueError):
            registry.complete("x", metrics={})
        with pytest.raises(ValueError):
            registry.fail("x", reason="again")

    def test_cannot_reopen_running(self, registry):
        registry.register(make_record(experiment_id="y"))
        with pytest.raises(ValueError):
            # Draft guard: no path back from / to RUNNING exists on the store.
            registry.fail("y", reason="noop")
            registry.complete("y", metrics={})


class TestFinalTestProtection:
    def test_claim_is_recorded(self, store):
        assert store.claim_final_test("holdout-eth") is True
        assert store.is_final_test("holdout-eth") is True

    def test_claim_is_idempotent(self, store):
        assert store.claim_final_test("holdout") is True
        assert store.claim_final_test("holdout") is False
        assert store.is_final_test("holdout") is True

    def test_final_test_registration_protects_dataset(self, registry):
        registry.register(
            make_record(experiment_id="final", dataset="holdout", group=ExperimentGroup.FINAL_TEST)
        )
        # Same dataset as TUNING must now be refused.
        with pytest.raises(ValueError, match="protected final-test data"):
            registry.register(make_record(experiment_id="tuner", dataset="holdout"))

    def test_tuning_allowed_on_unprotected_data(self, registry):
        registry.register(make_record(experiment_id="t1", dataset="free-data"))
        registry.register(make_record(experiment_id="t2", dataset="free-data"))
        assert len(registry.list()) == 2

    def test_multiple_final_tests_share_protection(self, registry):
        registry.register(
            make_record(experiment_id="f1", dataset="shared", group=ExperimentGroup.FINAL_TEST)
        )
        registry.register(
            make_record(experiment_id="f2", dataset="shared", group=ExperimentGroup.FINAL_TEST)
        )
        with pytest.raises(ValueError):
            registry.register(make_record(experiment_id="t", dataset="shared"))

    def test_validation_also_blocked(self, registry):
        registry.register(
            make_record(experiment_id="final", dataset="v", group=ExperimentGroup.FINAL_TEST)
        )
        with pytest.raises(ValueError, match="protected final-test data"):
            registry.register(
                make_record(experiment_id="val", dataset="v", group=ExperimentGroup.VALIDATION)
            )


class TestRoundTrip:
    def test_persisted_record_survives_new_connection(self, tmp_path):
        path = tmp_path / "persist.db"
        store = SqliteExperimentRepository(Database(path))
        store.save(make_record())
        store.record_result(
            "exp-1",
            ExperimentStatus.DONE,
            metrics={"accuracy": 0.5},
        )
        # A fresh connection sees the completed experiment.
        reopened = SqliteExperimentRepository(Database(path))
        loaded = reopened.get("exp-1")
        assert loaded is not None
        assert loaded.status is ExperimentStatus.DONE
        assert loaded.metrics["accuracy"] == 0.5
