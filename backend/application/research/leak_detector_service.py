# backend/application/research/leak_detector_service.py
"""Runtime leak-detector service (tasks T1-3-1 / T1-5-1).

The research firewall (P5-002) refuses TRAINING loads that would touch a
locked test period. This service is the runtime audit surface: it probes the
firewall itself for every frozen version (the store's ``load_records`` with
``purpose=TRAINING`` is the single owner of the refusal decision — the
detector never re-implements the overlap math), counts protected records
with labelled ``AUDIT`` loads (same discipline as the quality scanner), and
reports:

- per version: whether locked periods overlap it, whether the firewall
  actually refused, how many records the locks protect;
- per lock: how many records it protects across all versions;
- findings: ``LEAK`` when a TRAINING load was served despite overlapping
  locks (the firewall failed — critical), ``DEAD_LOCK`` when a claim
  protects no records at all (warning).

The audit never mutates the store.
"""

from __future__ import annotations

from datetime import datetime

from backend.application.interfaces.dataset_store import DatasetStore
from backend.domain.research.dataset import (
    DatasetPurpose,
    DatasetVersion,
    TestPeriodLock,
)
from backend.domain.research.leak_detector import (
    LeakAuditReport,
    LeakFinding,
    LeakFindingKind,
    LockCoverage,
    VersionAudit,
)


class LeakDetectorService:
    """Audit every dataset read for TRAINING purpose vs locks (T1-3-1)."""

    def __init__(self, store: DatasetStore) -> None:
        self._store = store

    def audit(self, dataset_id: str) -> LeakAuditReport:
        """Audit one dataset: probe the firewall for every version.

        Raises
        ------
        ValueError
            On an unknown dataset id.
        """
        versions = self._store.list_versions(dataset_id)
        if not versions:
            raise ValueError(f"dataset {dataset_id} not found")

        locks = self._store.list_test_locks(dataset_id)

        version_audits = tuple(
            self._audit_version(dataset_id, versions, version.version, locks)
            for version in versions
        )

        coverages = tuple(
            LockCoverage(
                lock=lock,
                protected_record_count=self._count_protected(dataset_id, lock),
            )
            for lock in locks
        )

        findings: list[LeakFinding] = []
        for audit in version_audits:
            if audit.leak:
                for lock in audit.overlapping_locks:
                    findings.append(
                        LeakFinding(
                            kind=LeakFindingKind.LEAK,
                            dataset_id=dataset_id,
                            lock=lock,
                            version=audit.version,
                            detail=(
                                f"v{audit.version} overlaps a locked test period "
                                f"[{lock.start.isoformat()}..{lock.end.isoformat()}] but "
                                "the firewall served a TRAINING load — firewall bypass"
                            ),
                        )
                    )
        for coverage in coverages:
            if coverage.protected_record_count == 0:
                findings.append(
                    LeakFinding(
                        kind=LeakFindingKind.DEAD_LOCK,
                        dataset_id=dataset_id,
                        lock=coverage.lock,
                        version=None,
                        detail=(
                            f"lock [{coverage.lock.start.isoformat()}.."
                            f"{coverage.lock.end.isoformat()}] protects no records "
                            "in any version — claim covers nothing"
                        ),
                    )
                )

        return LeakAuditReport(
            dataset_id=dataset_id,
            versions=version_audits,
            coverages=coverages,
            findings=tuple(findings),
        )

    def audit_all(self) -> tuple[LeakAuditReport, ...]:
        """Audit every dataset known to the store."""
        return tuple(self.audit(dataset_id) for dataset_id in self._store.list_datasets())

    # -- internals -----------------------------------------------------------

    def _audit_version(
        self,
        dataset_id: str,
        versions: list[DatasetVersion],
        version: int,
        locks: list[TestPeriodLock],
    ) -> VersionAudit:
        meta = next(v for v in versions if v.version == version)

        # A lock is "overlapping" only when the version actually has records
        # inside it — the same record-level condition the firewall checks. A
        # lock that merely grazes the version's source window (no records in
        # it) is not an overlap: the firewall correctly serves it, and the
        # detector must not report a false leak. Counted with labelled AUDIT
        # loads so the scan itself never touches the firewall.
        overlapping: list[TestPeriodLock] = []
        locked_count = 0
        for lock in locks:
            if not _overlaps(meta.source_start, meta.source_end, lock.start, lock.end):
                continue
            inside = self._store.load_records(
                dataset_id,
                version,
                start=lock.start,
                end=lock.end,
                purpose=DatasetPurpose.AUDIT,
            )
            if inside:
                overlapping.append(lock)
                locked_count += len(inside)

        # Probe the firewall: the store itself decides (single owner of the
        # refusal logic). A default unbounded TRAINING load either succeeds
        # or is refused with ValueError. If the firewall is healthy it refuses
        # exactly when there is an overlapping lock — anything else is a leak.
        refused = False
        try:
            self._store.load_records(
                dataset_id,
                version,
                purpose=DatasetPurpose.TRAINING,
            )
        except ValueError:
            refused = True

        return VersionAudit(
            dataset_id=dataset_id,
            version=version,
            record_count=meta.record_count,
            source_start=meta.source_start,
            source_end=meta.source_end,
            overlapping_locks=tuple(overlapping),
            firewall_refused_training=refused,
            locked_record_count=locked_count,
        )

    def _count_protected(self, dataset_id: str, lock: TestPeriodLock) -> int:
        """Records of any version whose source time falls inside the lock."""
        total = 0
        for meta in self._store.list_versions(dataset_id):
            if _overlaps(meta.source_start, meta.source_end, lock.start, lock.end):
                total += len(
                    self._store.load_records(
                        dataset_id,
                        meta.version,
                        start=lock.start,
                        end=lock.end,
                        purpose=DatasetPurpose.AUDIT,
                    )
                )
        return total


def _overlaps(
    window_start: datetime,
    window_end: datetime,
    lock_start: datetime,
    lock_end: datetime,
) -> bool:
    """Closed-interval overlap between a version window and a lock window.

    Mirrors the firewall's ``BETWEEN`` semantics (inclusive bounds).
    """
    return window_start <= lock_end and lock_start <= window_end


__all__ = ["LeakDetectorService"]
