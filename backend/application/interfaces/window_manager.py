# backend/application/interfaces/window_manager.py
"""Interface for managing a time‑based rolling window of ObservationEvents.

The WindowManager maintains its own internal mutable collection but only
exposes immutable snapshots to callers. It operates on a per‑symbol basis
(e.g., per market instrument) to allow independent windows.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.observation.event import ObservationEvent


class WindowManager(ABC):
    """Contract for a time‑based rolling window manager.

    Implementations must be thread‑safe and provide deterministic snapshots.
    Configuration is injected via ``ContextSettings`` at construction time.
    """

    @abstractmethod
    def add(self, event: ObservationEvent) -> None:
        """Add a single event to the appropriate symbol window.

        The symbol is extracted from ``event.payload['symbol']``.
        """
        raise NotImplementedError

    @abstractmethod
    def snapshot(self, symbol: str) -> ContextSnapshot:
        """Return an immutable snapshot of the current window for the symbol.

        The snapshot must contain events ordered by timestamp.
        """
        raise NotImplementedError

    @abstractmethod
    def clear(self, symbol: str) -> None:
        """Clear all stored events for the given symbol."""
        raise NotImplementedError
