# 0008: Deterministic Paper Trading Simulator

## Decision
V1 execution validation happens in a **deterministic, replay-driven paper simulator**:

- `PaperTradingSimulator` consumes `DecisionProposal` instances, subjects each to the `RiskGate` (veto authority), fills approved actions via a `PaperFillEngine` (the V1 `OrderGateway`), and records outcomes into the durable `LedgerRepository`.
- The simulator is **fully deterministic given the same input sequence**: the same proposals and mark prices produce the identical ledger. It has no clock or network dependence — prices and order timestamps are supplied by the replay driver.
- Proposals and their outcomes persist (via `ProposalRepository` and `LedgerRepository`), so every simulation run is observable, explainable, and auditable.

## Why
The Architecture Review's Phase 2 objective is a simulation harness before any live execution. A deterministic paper simulator gives the system a safe, repeatable way to validate the decision → risk → execution → ledger path and, later, to calibrate confidence against outcomes. Replay determinism is the property that makes backtests and paper results trustworthy.

## Alternatives Considered
- **Simulate inline in the risk gate:** rejected — conflates safety (which must be purely deterministic and veto-only) with execution mechanics.
- **Use live data feeds / wall-clock fills:** rejected — nondeterministic; cannot be replayed and audited.
- **No simulator until a live venue exists:** rejected — leaves the risk, execution, and ledger path entirely untested.

## Trade-offs
- The simulator models fills against a supplied order book (top-of-book or multi-level ladder), not a full matching engine; acceptable for V1 risk/decision validation.
- V1 (P0) modeled fills at the touch; P2-001 added deterministic microstructure to the `PaperFillEngine`: multi-level depth (VWAP fills), partial fills with a reported remaining quantity, a FIFO price-time queue for resting limit orders (with `advance` sweeping and reported queue positions), cancellation via the `CancelableGateway` capability, constant modeled latency, and an optional participation-impact dial. All knobs default to the legacy behavior, so existing replays are unchanged unless configured.

## Consequences
- The `PaperFillEngine` is the only `OrderGateway` in V1; swapping to a real venue is a single adapter.
- The paper engine also satisfies `CancelableGateway`, so cancellation is modelled in simulation and shares its interface with any live adapter that supports cancels.
- Every proposal path (approved, rejected, opened, closed, partial) is persisted and replayable.
- The decision pipeline and observability layer read and write through this deterministic harness.
