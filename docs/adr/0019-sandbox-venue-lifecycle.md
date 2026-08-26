# ADR 0019: Sandbox Venue Lifecycle

## Status
Accepted

## Context
The paper `PaperFillEngine` fills orders deterministically but owns no venue
bookkeeping: there is no authoritative per-order lifecycle, no expiry, and no
way for the system to learn *venue truth* (what the venue holds) without a real
exchange. Resting orders live forever unless externally cancelled, rejections
are a report and nothing more, and `VenueStateSource` — the read-side port every
live adapter implements — had no paper implementation (P0-012 / ADR 0008). The
all-paper pipeline therefore could not rehearse order lifecycle or
reconciliation against a venue that self-reports, the exact failure modes live
trading must survive.

## Decision
Introduce a sandbox venue as the authoritative owner of order lifecycle on the
paper path, split into a pure domain state machine and a deterministic
application adapter:

- **Domain** (`backend/domain/execution/order_lifecycle.py`): `VenueOrderState`
  is an immutable record of one order (id, symbol, side, quantity, created_at,
  status, cumulative filled quantity, volume-weighted average price, resting
  window, and an append-only fill log). Every transition is a *guarded pure
  function*: `with_fill`, `as_rested`, `as_rejected`, `as_cancelled`,
  `as_expired`. `TERMINAL_STATUSES = {FILLED, CANCELLED, REJECTED, EXPIRED}`;
  a terminal order can never be filled, cancelled or expired again. All
  timestamps are timezone-aware and supplied by the caller.
- **Application** (`backend/application/simulation/sandbox_venue.py`):
  `SandboxVenue` wraps `PaperFillEngine` and implements `OrderGateway` +
  `CancelableGateway` + `VenueStateSource`. It records the lifecycle for every
  submit/advance/cancel, and adds deterministic expiry: a resting order's
  deadline is `created_at + resting_ttl_hours`, and `expire_due(now)` moves
  every due order to `EXPIRED` under a driver-supplied clock — never the wall.
  Expiry removes the order from the engine's FIFO mechanically
  (`engine.cancel`) but the venue truthfully reports `EXPIRED`, not `CANCELLED`.
- **Surface**: `POST /v1/reconcile/sandbox` runs the shared reconciliation
  service against the sandbox venue's self-reported positions, persisting the
  same report shape as `POST /v1/reconcile`. Disagreements are surfaced, never
  coerced.

Alternatives rejected:
- Expiry as an engine feature. The engine fills orders; whether an order
  remains at risk is venue policy. Keeping expiry in the venue keeps the engine
  a pure fill model.
- Wall-clock expiry. Any realtime dependency would break replay determinism;
  the venue never reads the clock — the driver tells it the time.
- A `UNKNOWN` default for unknown order ids. Kept deliberately: an
  unrecognised venue state must surface as a reconciliation problem, never be
  silently coerced into `NEW` or `CANCELLED` (order.py contract).

## Consequences
- **Positive**: The all-paper pipeline now rehearses a complete order lifecycle
  — fill, rest, partial, cancel, reject, expire — with guarded transitions, and
  `VenueStateSource` has a reference implementation to reconcile against, so
  the reconciliation service, recovery path, and `POST /v1/reconcile/sandbox`
  are exercised before any live adapter exists.
- **Negative**: A market order's unfilled remainder is never enqueued by the
  engine, so expiring such an order is a best-effort engine removal; the venue
  lifecycle remains authoritative. Resting TTL applies uniformly (a single
  `resting_ttl_hours`), not per-order or per-symbol.
- **Neutral**: No change to the fill engine or internal simulator state; the
  venue is an additive layer that shares the exact ports live adapters use.

## References
- ADR 0008 (deterministic paper simulator)
- ADR 0012 (CCXT venue adapter — the live adapter the venue mirrors)
- P0-012 / ADR assortments on position/order reconciliation
- docs/ATI_BUILD_ORDER.md STAGE 3 item #27
- `backend/domain/execution/order_lifecycle.py`,
  `backend/application/simulation/sandbox_venue.py`,
  `backend/presentation/api/routes_reconciliation.py`,
  `tests/application/test_sandbox_venue.py`