# backend/application/interfaces/context_builder.py
"""Interface for building market context from observation events.

The implementation must be deterministic, thread‑safe and return an immutable
:class:`~backend.domain.context.market_context.MarketContext`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.context.market_context import MarketContext
from backend.domain.observation.event import ObservationEvent


class ContextBuilder(ABC):
    """Application‑level contract for constructing a MarketContext.

    Implementations receive a single :class:`ObservationEvent` and orchestrate
    window management, feature computation and context creation.
    """

    @abstractmethod
    def handle(self, event: ObservationEvent) -> MarketContext:
        """Process a single observation event and return a new MarketContext.

        Parameters
        ----------
        event: ObservationEvent
            The incoming observation to incorporate.
        """
        raise NotImplementedError
