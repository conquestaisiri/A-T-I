# backend/domain/research/experiment_lineage.py
"""Experiment lineage query contracts (task T1-4-1).

``ExperimentRecord.parent_experiment_id`` is the dormant DAG edge (P1-008):
an experiment that varies/ablates another names its parent. This module
defines the audit report a lineage walk produces. Honesty rules (mirroring
``passport_provenance.py``):

- dangling lineage is reported, never dropped: a ``parent_experiment_id``
  that names a missing record shows up as ``dangling_parent``;
- a cycle in the parent chain is reported, never looped on: the walk is
  bounded and the report states which ids participate;
- no lineage is stated as no lineage: an experiment without a parent gets
  empty ancestor/descendant walks, not a guessed root.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.domain.research.experiment import ExperimentRecord


@dataclass(frozen=True, slots=True)
class ExperimentLineage:
    """The lineage walk of one experiment.

    Attributes
    ----------
    experiment_id: str
        The experiment whose lineage was queried.
    ancestors: tuple[ExperimentRecord, ...]
        Parent chain from nearest to furthest (root last). Empty when the
        experiment has no parent.
    descendants: tuple[ExperimentRecord, ...]
        Children chain, nearest generation first, each generation in the
        store's deterministic order (``created_at`` DESC, ``id`` DESC).
        Empty when nothing names this experiment as parent.
    dangling_parent: bool
        True when ``parent_experiment_id`` is set but the named record does
        not exist in the registry.
    cycle: bool
        True when the parent chain contains a cycle (an ancestor eventually
        names a descendant as its parent). The walk stops at the first
        repeat; ``cycle_ids`` lists the ids on the loop.
    cycle_ids: tuple[str, ...]
        The ids participating in a detected cycle, in walk order.
    """

    experiment_id: str
    ancestors: tuple[ExperimentRecord, ...]
    descendants: tuple[ExperimentRecord, ...]
    dangling_parent: bool
    cycle: bool
    cycle_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "ancestors": [record.as_dict() for record in self.ancestors],
            "descendants": [record.as_dict() for record in self.descendants],
            "dangling_parent": self.dangling_parent,
            "cycle": self.cycle,
            "cycle_ids": list(self.cycle_ids),
        }


__all__ = ["ExperimentLineage"]
