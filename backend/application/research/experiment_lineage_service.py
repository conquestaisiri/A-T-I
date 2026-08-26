# backend/application/research/experiment_lineage_service.py
"""Experiment lineage query service (task T1-4-1).

Walks the experiment DAG for audit reports. The DAG edge is
``ExperimentRecord.parent_experiment_id`` (P1-008); children are implicit
(reverse lookup). The service builds a parent->children index from the
store's full listing — audit-scale queries, so a full scan per query is
acceptable and keeps the port unchanged (no new child-lookup method, no
schema index churn).

The walk is always bounded: a visited set stops any cycle in the parent
chain and any diamond in the descendant fan-out, and the report records
what it stopped at rather than looping forever.
"""

from __future__ import annotations

from collections import defaultdict

from backend.application.interfaces.experiment_store import ExperimentStore
from backend.domain.research.experiment import ExperimentRecord
from backend.domain.research.experiment_lineage import ExperimentLineage


class ExperimentLineageService:
    """Query the parent/child experiment DAG for one experiment."""

    def __init__(self, store: ExperimentStore) -> None:
        self._store = store

    def lineage(self, experiment_id: str) -> ExperimentLineage:
        """Return the full lineage walk of one experiment.

        Raises
        ------
        ValueError
            On an unknown experiment id.
        """
        root = self._store.get(experiment_id)
        if root is None:
            raise ValueError(f"experiment {experiment_id} not found")

        ancestors, cycle_ids = self._walk_ancestors(root)
        children = self._children_index()
        descendants = self._walk_descendants(experiment_id, children)

        parent_id = root.parent_experiment_id
        dangling = parent_id is not None and self._store.get(parent_id) is None

        return ExperimentLineage(
            experiment_id=experiment_id,
            ancestors=ancestors,
            descendants=descendants,
            dangling_parent=dangling,
            cycle=bool(cycle_ids),
            cycle_ids=cycle_ids,
        )

    def ancestors(self, experiment_id: str) -> tuple[ExperimentRecord, ...]:
        """The parent chain of one experiment, nearest to furthest."""
        return self.lineage(experiment_id).ancestors

    def descendants(self, experiment_id: str) -> tuple[ExperimentRecord, ...]:
        """Everything that (directly or transitively) names this experiment
        as an ancestor, nearest generation first."""
        return self.lineage(experiment_id).descendants

    # -- internals -----------------------------------------------------------

    def _walk_ancestors(
        self, root: ExperimentRecord
    ) -> tuple[tuple[ExperimentRecord, ...], tuple[str, ...]]:
        """Walk ``parent_experiment_id`` up to the root of the chain.

        Returns the walked records (nearest parent first) and, when the
        chain loops, the ids on the loop. The walk is bounded by the visited
        set, so a cycle cannot run forever.
        """
        walked: list[ExperimentRecord] = []
        visited: set[str] = set()
        cycle_ids: tuple[str, ...] = ()
        current = root.parent_experiment_id
        while current is not None:
            if current in visited:
                cycle_ids = tuple(visited)
                break
            visited.add(current)
            record = self._store.get(current)
            if record is None:
                break  # dangling parent: reported by the caller, not dropped
            walked.append(record)
            current = record.parent_experiment_id
        return tuple(walked), cycle_ids

    def _children_index(self) -> dict[str, list[ExperimentRecord]]:
        """Map every parent id to its direct children in store order."""
        index: dict[str, list[ExperimentRecord]] = defaultdict(list)
        for record in self._store.list():
            if record.parent_experiment_id is not None:
                index[record.parent_experiment_id].append(record)
        return index

    def _walk_descendants(
        self, experiment_id: str, children: dict[str, list[ExperimentRecord]]
    ) -> tuple[ExperimentRecord, ...]:
        """Breadth-first walk of all descendants, nearest generation first.

        A visited set bounds the walk: a diamond (two children sharing a
        grandchild) emits the grandchild once, in the generation it first
        appears.
        """
        result: list[ExperimentRecord] = []
        visited: set[str] = set()
        frontier = list(children.get(experiment_id, ()))
        while frontier:
            generation: list[ExperimentRecord] = []
            for record in frontier:
                if record.experiment_id in visited:
                    continue
                visited.add(record.experiment_id)
                generation.append(record)
            result.extend(generation)
            frontier = [
                child for record in generation for child in children.get(record.experiment_id, ())
            ]
        return tuple(result)


__all__ = ["ExperimentLineageService"]
