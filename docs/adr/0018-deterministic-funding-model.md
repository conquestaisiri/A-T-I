# ADR 0018: Deterministic Funding Model

## Status
Accepted

## Context
Perpetual swaps charge a periodic holding cost (funding) on open position
notional, typically every 8 hours. The paper simulator's execution fee was
fully modeled (P0-010) but funding was explicitly deferred: `funding_cost`
existed on `TradeRecord` / `ExecutionReport` and stayed `None`. Without a
funding model, strategies that hold overnight (or trade perpetuals) report a
systematically flattering PnL that could be mistaken for alpha (see ADR 0017:
fees + funding are separate cost streams).

## Decision
Model funding as a pure, deterministic function in the domain
(`backend/domain/execution/funding.py`), independent of any clock or market
data:

- `FundingConfig(rate, interval_hours=8.0, epoch=UTC-midnight)` — signed
  fraction per interval; payment boundaries at `epoch + k·interval`, i.e. the
  conventional 00:00 / 08:00 / 16:00 UTC cadence.
- `funding_cost_for(side, quantity, entry_price, opened_at, closed_at, cfg)`:
  `cost = direction · rate · entry_price · quantity · intervals`, with
  `direction = +1` for a long, `-1` for a short. Longs pay a positive rate,
  shorts receive; a negative rate flips the payer.
- `intervals` counts boundaries strictly after open and through close; a
  boundary at open is not charged, at close it is. Naive (naive-UTC) inputs
  are rejected so boundaries are never ambiguous.

The simulator accepts an optional `funding_config`. When `None` the behavior
is unchanged (`funding_cost` stays `None`). When configured, every close path
(full, partial slice, bracket) charges the slice's funding into equity and
the daily/monthly loss windows and records `funding_cost` on the closed
ledger row, so `realized_pnl = gross − fees − funding` keeps the attribution
identity exact.

Alternative rejected: tick-level funding accrued at every boundary while a
position is open. It is more realistic but requires the replay driver to
tick time independently of proposals; the chosen model is an exact function
of the trade's own timestamps.

## Consequences
- **Positive**: Overnight holding cost is now visible, separate from fees,
  land in attribution's `funding_cost`; deterministic replays reproduce it.
- **Negative**: Partial closes charge each slice its own held boundaries
  (slightly over-counts versus per-boundary accounting on live quantity);
  full closes are exact. The approximation is documented in the domain
  module and tests.
- **Neutral**: Stays `None`/zero-cost unless an operator configures a
  schedule, preserving the fee-free deterministic baseline.

## References
- ADR 0017 (execution attribution — the identity `net = gross − fees − funding`)
- ADR 0008 (deterministic paper simulator)
- docs/ATI_BUILD_ORDER.md STAGE 3 items #25/#26
- `backend/domain/execution/funding.py`, `tests/application/test_paper_funding.py`