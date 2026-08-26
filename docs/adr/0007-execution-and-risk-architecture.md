# 0007: Execution and Risk Architecture

## Decision
Execution and risk are clean-architecture services defined by **ports** in the application layer and **domain contracts** in the domain layer, with **no implementation of a live venue** in V1:

- **Domain contracts** (immutable value objects): `OrderRequest`, `OrderSide`, `OrderType`, `OrderStatus`, `Position`, `ExecutionReport`, and the durable `TradeRecord` outcome.
- **Ports**: `OrderGateway` (venue-agnostic submission), `RiskGate` (veto authority), `LedgerRepository` (durable outcome store), `ProposalRepository` (durable decision store).
- **Risk is a decoupled, deterministic service with veto authority over every order.** No strategy and no AI may bypass it.
- **Strategies and AI never import an exchange SDK.** They interact only with the domain order contracts and the `OrderGateway` port; one adapter per venue sits behind it.
- The **paper simulator** (`PaperFillEngine` implementing `OrderGateway`) is the V1 execution surface.

## Why
Document 03 and the Architecture Review mandate: risk must be a decoupled service with veto power, strategies must never import an exchange SDK, and execution must be replaceable behind one port per venue. Modelling the execution/risk domain *now* (cheap, high value) while implementing live venues *last* (after observation, context, and decision schema are real) prevents the claimed-but-unbuilt venue-agnosticism trap seen in the research repos.

The playbook's circuit breakers (daily 5% / monthly 15% / max drawdown 25% / total halt), dynamic sizing, and loss-limit halting are codified deterministically in the `CircuitBreakerRiskGate`.

## Alternatives Considered
- **Live venue first:** rejected — no operator state, no ledger, no risk history; unsafe and untestable before simulation exists.
- **Risk as inline checks inside execution:** rejected — violates the decoupled-veto requirement and couples safety to venue behavior.
- **Bypassable risk (best-effort):** rejected — the AI-is-trader/rules-are-safety constraint makes risk veto non-negotiable.

## Trade-offs
- A live venue adapter must be written later; the port surface is the cost to amortize.
- `TradeRecord` persists the *outcome* (entry/exit/PnL), not a full order book; that is the minimum the learning loop needs.

## Consequences
- Executing against a real venue means adding exactly one `OrderGateway` implementation; nothing in domain, application, risk, or ledger changes.
- All execution in V1 runs through the deterministic, replay-driven paper gateway.
- Risk breakers and sizing live in one decoupled gate, fully tested, with veto on REJECTED and cap on REDUCED.