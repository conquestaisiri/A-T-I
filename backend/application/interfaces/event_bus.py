# backend/application/interfaces/event_bus.py
"""Interface for the platform EventBus abstraction.

Only the contract is defined here; concrete implementations live in the
infrastructure layer and may be swapped without impacting the application
logic.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EventBus(ABC):
    """Contract for publishing events to the platform's event bus.

    Implementations must provide at least a ``publish`` method.
    """

    @abstractmethod
    def publish(self, event_name: str, payload: Any) -> None:
        """Publish an event with the given name and payload.

        Parameters
        ----------
        event_name: str
            Name of the event (e.g., ``"MarketContextCreated"``).
        payload: Any
            JSON‑serialisable payload representing the event data.
        """
        raise NotImplementedError
