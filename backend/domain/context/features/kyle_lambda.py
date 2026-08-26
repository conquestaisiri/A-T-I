# backend/domain/context/features/kyle_lambda.py
"""Kyle's lambda price-impact coefficient feature (integration #12).

Kyle's lambda (λ) measures the expected price move per unit of signed order
flow: ``Δmid ≈ λ · V``. A rolling OLS slope of price change on signed volume
gives a *model-free* local estimate of market impact and also a normaliser
that makes order-flow signals stationary across time-of-day and regimes.

This feature is fully deterministic: it recomputes the regression from the
trade events inside the snapshot window, so replay of the same events always
produces the same λ (ADR 0007).

Signed flow convention (Binance aggTrade semantics): a trade where the buyer
was NOT the market maker is a buyer-initiated trade and gets positive sign.
That matches crypto venues where the aggressor side is known exactly — no
Lee-Ready classification needed (integration stream 7).
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any, ClassVar

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.observation.event import ObservationEventType


def signed_flow_from_event(payload: Mapping[str, Any]) -> float:
    """Signed order flow of one trade event (aggressor convention).

    ``is_market_maker`` uses Binance aggTrade semantics: True means the buyer
    was resting (passive), so the seller aggressed and flow is negative.
    """
    quantity = payload.get("quantity")
    if not isinstance(quantity, (int, float)) or quantity <= 0.0:
        return 0.0
    is_maker = payload.get("is_market_maker", False)
    return float(quantity) if not is_maker else -float(quantity)


def kyle_lambda(
    flows: list[float],
    price_changes: list[float],
) -> dict[str, float] | None:
    """OLS slope of price change on signed flow, with ``None`` when degenerate.

    Returns ``{"lambda": ..., "r_squared": ..., "samples": n}``. ``None`` is
    returned when fewer than 2 samples exist or signed-flow variance is zero
    (nothing identifiable about impact in that window).
    """
    n = len(flows)
    if n < 2 or len(price_changes) != n:
        return None
    mean_x = sum(flows) / n
    mean_y = sum(price_changes) / n
    num = sum((flows[i] - mean_x) * (price_changes[i] - mean_y) for i in range(n))
    den = sum((flows[i] - mean_x) ** 2 for i in range(n))
    if den == 0.0:
        return None
    slope = num / den
    var_y = sum((price_changes[i] - mean_y) ** 2 for i in range(n))
    r_squared = (slope * num / var_y) if var_y > 0.0 else 1.0
    return {
        "lambda": round(slope, 8),
        "r_squared": round(min(1.0, max(0.0, r_squared)), 6),
        "samples": n,
    }


def extract_trade_series(snapshot: ContextSnapshot) -> tuple[list[float], list[float]]:
    """Extract ``(price_changes, signed_flows)`` from the snapshot's trades.

    Price change uses consecutive trade mid-approximations (the trade price
    itself — trades print at the touch, so trade-to-trade delta is the right
    signal). Returns parallel lists; the first price change is 0.0 since there
    is no prior trade inside this snapshot to diff against.
    """
    prices: list[float] = []
    flows: list[float] = []
    for event in snapshot.events:
        if event.event_type != ObservationEventType.TRADE:
            continue
        price = event.payload.get("price")
        if not isinstance(price, (int, float)) or price <= 0.0:
            continue
        prices.append(float(price))
        flows.append(signed_flow_from_event(event.payload))
    if len(prices) < 2:
        return [], []
    price_changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    return price_changes, flows[1:]


class KyleLambdaFeature:
    """Kyle's lambda from the trades in the current snapshot window.

    Because the estimator is stateless and snapshot-scoped, it is safe to wire
    anywhere a :class:`ContextSnapshot` exists and is replay-deterministic.
    """

    name: ClassVar[str] = "kyle_lambda"

    @staticmethod
    def compute(
        snapshot: ContextSnapshot,
        parameters: Mapping[str, Any] | None = None,
    ) -> ContextFeature:
        start = time.perf_counter()
        price_changes, flows = extract_trade_series(snapshot)
        result = kyle_lambda(flows, price_changes)

        if result is None:
            value: dict[str, Any] = {
                "lambda": None,
                "r_squared": None,
                "samples": len(price_changes),
                "status": "insufficient_data",
            }
        else:
            value = {
                **result,
                "status": "ok",
            }
        return ContextFeature(
            name=KyleLambdaFeature.name,
            value=value,
            computation_timestamp=snapshot.end_timestamp,
            execution_time=time.perf_counter() - start,
        )
