# backend/application/interfaces/passport_store.py
"""Strategy-passport persistence contract (task P5-003b, evidence engine).

The passport store is the durable ledger of the evidence engine: it writes
each immutable :class:`StrategyPassport` snapshot and appends lifecycle
events, and it refuses to silently overwrite history. This mirrors the other
record stores (``experiment_store``, ``autonomy_store``): records are
immutable facts; updates are appends; a repository must reject a write over
an existing passport id.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from typing import Any

from backend.domain.research.passport import (
    PassportLifecycleEvent,
    StrategyPassport,
)


class PassportStore(ABC):
    """Durable, append-only storage for strategy passports."""

    @abstractmethod
    def save_passport(self, passport: StrategyPassport) -> None:
        """Persist a passport snapshot.

        Raises ``ValueError`` if a passport with the same id already exists:
        records are immutable facts and updates must be expressed as
        lifecycle events.
        """

    @abstractmethod
    def load_passport(self, passport_id: str) -> StrategyPassport | None:
        """Return the latest passport snapshot by id (None when unknown)."""

    @abstractmethod
    def append_lifecycle_event(self, event: PassportLifecycleEvent) -> None:
        """Append one lifecycle event to a passport's ledger."""

    @abstractmethod
    def lifecycle(self, passport_id: str) -> tuple[PassportLifecycleEvent, ...]:
        """Return all lifecycle events for a passport, oldest first."""

    @abstractmethod
    def all_passports(self) -> tuple[StrategyPassport, ...]:
        """Return every passport snapshot (for population views, T2-12)."""

    @abstractmethod
    def replace_passport(self, passport: StrategyPassport) -> None:
        """Persist a new snapshot over an existing passport (after a
        lifecycle event recorded the transition). Raises ``ValueError``
        when the passport does not exist yet."""


def passport_as_json(passport: StrategyPassport) -> Mapping[str, Any]:
    """Return the passport as the JSON-able dict the operator report uses."""
    return passport.as_dict()
