# backend/application/interfaces/venue_state.py
"""Port for reading venue-reported execution state.

This is the read-side twin of :class:`OrderGateway`: where the gateway submits
orders, this port answers *what the venue currently holds*. It exists because
the paper engine (a pure in-memory fill model) cannot truthfully report venue
state — only real adapters like CCXT can. It powers position reconciliation and
restart recovery (P0-012).

The venue is the source of truth for market-exposed state; internal records are
the source of truth for intent. This port only reads — mutation stays behind
``OrderGateway``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.execution.order import OrderStatus
from backend.domain.execution.reconciliation import VenuePosition


class VenueStateSource(ABC):
    """Read-only access to venue-reported orders and positions."""

    @abstractmethod
    def fetch_open_positions(self) -> list[VenuePosition]:
        """Return every position the venue reports as currently open.

        Adapters normalise venue-specific representations into
        :class:`VenuePosition` (net sizes mapped to BUY/SELL). The list is
        empty when the venue holds nothing.
        """
        raise NotImplementedError

    @abstractmethod
    def fetch_order_status(self, order_id: str) -> OrderStatus:
        """Return the venue's current status for one order.

        The result is explicit: an unrecognised venue status maps to
        ``OrderStatus.UNKNOWN``, never to a meaningful state.
        """
        raise NotImplementedError
