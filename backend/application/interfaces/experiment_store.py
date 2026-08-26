# backend/application/interfaces/experiment_store.py
"""Port for the research experiment registry (task P1-005).

The store persists immutable :class:`ExperimentRecord` values and enforces
final-test dataset protection. Implementations must guarantee:

- a record is saved once: registering the same ``experiment_id`` twice is an
  error, and an existing record is never overwritten;
- a status transition does not reopen a closed record; only forward moves
  ``RUNNING -> DONE/FAILED/ABORTED`` are accepted;
- once a dataset is claimed as final test, ``is_final_test`` reports true and
  the claim is never revoked;
- failed experiments are retrievable exactly like successful ones (no delete
  path in the interface).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.research.experiment import (
    ExperimentGroup,
    ExperimentRecord,
    ExperimentStatus,
)


class ExperimentStore(ABC):
    """Contract for persisting immutable experiment records."""

    @abstractmethod
    def save(self, record: ExperimentRecord) -> None:
        """Persist a new experiment record.

        Raises ``ValueError`` if ``experiment_id`` already exists, or if the
        record is already in a terminal state at save time.
        """
        raise NotImplementedError

    @abstractmethod
    def get(self, experiment_id: str) -> ExperimentRecord | None:
        """Return the record for ``experiment_id`` or None."""
        raise NotImplementedError

    @abstractmethod
    def list(
        self,
        *,
        group: ExperimentGroup | None = None,
        status: ExperimentStatus | None = None,
    ) -> list[ExperimentRecord]:
        """Return matching records, newest first."""
        raise NotImplementedError

    @abstractmethod
    def set_status(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        failure_reason: str | None = None,
    ) -> ExperimentRecord:
        """Move a running record to a terminal status.

        Raises ``ValueError`` for an unknown id, a backward transition, or a
        terminal-to-terminal rewrite.
        """
        raise NotImplementedError

    @abstractmethod
    def record_result(
        self,
        experiment_id: str,
        status: ExperimentStatus,
        *,
        metrics: dict[str, object],
        failure_reason: str | None = None,
    ) -> ExperimentRecord:
        """Close a running record with its result payload.

        ``metrics`` captures the experiment's outcome (report dicts from the
        other research modules). The record's config fields stay immutable;
        only the status, metrics, and (for failures) the failure reason change.
        Raises ``ValueError`` for an unknown id, a backward transition, or a
        terminal-to-terminal rewrite.
        """
        raise NotImplementedError

    @abstractmethod
    def claim_final_test(self, dataset_id: str) -> bool:
        """Claim ``dataset_id`` as protected final-test data.

        Returns ``True`` the first time a dataset is claimed, ``False`` on
        repeated claims (idempotent; never raises).
        """
        raise NotImplementedError

    @abstractmethod
    def is_final_test(self, dataset_id: str) -> bool:
        """Whether ``dataset_id`` is already protected as final test."""
        raise NotImplementedError
