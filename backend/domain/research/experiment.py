# backend/domain/research/experiment.py
"""Research experiment registry contracts (task P1-005).

The registry is the memory of the research factory. Every experiment —
successful or failed — is recorded with immutable metadata so that:

- **every experiment has immutable metadata**: an ``ExperimentRecord`` is a
  frozen value object. Once registered it can never be rewritten; the only
  permitted lifecycle change is a status transition (running -> done/failed).
- **failed experiments are preserved**: a FAILED record is stored and
  retrievable exactly like a successful one. The registry has no delete
  path; failed research is evidence, and the queue forbids discarding it.
- **final test data is protected**: a dataset designated FINAL_TEST is
  claimed by the registry and can no longer be referenced by a tuning or
  validation experiment. This is the hard boundary that prevents the core
  leakage: tuning on the data you will later test on.

The record mirrors the other research contracts: it is serialisable to a
plain dictionary so the sqlite repository can persist it faithfully and the
robustness harness (P1-008) can replay experiments unchanged.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


class ExperimentGroup(enum.StrEnum):
    """What an experiment is allowed to do with its data.

    TUNING and VALIDATION may freely iterate over their datasets. FINAL_TEST
    is the once-and-only evaluation of a claim; once claimed, the dataset is
    protected from all tuning/validation reuse.
    """

    TUNING = "tuning"
    VALIDATION = "validation"
    FINAL_TEST = "final_test"


class ExperimentStatus(enum.StrEnum):
    """Lifecycle of an experiment record.

    Only forward transitions RUNNING -> DONE/FAILED/ABORTED are legal; a
    record cannot go back to running and cannot be deleted.
    """

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    ABORTED = "aborted"


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    """One immutable research experiment.

    Parameters
    ----------
    experiment_id: str
        Unique, caller-assigned identifier.
    created_at: datetime
        When the experiment was registered (aware UTC).
    hypothesis: str
        The claim being investigated.
    dataset_id, dataset_version:
        The frozen dataset/version the experiment ran on (P1-001).
    group: ExperimentGroup
        TUNING/VALIDATION/FINAL_TEST; decides data-protection behaviour.
    scorer_name: str
        Identifier of the model or scorer run (P1-004 FeatureScorer).
    features: tuple[str, ...]
        The feature keys the scorer received.
    label_definition: Mapping[str, Any]
        The exact :class:`LabelDefinition.as_dict()` used (P1-002).
    cost_model: Mapping[str, Any]
        The exact cost assumptions (P1-003/004), reproduced for audit.
    metrics: Mapping[str, Any]
        The result payload (e.g. an ``AttributionReport`` or baseline
        result as a dict). Empty until the experiment finishes.
    status: ExperimentStatus
        Lifecycle state; RUNNING on registration.
    parent_experiment_id: str | None
        Lineage: the experiment this one varies/ablates (P1-008).
    failure_reason: str | None
        Why a FAILED experiment failed; preserved as evidence.
    """

    experiment_id: str
    created_at: datetime
    hypothesis: str
    dataset_id: str
    dataset_version: int
    group: ExperimentGroup
    scorer_name: str
    features: tuple[str, ...]
    label_definition: Mapping[str, Any]
    cost_model: Mapping[str, Any]
    metrics: Mapping[str, Any]
    status: ExperimentStatus = ExperimentStatus.RUNNING
    parent_experiment_id: str | None = None
    failure_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise the record to a plain dictionary (round-trips)."""
        return {
            "experiment_id": self.experiment_id,
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
            "hypothesis": self.hypothesis,
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "group": self.group.value,
            "scorer_name": self.scorer_name,
            "features": list(self.features),
            "label_definition": dict(self.label_definition),
            "cost_model": dict(self.cost_model),
            "metrics": dict(self.metrics),
            "status": self.status.value,
            "parent_experiment_id": self.parent_experiment_id,
            "failure_reason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ExperimentRecord:
        """Reconstruct a record from :meth:`as_dict` output."""
        return cls(
            experiment_id=str(data["experiment_id"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            hypothesis=str(data["hypothesis"]),
            dataset_id=str(data["dataset_id"]),
            dataset_version=int(data["dataset_version"]),
            group=ExperimentGroup(str(data["group"])),
            scorer_name=str(data["scorer_name"]),
            features=tuple(str(f) for f in data["features"]),
            label_definition=dict(data["label_definition"]),
            cost_model=dict(data["cost_model"]),
            metrics=dict(data["metrics"]),
            status=ExperimentStatus(str(data["status"])),
            parent_experiment_id=(
                str(data["parent_experiment_id"])
                if data.get("parent_experiment_id") is not None
                else None
            ),
            failure_reason=str(data["failure_reason"]) if data.get("failure_reason") else None,
        )
