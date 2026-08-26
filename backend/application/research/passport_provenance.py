# backend/application/research/passport_provenance.py
"""Experiment <-> passport provenance linkage (task T3-22-1).

Every passport records its parent experiment id (``experiment_id``, set
at issue time by the evidence engine and at birth by the hypothesis
service). This module is the read-side seam that makes that linkage
*verifiable*: given a passport, find its parent experiment record; given
an experiment, find every passport that derives from it.

Honesty rules
-------------
- **Projection only, no second copy.** Children are derived from
  ``PassportStore.all_passports()`` at read time; nothing here persists
  linkage, so it cannot drift from the ledger.
- **A dangling lineage is reported, not dropped.** If a passport names an
  ``experiment_id`` whose record no longer exists in the experiment
  registry, the provenance says so explicitly (``link_ok=False`` with
  the reason). Silently returning "no parent" would hide a ledger
  inconsistency.
- **No lineage is stated as no lineage.** A passport without an
  ``experiment_id`` gets ``link_ok=False`` and the reason
  "passport carries no experiment lineage" — never a guessed parent.
- **Library-only (Tier 3).** Nothing here is wired into the live path.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.application.interfaces.experiment_store import ExperimentStore
from backend.application.interfaces.passport_store import PassportStore
from backend.domain.research.experiment import ExperimentRecord, ExperimentStatus


@dataclass(frozen=True, slots=True)
class PassportProvenance:
    """One passport's parent-experiment lineage, verified against the ledger.

    Attributes
    ----------
    passport_id: str
        The passport being traced.
    experiment_id: str | None
        The parent experiment id the passport names (None = no lineage).
    link_ok: bool
        True only when the named parent exists in the experiment registry.
    reason: str
        Empty when ``link_ok``; otherwise why the linkage cannot be
        confirmed.
    parent: ExperimentRecord | None
        The parent experiment record when ``link_ok``.
    """

    passport_id: str
    experiment_id: str | None
    link_ok: bool
    reason: str
    parent: ExperimentRecord | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "passport_id": self.passport_id,
            "experiment_id": self.experiment_id,
            "link_ok": self.link_ok,
            "reason": self.reason,
            "parent": self.parent.as_dict() if self.parent is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ExperimentProvenance:
    """One experiment's derived passports (forward lineage).

    Attributes
    ----------
    experiment_id: str
        The parent experiment being traced.
    child_passport_ids: tuple[str, ...]
        Passport ids naming this experiment as their parent, in issue
        order (oldest first).
    """

    experiment_id: str
    child_passport_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "child_passport_ids": list(self.child_passport_ids),
        }


class PassportProvenanceService:
    """Verify and traverse experiment <-> passport provenance.

    Parameters
    ----------
    store: PassportStore
        The passport ledger (children live here).
    registry: ExperimentStore
        The experiment registry (parents live here).
    """

    def __init__(self, store: PassportStore, registry: ExperimentStore) -> None:
        self._store = store
        self._registry = registry

    def provenance(self, passport_id: str) -> PassportProvenance:
        """Trace one passport to its parent experiment, or state why not."""
        passport = self._store.load_passport(passport_id)
        if passport is None:
            raise ValueError(f"unknown passport {passport_id}")
        experiment_id = passport.experiment_id
        if experiment_id is None:
            return PassportProvenance(
                passport_id=passport_id,
                experiment_id=None,
                link_ok=False,
                reason="passport carries no experiment lineage",
                parent=None,
            )
        parent = self._registry.get(experiment_id)
        if parent is None:
            return PassportProvenance(
                passport_id=passport_id,
                experiment_id=experiment_id,
                link_ok=False,
                reason=f"experiment record {experiment_id} not found in the registry",
                parent=None,
            )
        return PassportProvenance(
            passport_id=passport_id,
            experiment_id=experiment_id,
            link_ok=True,
            reason="",
            parent=parent,
        )

    def children(self, experiment_id: str) -> ExperimentProvenance:
        """Every passport that names ``experiment_id`` as its parent.

        Derived from the ledger at read time (projection only); an
        experiment with no children yields an empty tuple, never an error.
        """
        children = sorted(
            p.passport_id for p in self._store.all_passports() if p.experiment_id == experiment_id
        )
        return ExperimentProvenance(experiment_id=experiment_id, child_passport_ids=tuple(children))

    def linked_experiment_status(self, passport_id: str) -> ExperimentStatus | None:
        """Convenience: the parent's status, None when unlinked or missing."""
        provenance = self.provenance(passport_id)
        return provenance.parent.status if provenance.parent is not None else None


__all__ = ["ExperimentProvenance", "PassportProvenance", "PassportProvenanceService"]
