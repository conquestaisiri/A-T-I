# 0010: Bounded Episodic Memory and LLM Reasoning

## Decision
ATI gains a **bounded episodic memory** store and an **LLM-backed reasoner** as a second
implementation of the `AIReasoner` port (ADR 0006, 0009):

**Memory (Constitution Document 05, Hermes-style)**
- A `MemoryStore` port owns the contract; a SQLite implementation persists episodes
  behind it (`memory_episodes` table, ADR 0004).
- An episode binds one decision (proposal id, action type, confidence) to the market
  outcome that followed (win/loss/flat, realised PnL). Content is **market outcomes,
  not conversations**.
- Memory is **bounded**: the reasoner recalls at most `recall_limit` episodes per symbol
  per decision; it never dumps its full history into a prompt.
- Rules: never remember secrets or raw prompts; always remember decisions, reasons,
  failures, and lessons; memory is explainable to the operator.

**LLM reasoning (ADR 0005, 0006)**
- `AiOmniRouteReasoner` is the V1 LLM implementation of `AIReasoner`. It posts a compact,
  symbolic prompt — market features, risk snapshot, recent episodic memory — to the
  OmniRoute router (`localhost:20128/v1`, multi-provider failover), requests strict JSON,
  and validates the reply into a `DecisionProposal`.
- The router owns provider failover; the client performs no fallback logic.
- The deterministic `RuleBasedSolver` remains the calibration baseline.

**Reflection (Constitution Document 05: "Reflection should update this memory")**
- `ReflectionService` (application layer) reads closed trades from the durable ledger,
  joins their originating proposal for confidence and primary action, derives the
  realised outcome from PnL (win/loss/flat), and writes a bounded `MemoryEpisode`.
- Recording is idempotent by `ep-<trade_id>` (ADR 0004 `ON CONFLICT DO NOTHING`), so
  re-running reflection is always safe.
- Reflection is out-of-band: it reads durable artifacts only, never live prices, and
  never alters risk parameters. Open trades and stand-aside proposals are skipped.

## Why
The free-tier degradation policy (ADR 0005) and the AI entry point (ADR 0006) define *how*
the system reasons. A reasoner that is grounded in bounded, recallable market outcomes is
the mechanism by which ATI becomes "smarter" across sessions — it stops deciding from a
single isolated context and begins deciding from experience.

## Alternatives Considered
- **No memory:** rejected — the LLM would reason from a single context with no historical
  grounding, defeating the "improve over time" objective of Document 05.
- **Unbounded memory dump:** rejected — violates the bounded-context rule and would bloat
  every prompt with irrelevant episodes (Constitution: only relevant knowledge enters
  context).
- **Client-side provider failover:** rejected — the router already provides lightning-fast
  multi-provider failover; duplicating it adds coupling without resilience.

## Trade-offs
- Reasoning quality depends on an uncontrollable free tier (ADR 0005); latency and model
  availability remain external risks accepted for V1.
- Memory improves recall only if episodes are actually recorded from outcomes; the
  `ReflectionService` (writing episodes when trades close) is the writer, and must be
  invoked on a trade lifecycle (e.g. a post-close hook) to populate memory in practice.
- The LLM is nondeterministic; proposals must always pass the deterministic risk gate,
  which holds veto authority.

## Consequences
- `MemoryStore` lives behind a port; the storage backend is swappable (SQLite first).
- `AiOmniRouteReasoner` records failures (`ai_unavailable`) and degrades to `STAND_ASIDE`,
  never garbage and never a direct order.
- Reflection is implemented and **wired into the decision path**: the pipeline
  (`DecisionPipelineService`) invokes reflection automatically whenever a proposal closes
  a trade, writing the outcome to episodic memory. Reflection is out-of-band and sandboxed —
  a reflection failure is logged and swallowed so learning never blocks or corrupts trading.
  An explicit operator run is also exposed (`/v1/reflection/reflect`) for backfilling memory
  from an existing ledger.
- A deterministic backtest replay layer (`BacktestRunner`) drives historical contexts
  through the *live* decision path and produces a per-campaign report. Because the
  simulator is replay-driven and reasoner-agnostic, it allows objective comparison of the
  rule solver vs the LLM reasoner, and (via reflection on the resulting ledger) populates
  episodic memory at scale.
- Live trading never depends on free AI tiers; LLM reasoning is dev/backtest only.