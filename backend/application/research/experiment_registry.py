# backend/application/research/experiment_registry.py
"""Research experiment registry service (task P1-005).

A thin application-side owner over the :class:`ExperimentStore` port that
applies the research rules the store structurally enables:

- records are registered once, with immutable metadata;
- failed experiments are preserved and retrievable (the registry offers no
  delete path);
- a FINAL_TEST experiment claims its dataset; from that moment the registry
  refuses to register any TUNING/VALIDATION experiment on the same dataset.
  This is a hard gate, not a convention: tuning on final-test data (or vice
  versa) cannot be registered, so the core leakage silently cannot creep in.
"""

from __future__ import annotations

import logging

from backend.application.interfaces.experiment_store import ExperimentStore
from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)

logger = logging.getLogger(__name__)


class ExperimentRegistry:
    """Register, list, and protect research experiments."""

    def __init__(self, store: ExperimentStore) -> None:
        self._store = store

    def register(self, record: ExperimentRecord) -> ExperimentRecord:
        """Persist ``record`` after enforcing final-test data protection.

        Raises
        ------
        ValueError
            If ``record.experiment_id`` already exists, or if the record is a
            TUNING/VALIDATION experiment on a dataset already claimed as
            FINAL_TEST (data protection violation).
        """
        if not record.experiment_id:
            raise ValueError("experiment_id must be non-empty")

        if record.group is ExperimentGroup.FINAL_TEST:
            if self._store.claim_final_test(record.dataset_id):
                logger.info(
                    "Dataset %s claimed as protected final-test data by %s",
                    record.dataset_id,
                    record.experiment_id,
                )
        elif self._store.is_final_test(record.dataset_id):
            raise ValueError(
                f"dataset {record.dataset_id} is protected final-test data; "
                f"{record.group.value} experiment {record.experiment_id} cannot use it"
            )

        self._store.save(record)
        return record

    def complete(
        self,
        experiment_id: str,
        *,
        metrics: dict[str, object],
    ) -> ExperimentRecord:
        """Record a successful run's result and close it as DONE."""
        return self._store.record_result(experiment_id, ExperimentStatus.DONE, metrics=metrics)

    def fail(
        self,
        experiment_id: str,
        *,
        reason: str,
        metrics: dict[str, object] | None = None,
    ) -> ExperimentRecord:
        """Preserve a failed experiment (partial metrics kept, never deleted)."""
        empty: dict[str, object] = {}
        return self._store.record_result(
            experiment_id,
            ExperimentStatus.FAILED,
            metrics=metrics if metrics is not None else empty,
            failure_reason=reason,
        )

    def get(self, experiment_id: str) -> ExperimentRecord | None:
        return self._store.get(experiment_id)

    def list(
        self,
        *,
        group: ExperimentGroup | None = None,
        status: ExperimentStatus | None = None,
    ) -> list[ExperimentRecord]:
        return self._store.list(group=group, status=status)
