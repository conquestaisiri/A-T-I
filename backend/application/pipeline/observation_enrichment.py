# backend/application/pipeline/observation_enrichment.py
"""Canonical observation enrichment step for order-book-derived features.

Routes each incoming :class:`ObservationEvent` to the module state that the
order-book features read:

- full order-book snapshots -> micro-price state + OFI reconstructed book
- L2 delta events          -> OFI tracker + optional tick recorder

This is the single explicit event-to-state path (audit §19; task P0-004). A
feature never mutates its own cache on demand and never depends on an
undocumented external caller; the pipeline feeds it through this component.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from backend.domain.context.features.micro_price import (
    get_state as get_micro_price_state,
)
from backend.domain.context.features.micro_price import (
    reset_state as reset_micro_price_state,
)
from backend.domain.context.features.micro_price import update_from_event
from backend.domain.context.features.order_flow import (
    OFITracker,
    process_observation_event,
    set_ofi_tracker,
)
from backend.domain.observation.event import ObservationEvent, ObservationEventType

if TYPE_CHECKING:
    from backend.application.validation.tick_recorder import TickRecorder


class ObservationEnrichment:
    """Route order-book events into feature state and research capture.

    Parameters
    ----------
    ofi_tracker:
        OFI tracker to feed L2 deltas into. When omitted a fresh tracker is
        created and registered globally so :class:`OrderFlowFeature` reads it.
    tick_recorder:
        Optional L2 tick recorder for historical research capture.
    """

    def __init__(
        self,
        *,
        ofi_tracker: OFITracker | None = None,
        tick_recorder: TickRecorder | None = None,
    ) -> None:
        self._ofi_tracker = ofi_tracker or OFITracker()
        set_ofi_tracker(self._ofi_tracker)
        self._tick_recorder = tick_recorder
        reset_micro_price_state()

    def enrich(self, event: ObservationEvent) -> None:
        """Apply one observation event to feature state and research capture.

        Never raises: enrichment must not be able to kill the ingest path.
        """
        if event.event_type is not ObservationEventType.ORDER_BOOK:
            return
        try:
            if event.payload.get("delta", False):
                process_observation_event(event)
                if self._tick_recorder is not None:
                    self._tick_recorder.record_event(event)
            else:
                update_from_event(event)
                process_observation_event(event)
        except Exception:  # noqa: BLE001
            return

    def reset(self) -> None:
        """Clear feature state so a replay produces identical contexts (ADR 0007)."""
        reset_micro_price_state()
        self._ofi_tracker = OFITracker()
        set_ofi_tracker(self._ofi_tracker)

    def micro_price(self, symbol: str) -> dict[str, Any] | None:
        """Current micro-price state for ``symbol`` (for tests/observability)."""
        return get_micro_price_state(symbol)

    def ofi(self, symbol: str) -> dict[str, Any]:
        """Current OFI statistics for ``symbol`` (for tests/observability)."""
        return self._ofi_tracker.get_ofi(symbol)


def reset_observation_enrichment_state() -> None:
    """Reset module-level enrichment state used by order-book features.

    Called when a fresh context pipeline is built so a replay of the same
    events produces identical contexts (ADR 0007), matching the regime
    detector reset in ``bootstrap.build_context_pipeline``.
    """
    reset_micro_price_state()
    set_ofi_tracker(OFITracker())
