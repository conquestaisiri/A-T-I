# backend/application/interfaces/risk_feed.py
"""Port for feeding live risk signals into the risk gate.

The gate's advanced layers (VPIN toxicity veto, square-root impact veto,
fractional-Kelly cap) hold no state of their own beyond what the pipeline
feeds them (gap G3). ``RiskFeed`` is the contract that makes those layers
live without coupling the pipelines to a concrete gate:

- :meth:`record_toxicity_flow` — signed order flow from observed trades.
- :meth:`set_market_stats` — per-symbol ADV / volatility / spread.
- :meth:`record_impact_fill` — realized fills for the impact calibrator.
- :meth:`update_edge_estimate` — historical edge for fractional-Kelly.
- :meth:`set_reconciliation_state` — per-symbol venue-vs-internal health; an
  inconsistent symbol blocks new risk (P0-012 / spec §9.5).

``CircuitBreakerRiskGate`` implements every method; the composition root
passes the single gate instance to both the ingest path (toxicity) and the
decision path (market stats / fills / edge). ``market_stats_registered`` is
the guard that lets the decision path safely skip impact calibration until
the operator has supplied venue-level stats — no fabricated data.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover -- type-only import (lazy annotations)
    from backend.application.risk.circuit_breaker_risk_gate import KellyEdgeEstimate


class RiskFeed(ABC):
    """Contract for the live risk-signal feeds into the gate."""

    @abstractmethod
    def record_toxicity_flow(self, symbol: str, signed_flow: float) -> None:
        """Feed signed order flow for ``symbol`` into its VPIN estimator."""

    @abstractmethod
    def set_market_stats(
        self,
        symbol: str,
        *,
        avg_daily_volume: float,
        volatility_bps: float,
        half_spread_bps: float,
    ) -> None:
        """Register per-symbol market stats used by the impact veto."""

    @abstractmethod
    def market_stats_registered(self, symbol: str) -> bool:
        """True when market stats are registered for ``symbol``."""

    @abstractmethod
    def record_impact_fill(
        self,
        symbol: str,
        *,
        quantity: float,
        realized_slippage_bps: float,
    ) -> None:
        """Feed one realized fill into the impact calibrator."""

    @abstractmethod
    def update_edge_estimate(self, symbol: str, edge_est: KellyEdgeEstimate) -> None:
        """Update the fractional-Kelly edge estimate for ``symbol``."""

    @abstractmethod
    def set_reconciliation_state(self, symbol: str, consistent: bool) -> None:
        """Feed venue-vs-internal reconciliation health for ``symbol``.

        ``consistent=False`` flags ``symbol`` as having an outstanding
        position mismatch (P0-012). The gate treats an inconsistent symbol as
        reason to block new risk: it can no longer trust the internal ledger
        for that symbol.
        """
