# backend/application/interfaces/order_gateway.py
"""Port for venue-agnostic order execution.

Strategies and the AI interact only with this port and the domain order
contracts. One adapter per venue sits behind it; swapping venues never touches
the core (Architecture Constitution: replaceability rules).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, runtime_checkable

from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderRequest


class OrderGateway(ABC):
    """Contract for submitting orders to any venue."""

    @abstractmethod
    def submit(self, order: OrderRequest) -> ExecutionReport:
        """Submit an order and return its execution report.

        The gateway decides whether the order filled at the venue. It never
        decides *whether* to trade — that is the risk gate's authority.
        """
        raise NotImplementedError


@runtime_checkable
class CancelableGateway(Protocol):
    """Optional capability for venues that support order cancellation.

    Structural protocol (not an ABC): a gateway that implements ``cancel``
    satisfies it without inheriting. The paper engine and any live adapter
    that supports cancels expose this same interface, so cancellation is
    modelled consistently between simulation and live execution.
    """

    def cancel(self, order_id: str) -> ExecutionReport:
        """Cancel a resting order.

        Returns the CANCELLED report for the order, or raises if the order is
        unknown or no longer resting.
        """
        raise NotImplementedError
