# backend/domain/research/leak_detector.py
"""Runtime leak-detector contracts (tasks T1-3-1 / T1-5-1).

The research firewall (P5-002) refuses a TRAINING load that would touch a
locked test period at data-access time. The leak-detector is the runtime
counterpart: an operator-facing audit that re-probes the firewall for every
frozen version and reports, per version, whether locked periods overlap it,
whether the firewall actually refused, and how many records the locks
protect.

Design rule (T1-5-1: single implementation, one owner): the detector never
re-implements the overlap/refusal math. It drives the store's own
``load_records`` with ``purpose=TRAINING`` (the firewall is the single owner
of the refusal decision) and with ``purpose=AUDIT`` (always served, labelled
— the same discipline as the quality scanner) to count protected records.
A ``LEAK`` finding therefore means the firewall itself failed to refuse,
which is exactly the failure the detector exists to catch.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.domain.research.dataset import TestPeriodLock


class LeakFindingKind(enum.StrEnum):
    """What a leak-detector finding reports.

    ``LEAK`` — a version whose records overlap a locked test period was
    served by a TRAINING load (the firewall failed to refuse). Critical.
    ``DEAD_LOCK`` — a lock claim whose window contains no records in any
    version of the dataset (the claim protects nothing; likely a mistyped
    window). Warning, not a leak.
    """

    LEAK = "leak"
    DEAD_LOCK = "dead_lock"


@dataclass(frozen=True, slots=True)
class LockCoverage:
    """One lock and how many records it actually protects (across versions).

    Attributes
    ----------
    lock: TestPeriodLock
        The immutable claim.
    protected_record_count: int
        Records of any version whose source time falls inside the lock
        window (counted with a labelled ``AUDIT`` load).
    """

    lock: TestPeriodLock
    protected_record_count: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "lock": self.lock.as_dict(),
            "protected_record_count": self.protected_record_count,
        }


@dataclass(frozen=True, slots=True)
class VersionAudit:
    """The leak status of one frozen version.

    Attributes
    ----------
    dataset_id: str
        Dataset the version belongs to.
    version: int
        Version number.
    record_count: int
        Records in this version.
    source_start, source_end: datetime
        Source-time window of the version.
    overlapping_locks: tuple[TestPeriodLock, ...]
        Locks whose window overlaps the version's source window.
    firewall_refused_training: bool
        Whether the firewall refused a default (unbounded) TRAINING load of
        this version. This is the firewall's own decision, probed directly.
    locked_record_count: int
        Records of this version that fall inside the overlapping locks'
        windows (labelled AUDIT load).
    """

    dataset_id: str
    version: int
    record_count: int
    source_start: datetime
    source_end: datetime
    overlapping_locks: tuple[TestPeriodLock, ...]
    firewall_refused_training: bool
    locked_record_count: int

    @property
    def leak(self) -> bool:
        """True when locked periods overlap this version yet the firewall
        served a TRAINING load — the firewall failed."""
        return bool(self.overlapping_locks) and not self.firewall_refused_training

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "version": self.version,
            "record_count": self.record_count,
            "source_start": self.source_start.isoformat(timespec="milliseconds"),
            "source_end": self.source_end.isoformat(timespec="milliseconds"),
            "overlapping_locks": [lock.as_dict() for lock in self.overlapping_locks],
            "firewall_refused_training": self.firewall_refused_training,
            "locked_record_count": self.locked_record_count,
            "leak": self.leak,
        }


@dataclass(frozen=True, slots=True)
class LeakFinding:
    """One leak-detector finding.

    Attributes
    ----------
    kind: LeakFindingKind
        LEAK or DEAD_LOCK.
    dataset_id: str
        Dataset the finding belongs to.
    lock: TestPeriodLock
        The lock involved (for DEAD_LOCK, the claim that protects nothing).
    version: int | None
        Version involved (None for a DEAD_LOCK, which is dataset-wide).
    detail: str
        Human-readable explanation.
    """

    kind: LeakFindingKind
    dataset_id: str
    lock: TestPeriodLock
    version: int | None
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "dataset_id": self.dataset_id,
            "lock": self.lock.as_dict(),
            "version": self.version,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class LeakAuditReport:
    """The bounded result of one leak audit of a dataset.

    Attributes
    ----------
    dataset_id: str
        Dataset audited.
    versions: tuple[VersionAudit, ...]
        One entry per frozen version, newest first.
    coverages: tuple[LockCoverage, ...]
        Every lock with the records it protects (across all versions).
    findings: tuple[LeakFinding, ...]
        LEAK findings (firewall bypasses) then DEAD_LOCK findings, each
        bounded by the real lock/version count of the dataset.
    """

    dataset_id: str
    versions: tuple[VersionAudit, ...]
    coverages: tuple[LockCoverage, ...]
    findings: tuple[LeakFinding, ...]

    @property
    def leaks(self) -> tuple[LeakFinding, ...]:
        """Only the critical firewall-bypass findings."""
        return tuple(f for f in self.findings if f.kind is LeakFindingKind.LEAK)

    @property
    def dead_locks(self) -> tuple[LeakFinding, ...]:
        """Only the warning findings (claims protecting nothing)."""
        return tuple(f for f in self.findings if f.kind is LeakFindingKind.DEAD_LOCK)

    @property
    def clean(self) -> bool:
        """True when there are no findings at all."""
        return not self.findings

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "versions": [v.as_dict() for v in self.versions],
            "coverages": [c.as_dict() for c in self.coverages],
            "findings": [f.as_dict() for f in self.findings],
            "leaks": [f.as_dict() for f in self.leaks],
            "dead_locks": [f.as_dict() for f in self.dead_locks],
            "clean": self.clean,
        }


__all__ = [
    "LeakAuditReport",
    "LeakFinding",
    "LeakFindingKind",
    "LockCoverage",
    "VersionAudit",
]
