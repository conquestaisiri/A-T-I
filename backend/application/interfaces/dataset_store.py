# backend/application/interfaces/dataset_store.py
"""Port for the versioned dataset store (tasks P1-001, P5-002).

The store persists immutable, content-addressed dataset versions and the
records they contain. Implementations must guarantee:

- a frozen version is never mutated or deleted;
- ``append_version`` is atomic (all-or-nothing) and rejects a hash mismatch
  against the caller-computed hash, so a stored snapshot is provably the
  bytes the researcher approved;
- reads return records sorted by ``source_timestamp`` for deterministic
  downstream consumption.

Research firewall ("locked test is dead", task P5-002)
------------------------------------------------------
Once a source-time period is claimed as a test set
(``lock_test_period``), that period can never again be served as training
data — the claim is recorded immutably (who, when, which experiment) and
violations are refused *at data-access time*, not merely at validation
time. Concretely:

- ``lock_test_period`` records the claim; it is append-only and can never
  be amended or deleted;
- ``load_records`` takes an explicit ``purpose``. Training loads (the
  default, fail-safe) are refused when the requested source-time window
  overlaps any lock of the dataset. Test loads are always served — the
  locked period must still be readable by the experiment that owns it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

from backend.domain.research.dataset import (
    DatasetKind,
    DatasetPurpose,
    DatasetRecord,
    DatasetVersion,
    TestPeriodLock,
)


class DatasetStore(ABC):
    """Contract for persisting and reading versioned datasets."""

    @abstractmethod
    def append_version(
        self,
        *,
        dataset_id: str,
        version: int,
        kind: DatasetKind,
        content_hash: str,
        records: list[DatasetRecord],
        metadata: dict[str, object],
        created_at: datetime,
    ) -> None:
        """Freeze a new dataset version atomically.

        Raises ``ValueError`` if a version with the same id+version already
        exists (immutability), or if ``content_hash`` does not match the
        computed hash of ``records``.
        """
        raise NotImplementedError

    @abstractmethod
    def lock_test_period(
        self,
        *,
        dataset_id: str,
        start: datetime,
        end: datetime,
        experiment_id: str,
        claimed_by: str,
        claimed_at: datetime,
    ) -> None:
        """Claim a source-time period as a locked test set (append-only).

        The claim is recorded immutably: who (``claimed_by``), when
        (``claimed_at``) and which experiment (``experiment_id``). Once
        recorded, training loads overlapping ``[start, end]`` are refused
        at data-access time. Raises ``ValueError`` on an invalid claim
        (empty ids, ``start > end``, naive datetimes).
        """
        raise NotImplementedError

    @abstractmethod
    def list_test_locks(self, dataset_id: str) -> list[TestPeriodLock]:
        """Return all test-period locks of a dataset, newest first."""
        raise NotImplementedError

    @abstractmethod
    def list_versions(self, dataset_id: str) -> list[DatasetVersion]:
        """Return all frozen versions of a dataset, newest first."""
        raise NotImplementedError

    @abstractmethod
    def latest_version(self, dataset_id: str) -> DatasetVersion | None:
        """Return the most recent frozen version, or None if none exists."""
        raise NotImplementedError

    @abstractmethod
    def load_records(
        self,
        dataset_id: str,
        version: int,
        *,
        kind: DatasetKind | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        purpose: DatasetPurpose = DatasetPurpose.TRAINING,
        available_by: datetime | None = None,
    ) -> list[DatasetRecord]:
        """Return the records of a frozen version, ordered by source time.

        Optional filters select a record kind, a source-time window, or
        point-in-time availability (``available_by`` filters on
        ``available_at <= available_by``).

        ``purpose`` is the firewall switch (task P5-002): a ``TRAINING`` load
        is refused with ``ValueError`` when the records it would return
        include any source-time period locked as a test set of this dataset —
        the check is evaluated against the exact scope of the load (kind,
        window and ``available_by``), so a point-in-time query that cannot
        reach the locked data is served. A ``TEST`` load is always served.
        """
        raise NotImplementedError

    @abstractmethod
    def list_datasets(self) -> list[str]:
        """Return all dataset ids known to the store."""
        raise NotImplementedError
