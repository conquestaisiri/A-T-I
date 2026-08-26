"""Domain interface for observation adapters.

Each concrete adapter (e.g., Binance, Kraken, News API) must implement this
abstract base class. The interface lives in the domain layer so that the
application and infrastructure layers can depend on it without coupling to a
specific implementation.
"""

import abc
from typing import Any

from .event import ObservationEvent


class ObservationAdapter(abc.ABC):
    """Abstract base class for all observation source adapters.

    Adapters are responsible for:
    * Connecting to an external data source (WebSocket, REST, SDK, etc.)
    * Subscribing to the required event types
    * Normalising raw messages into :class:`ObservationEvent`
    * Publishing the normalised events via the ObservationBus (injected by the
      infrastructure layer)
    * Providing health information for monitoring
    """

    @abc.abstractmethod
    async def connect(self) -> None:
        """Establish a connection to the external source."""

    @abc.abstractmethod
    async def disconnect(self) -> None:
        """Close the connection and clean up any resources."""

    @abc.abstractmethod
    async def subscribe(self, event_types: list[str]) -> None:
        """Subscribe to a list of event types defined in ``ObservationEventType``.

        Implementations may send subscription messages to a WebSocket or set up
        polling intervals for REST endpoints.
        """

    @abc.abstractmethod
    def normalize(self, raw: dict[str, Any]) -> ObservationEvent:
        """Convert a raw message from the external source into an ``ObservationEvent``.

        This method should raise a ``ValueError`` if the payload cannot be
        interpreted.
        """

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]:
        """Return a health dictionary (e.g., ``{"connected": True, "latency_ms": 12}``)."""
