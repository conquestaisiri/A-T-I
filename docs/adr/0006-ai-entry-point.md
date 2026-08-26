# 0006: AI Entry Point

## Decision
The AI enters the decision path at the **context level, out-of-band**:

- The AI never sees raw market events and never runs inside the ingest path.
- The AI consumes a persisted, immutable `MarketContext` (features + snapshot metadata) produced by the deterministic pipeline.
- AI output is a **plan/proposal**, not an order. Proposals must pass deterministic risk gates and require human approval before any live execution.
- The entry point is a port (`AIReasoner`) in the application layer; the free-tier client is an infrastructure implementation. The deterministic core and AI are cleanly separated.

## Why
The review identified the unresolved question of *when AI enters the decision path* as directionless engineering: every sprint either built deterministic scaffolding or waited for AI. This ADR fixes the boundary now, before application code exists.

The choice follows from the Constitution's identity: "The AI is the trader, rules are safety constraints." The AI reasons over the richest artifact the system produces (the `MarketContext`), while the deterministic layer owns everything that must be deterministic — ingestion, feature computation, persistence, and safety.

## Alternatives Considered
- **Event-level AI:** reasoning on every raw trade. Rejected — noisy, expensive on a free tier, and bypasses the deterministic windowing that already aggregates signal.
- **AI as the only path:** no deterministic context. Rejected — contradicts the built deterministic core and the safety constraint.
- **Inline AI (in ingest path):** rejected by ADR 0005 — a free-tier outage would block ingestion.

## Trade-offs
- The AI is one step removed from raw data; it trusts the deterministic features it is given. Feature bugs become reasoning bugs, so the feature engine stays the most-tested surface.
- Out-of-band reasoning adds latency between observation and proposal; acceptable because proposals are not time-critical to the degree of ingestion.

## Consequences
- Application code that needs AI depends on the `AIReasoner` port; nothing else imports an AI client.
- Proposals are first-class, persisted, risk-gated artifacts — never direct order submissions.
- This ADR and ADR 0005 together define the boundary for all future AI work: context-in, proposal-out, risk-gated, human-approved, dev/backtest-only on free tiers.
