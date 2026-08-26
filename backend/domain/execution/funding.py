# backend/domain/execution/funding.py
"""Deterministic perpetual-swaps funding / carry cost model.

Funding is a periodic holding cost charged on open position notional
(typically every 8 hours, at fixed UTC boundaries). This module reproduces
that mechanism as a pure function of the trade timestamps, quantity and rate
— no clock, no market data — so replays of the same proposal sequence produce
the identical funding stream.

Sign convention (signed *cost*, the exact amount added to the PnL windows):

    cost = direction * rate * notional * intervals

with ``direction = +1`` for a long and ``-1`` for a short. A positive cost
reduces realized PnL; a negative cost is a credit. This mirrors real perpetual
swaps where longs pay shorts when the funding rate is positive (and receive
when it is negative).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from .order import OrderSide
from .order import signed_direction as _signed_direction

# Re-export for compatibility
signed_direction = _signed_direction

# Default anchoring epoch: funding boundaries fall on UTC midnight +
# k * interval, i.e. the conventional 00:00 / 08:00 / 16:00 UTC cadence for
# an 8-hour interval.
_DEFAULT_EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class FundingConfig:
    """Funding schedule for a venue, deterministic by construction.

    ``rate`` is the signed fraction of notional charged per interval (e.g.
    ``0.0001`` at every 8-hour boundary). A negative rate flips the payer
    (longs receive, shorts pay). ``interval_hours`` is the period between
    funding payments; ``epoch`` anchors the payment grid so payments fall at
    fixed UTC boundaries rather than relative to each position's open time.

    The default configuration models the perpetual-swap cadence (00/08/16
    UTC). Setting ``rate`` to zero keeps funding modeled (a zero-cost credit
    ledger stream is recorded); passing ``None`` as the config to the
    simulator leaves funding unmodeled entirely.
    """

    rate: float
    interval_hours: float = 8.0
    epoch: datetime = _DEFAULT_EPOCH

    def __post_init__(self) -> None:
        if math.isnan(self.rate) or math.isinf(self.rate):
            raise ValueError("funding rate must be a finite float")
        if self.interval_hours <= 0.0:
            raise ValueError("interval_hours must be positive")
        if self.epoch.tzinfo is None:
            raise ValueError("funding epoch must be timezone-aware")


def funding_intervals(opened_at: datetime, closed_at: datetime, config: FundingConfig) -> int:
    """Count funding payment boundaries strictly after open and through close.

    A boundary exactly at ``opened_at`` is not charged (the position is not
    held *over* it); a boundary at ``closed_at`` is. Returns 0 when ``close``
    precedes ``open`` or no boundary lies between.
    """
    opened = _aware_utc(opened_at, "opened_at")
    closed = _aware_utc(closed_at, "closed_at")
    if closed <= opened:
        return 0
    interval = timedelta(hours=config.interval_hours)
    return _boundary_index(closed, config.epoch, interval) - _boundary_index(
        opened, config.epoch, interval
    )


def funding_cost_for(
    side: OrderSide,
    quantity: float,
    entry_price: float,
    opened_at: datetime,
    closed_at: datetime,
    config: FundingConfig,
) -> float:
    """Signed funding cost for holding ``quantity`` from ``opened_at`` to
    ``closed_at`` under ``config``.

    Notional is priced at the deterministic entry price (never the mark), so
    the result depends only on the trade itself and the schedule.
    """
    intervals = funding_intervals(opened_at, closed_at, config)
    if intervals == 0 or quantity <= 0 or entry_price <= 0:
        return 0.0
    notional = entry_price * quantity
    return signed_direction(side) * config.rate * notional * intervals


def _boundary_index(at: datetime, epoch: datetime, interval: timedelta) -> int:
    """Number of funding boundaries at or before ``at`` on the epoch grid."""
    seconds = (at - epoch).total_seconds()
    if seconds < 0:
        return -1
    return int(seconds // interval.total_seconds())


def _aware_utc(value: datetime, name: str) -> datetime:
    """Coerce an unaware datetime to UTC or reject an invalid one."""
    if value.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value
