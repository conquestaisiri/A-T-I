# backend/application/research/dataset_service.py
"""Versioned dataset building service (tasks P1-001, P5-002).

The researcher-facing entry point. It turns raw :class:`ObservationEvent`
streams into immutable, content-addressed dataset versions while preserving
the two timestamps that make research honest:

- ``source_timestamp``: when the market event happened;
- ``available_at``: when this system first knew the data (ingestion time for
  live capture; the download time for backfilled data).

The point-in-time rule is enforced structurally: a record's ``available_at``
must be >= its ``source_timestamp``. Downstream labelers and models must only
use records whose ``available_at`` precedes the decision time — this module
provides :meth:`records_available_by` to express that query explicitly.

The research firewall (P5-002) is the second honesty guarantee: before any
model is trained on a period, that period must be claimed as a test set by an
experiment (:meth:`lock_test_period`), after which the store refuses to serve
it as training data — permanently and at data-access time. The service marks
its point-in-time query as ``TRAINING`` so the firewall always guards it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from backend.application.interfaces.dataset_store import DatasetStore
from backend.domain.observation.event import ObservationEvent
from backend.domain.research.dataset import (
    DatasetKind,
    DatasetPurpose,
    DatasetRecord,
    DatasetVersion,
    TestPeriodLock,
    compute_content_hash,
)

logger = logging.getLogger(__name__)


class DatasetService:
    """Build and query immutable historical datasets."""

    def __init__(self, store: DatasetStore) -> None:
        self._store = store

    # -- building -----------------------------------------------------------

    def build_raw_dataset(
        self,
        *,
        dataset_id: str,
        events: Sequence[ObservationEvent],
        metadata: Mapping[str, Any] | None = None,
        available_at: datetime | None = None,
    ) -> DatasetVersion:
        """Freeze a new RAW version of ``dataset_id`` from observation events.

        ``available_at`` is the point-in-time timestamp stamped on every record.
        When omitted it defaults to ``now`` (live capture). For backfilled
        history pass the download time explicitly so the researcher can
        reproduce exactly what was knowable at each point in time.

        The new version number is ``latest + 1`` (or 1 for a new dataset).
        """
        now = available_at or datetime.now(UTC)
        records = [
            DatasetRecord(
                dataset_id=dataset_id,
                source_timestamp=event.timestamp,
                available_at=now,
                payload=event.payload,
                kind=DatasetKind.RAW,
            )
            for event in events
        ]
        records.sort(key=lambda r: r.source_timestamp)
        return self._freeze(
            dataset_id=dataset_id,
            records=records,
            kind=DatasetKind.RAW,
            metadata=metadata,
            created_at=now,
        )

    def build_normalized_dataset(
        self,
        *,
        dataset_id: str,
        records: Sequence[DatasetRecord],
        metadata: Mapping[str, Any] | None = None,
        created_at: datetime | None = None,
    ) -> DatasetVersion:
        """Freeze a new NORMALIZED version from engineered feature records.

        ``records`` must already be ``DatasetRecord`` instances with kind
        :attr:`DatasetKind.NORMALIZED` (the output of the label/feature
        framework in P1-002). Their ``available_at`` must not precede their
        ``source_timestamp`` (point-in-time invariant).
        """
        if not records:
            raise ValueError("cannot build an empty normalized dataset")
        for record in records:
            if record.kind is not DatasetKind.NORMALIZED:
                raise ValueError("normalized dataset accepts only NORMALIZED records")
            if record.available_at < record.source_timestamp:
                raise ValueError(
                    "available_at must not precede source_timestamp "
                    f"(dataset={dataset_id}, source={record.source_timestamp.isoformat()})"
                )
        ordered = sorted(records, key=lambda r: r.source_timestamp)
        return self._freeze(
            dataset_id=dataset_id,
            records=ordered,
            kind=DatasetKind.NORMALIZED,
            metadata=metadata,
            created_at=created_at or datetime.now(UTC),
        )

    # -- querying -------------------------------------------------------------

    def records_available_by(
        self,
        dataset_id: str,
        version: int,
        cutoff: datetime,
        *,
        kind: DatasetKind | None = None,
    ) -> list[DatasetRecord]:
        """Return records that were knowable at or before ``cutoff``.

        This is the explicit point-in-time query: it filters on ``available_at``
        (not ``source_timestamp``), so a label or feature computed from the
        result can never leak future data. Backfilled records are therefore
        only usable once their *download* time has passed, not merely their
        market time.

        The load is declared ``TRAINING``: it builds features and labels that
        influence a model, so the research firewall (P5-002) refuses it if the
        records it would return include any locked test period. Point-in-time
        filtering is delegated to the store (``available_by=cutoff``) so the
        firewall evaluates against exactly what the query would serve, not a
        whole-version approximation.
        """
        return self._store.load_records(
            dataset_id,
            version,
            kind=kind,
            purpose=DatasetPurpose.TRAINING,
            available_by=cutoff,
        )

    def lock_test_period(
        self,
        *,
        dataset_id: str,
        start: datetime,
        end: datetime,
        experiment_id: str,
        claimed_by: str,
        claimed_at: datetime | None = None,
    ) -> None:
        """Claim a source-time period as a locked test set (P5-002).

        Once claimed, the period can never be served as training data: the
        claim is recorded immutably (who, when, which experiment) and the
        store refuses overlapping training loads at data-access time.
        """
        self._store.lock_test_period(
            dataset_id=dataset_id,
            start=start,
            end=end,
            experiment_id=experiment_id,
            claimed_by=claimed_by,
            claimed_at=claimed_at or datetime.now(UTC),
        )

    def test_locks(self, dataset_id: str) -> list[TestPeriodLock]:
        """Return the immutable test-period claims of a dataset."""
        return self._store.list_test_locks(dataset_id)

    def available_versions(self, dataset_id: str) -> list[DatasetVersion]:
        return self._store.list_versions(dataset_id)

    def latest_version(self, dataset_id: str) -> DatasetVersion | None:
        return self._store.latest_version(dataset_id)

    def datasets(self) -> list[str]:
        return self._store.list_datasets()

    # -- internals -----------------------------------------------------------

    def _freeze(
        self,
        *,
        dataset_id: str,
        records: list[DatasetRecord],
        kind: DatasetKind,
        metadata: Mapping[str, Any] | None,
        created_at: datetime,
    ) -> DatasetVersion:
        latest = self._store.latest_version(dataset_id)
        version = (latest.version + 1) if latest is not None else 1
        content_hash = compute_content_hash(records)
        meta = dict(metadata) if metadata else {}
        self._store.append_version(
            dataset_id=dataset_id,
            version=version,
            kind=kind,
            content_hash=content_hash,
            records=records,
            metadata=meta,
            created_at=created_at,
        )
        frozen = self._store.latest_version(dataset_id)
        if frozen is None:
            raise RuntimeError("dataset store failed to return the frozen version")
        logger.info(
            "Froze %s dataset %s v%d (%d records, %s)",
            kind.value,
            dataset_id,
            version,
            len(records),
            content_hash[:12],
        )
        return frozen
