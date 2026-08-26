# ADR 0017: Execution Attribution

## Status
Accepted

## Context
The paper simulator reports realized PnL as a single number. The operator cannot tell
whether a losing trade lost money because the idea was wrong (alpha) or because
execution was poor (slippage, fees, funding). Without this decomposition, execution
cannot be measured or improved, and strategy decisions cannot be separated from
execution quality. The build order requires believable execution (STAGE 3, item #25).

## Decision
Decompose every closed trade's net PnL into four additive components using the
arrival-mid convention (ADR 0008, arrival-price capture):

- **alpha** — PnL earned if fills matched the decision-time arrival mid exactly;
- **entry slippage** — positive cost of filling away from arrival mid on entry
  (buy above arrival, sell below arrival);
- **exit slippage** — same on exit;
- **fees + funding cost** — the cost streams, kept separate.

Identities (exact per trade, both sides):

```text
gross_pnl = alpha_pnl - entry_slippage - exit_slippage
net_pnl   = gross_pnl - fees - funding_cost
```

Implementation:
- `backend/domain/execution/attribution.py` — pure function `attribute_trade` +
  `TradeAttribution` dataclass (no IO, unit-testable).
- `backend/application/execution/execution_attribution.py` — `ExecutionAttributionService`
  that loads closed trades from the ledger, attributes each, and produces a portfolio
  aggregate including `cost_drag_pct` (total cost ÷ gross alpha).
- Paper simulator captures `entry_arrival_price` / `exit_arrival_price` from the
  gateway report's `arrival_mid` on every open/close/bracket path; migration
  `0009` widens the trades table.
- Surface: `GET /v1/ledger/attribution` (symbol filter, per-trade + aggregate).

Alternative rejected: enriching the trade record with a single "slippage_pnl" field.
This cannot separate entry from exit slippage or alpha from execution, and it makes
the reconciliation identity implicit.

## Consequences
- **Positive**: Execution quality becomes measurable; strategy return (alpha) and
  execution cost are separable for inspection and learning; identity is exact.
- **Negative**: Requires the simulator to persist arrival prices; cannot attribute
  trades persisted before migration (arrival fields null → alpha falls back to
  decision-price basis, slippage unreported).
- **Neutral**: Pure function stays in domain; no coupling to venue adapters.

## References
- ADR 0008 (deterministic paper simulator)
- docs/ATI_BUILD_ORDER.md STAGE 3 item #25
- `backend/domain/execution/attribution.py`, `backend/application/execution/execution_attribution.py`