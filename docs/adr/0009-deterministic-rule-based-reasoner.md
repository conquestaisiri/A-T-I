# 0009: Deterministic Rule-Based Reasoner (Solver)

## Decision
The V1 reasoning surface that turns a persisted `MarketContext` into a `DecisionProposal` is a **deterministic, rule-based solver** behind the `AIReasoner` port:

- `AIReasoner` is the application-layer port; the `RuleBasedSolver` is the V1 implementation.
- The solver consumes an immutable `MarketContext` (features + risk snapshot) and emits a `DecisionProposal` (hypothesis, evidence, confidence, uncertainty, ordered action set, risk context, alternatives, rationale).
- It is **fully deterministic** — the same context yields the same proposal. It holds no model state and performs no stochastic reasoning.
- LLM-backed reasoning is a *later, separate* implementation of the same port (free-tier via ADR 0005, dev/backtest only), never a replacement for the deterministic core.

## Why
ADR 0006 fixes the AI entry point: context-in, proposal-out, risk-gated. Document 03 and the Constitution (I-01: "deterministic software is the workhorse") require a working, testable reasoning path before any free-tier LLM is attached. A rule-based solver is the smallest correct implementation of the `AIReasoner` contract and gives the paper simulator a real proposal source today.

## Alternatives Considered
- **LLM reasoning first:** rejected — free-tier is unreliable (ADR 0005), nondeterministic, and cannot yet be calibrated against outcomes. Determinism comes first.
- **No reasoner until after more infrastructure:** rejected — the decision pipeline, risk gate, and simulator need a proposal producer to be exercised end-to-end now.
- **Reasoner emits orders directly:** rejected — it emits proposals; risk holds veto (ADR 0007).

## Trade-offs
- A rule-based solver encodes simple feature thresholds and is not itself "intelligent"; that is the point of phase-order — it validates the *pipeline*, while reasoning quality matures later and is measured via the ledger.
- Thresholds are configuration, not magic: they are explicit, tested, and adjustable without touching the pipeline.

## Consequences
- The decision pipeline is wired as: `MarketContext` → `AIReasoner.reason(context, risk)` → `DecisionProposal` → risk gate → simulator → ledger.
- LLM integration later only supplies another `AIReasoner` implementation; nothing downstream changes.
- Solver behavior is fully understood, explained, and deterministically tested.