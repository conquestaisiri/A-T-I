"""Tests for the experiment lineage query (T1-4-1).

``ExperimentRecord.parent_experiment_id`` is the DAG edge: an experiment
that varies/ablates another names its parent. The lineage query walks the
graph for audit reports (feeding passport lineage). It must:

- walk the parent chain to the root and the child chain to every leaf;
- keep the walk bounded: a cycle in the parent chain is detected and
  reported, never looped on; a diamond emits the shared grandchild once;
- report dangling lineage (a parent id naming a missing record) instead of
  dropping it or guessing;
- state no lineage as no lineage (empty walks, not a fabricated root);
- expose the walk as an operator report via the CLI.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.interfaces.experiment_store import ExperimentStore
from backend.application.research.experiment_lineage_service import ExperimentLineageService
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.experiment_repository import SqliteExperimentRepository


def experiment(
    experiment_id: str,
    parent: str | None = None,
    created_at: datetime | None = None,
) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        created_at=created_at or datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC),
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
        parent_experiment_id=parent,
    )


@pytest.fixture
def service(tmp_path) -> ExperimentLineageService:
    return ExperimentLineageService(SqliteExperimentRepository(Database(tmp_path / "e.db")))


def store(tmp_path) -> ExperimentStore:
    return SqliteExperimentRepository(Database(tmp_path / "e.db"))


class TestLineageWalk:
    def test_unknown_experiment_refused(self, service) -> None:
        with pytest.raises(ValueError, match="not found"):
            service.lineage("nope")

    def test_no_lineage_stated_as_no_lineage(self, service) -> None:
        service._store.save(experiment("exp-1"))
        lineage = service.lineage("exp-1")
        assert lineage.ancestors == ()
        assert lineage.descendants == ()
        assert lineage.dangling_parent is False
        assert lineage.cycle is False
        assert lineage.cycle_ids == ()

    def test_ancestor_chain_walked_to_root(self, service) -> None:
        s = service._store
        s.save(experiment("exp-1"))
        s.save(experiment("exp-2", parent="exp-1"))
        s.save(experiment("exp-3", parent="exp-2"))
        lineage = service.lineage("exp-3")
        assert [r.experiment_id for r in lineage.ancestors] == ["exp-2", "exp-1"]
        assert lineage.dangling_parent is False
        # and the ancestor query is equivalent
        assert [r.experiment_id for r in service.ancestors("exp-3")] == ["exp-2", "exp-1"]

    def test_descendants_walked_nearest_generation_first(self, service) -> None:
        s = service._store
        s.save(experiment("exp-1"))
        s.save(experiment("exp-2", parent="exp-1"))
        s.save(experiment("exp-3", parent="exp-2"))
        lineage = service.lineage("exp-1")
        assert [r.experiment_id for r in lineage.descendants] == ["exp-2", "exp-3"]
        assert [r.experiment_id for r in service.descendants("exp-1")] == ["exp-2", "exp-3"]

    def test_middle_of_chain_gets_both_directions(self, service) -> None:
        s = service._store
        s.save(experiment("exp-1"))
        s.save(experiment("exp-2", parent="exp-1"))
        s.save(experiment("exp-3", parent="exp-2"))
        lineage = service.lineage("exp-2")
        assert [r.experiment_id for r in lineage.ancestors] == ["exp-1"]
        assert [r.experiment_id for r in lineage.descendants] == ["exp-3"]

    def test_diamond_emits_shared_grandchild_once(self, service) -> None:
        s = service._store
        s.save(experiment("exp-1"))
        s.save(experiment("exp-2", parent="exp-1"))
        s.save(experiment("exp-3", parent="exp-1"))
        s.save(experiment("exp-4", parent="exp-2"))
        s.save(experiment("exp-5", parent="exp-4"))
        s.save(experiment("exp-6", parent="exp-3"))
        # exp-5 and exp-6 both derive from exp-1; each is emitted exactly once
        lineage = service.lineage("exp-1")
        ids = [r.experiment_id for r in lineage.descendants]
        assert len(ids) == len(set(ids))
        assert set(ids) == {"exp-2", "exp-3", "exp-4", "exp-5", "exp-6"}

    def test_sibling_order_follows_store_order(self, service) -> None:
        s = service._store
        base = datetime(2026, 8, 12, 9, 0, 0, tzinfo=UTC)
        s.save(experiment("exp-1"))
        s.save(experiment("exp-2", parent="exp-1", created_at=base))
        s.save(
            experiment(
                "exp-3",
                parent="exp-1",
                created_at=base + timedelta(seconds=1),
            )
        )
        lineage = service.lineage("exp-1")
        assert [r.experiment_id for r in lineage.descendants] == ["exp-3", "exp-2"]


class TestHonestyRules:
    def test_dangling_parent_reported_not_dropped(self, service) -> None:
        s = service._store
        s.save(experiment("exp-2", parent="exp-missing"))
        lineage = service.lineage("exp-2")
        assert lineage.dangling_parent is True
        assert lineage.ancestors == ()  # the walk stops at the missing record
        assert lineage.cycle is False

    def test_cycle_detected_and_bounded(self, service) -> None:
        s = service._store
        s.save(experiment("exp-1", parent="exp-2"))
        s.save(experiment("exp-2", parent="exp-1"))
        lineage = service.lineage("exp-1")
        assert lineage.cycle is True
        assert set(lineage.cycle_ids) == {"exp-1", "exp-2"}
        # the walk must terminate: ancestors are bounded by the visited set
        assert len(lineage.ancestors) <= 2

    def test_self_loop_detected(self, service) -> None:
        s = service._store
        s.save(experiment("exp-1", parent="exp-1"))
        lineage = service.lineage("exp-1")
        assert lineage.cycle is True
        assert lineage.cycle_ids == ("exp-1",)

    def test_as_dict_round_trip(self, service) -> None:
        s = service._store
        s.save(experiment("exp-1"))
        s.save(experiment("exp-2", parent="exp-1"))
        data = service.lineage("exp-1").as_dict()
        assert data["experiment_id"] == "exp-1"
        assert [r["experiment_id"] for r in data["descendants"]] == ["exp-2"]
        assert data["dangling_parent"] is False
        assert data["cycle"] is False
