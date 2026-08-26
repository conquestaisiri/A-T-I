# ATI OmniRoute Context Continuity — Standing Directive

> **Status:** BINDING — operator directive, persisted 2026-08-19.
> **Priority:** FIRST — read this before any other build work. It outranks
> the Tier-1 reconciliation queue in the backlog (§0-8) and is not
> subordinate to any task boundary.

## The Invariant

Whenever OmniRoute switches the AI provider or model, the AI that answers
must have exactly the same knowledge, context, and memory as the AI that
answered before. The switch must be invisible: the operator must never see
an AI that "forgot" what the previous AI knew.

Formally: **the input to the model is a pure, deterministic function of
durable state — never of the conversational window, never of which
provider/model happens to serve the request.**

## Why This Exists

The operator runs a multi-provider AI router (OmniRoute, `localhost:20128/v1`,
auto-combo failover + `lkgp` session stickiness) to keep the AI available
with no downtime and no errors. That only helps if the switch is seamless.
A provider switch that loses knowledge would be worse than downtime.

The same invariant protects against agent compaction: a new or compacted
agent must reconstruct the same understanding from durable files, not from
a lost conversation.

## What This Means In This Repository

1. **The ATI decision reasoners are already stateless per call by design.**
   `backend/application/decision/prompt_builder.py` is the single source of
   truth (`SYSTEM_PROMPT` v1, `DEFAULT_RECALL_LIMIT`, `build_messages` pure).
   `backend/application/decision/omni_route_reasoner.py`,
   `backend/application/ai/pydantic_ai_reasoner.py` and
   `backend/application/decision/smart_fallback_reasoner.py` (Omega) all
   rebuild the full prompt from durable inputs every invocation via that
   builder:

   - system prompt (static, versioned persona),
   - market context (features, snapshot),
   - risk context,
   - episodic memory via the `MemoryStore` port (SQLite-backed, bounded,
     relevance-filtered).

   This is the correct architecture. It must be preserved: **no reasoner may
   ever rely on conversational state that a provider switch would not see.**

2. **The agent/builder layer reads a canonical standing context first.**
   Every session starts with:
   - `AGENTS.md` → `docs/ATI_BACKLOG.md` → this document.
   The backlog is the durable, anti-compaction memory of the build; this
   document is the binding statement of the continuity invariant.

3. **Continuity must be provable, not assumed.** A regression test must
   assert that identical durable state produces an identical prompt
   regardless of which provider/model is configured. The invariant dies the
   day it is not tested.

## Rules (binding)

- R1. No AI context may live only in a chat window. Anything that must
  survive a provider switch or a compaction is written to a durable store
  or a durable file.
- R2. The prompt a reasoner sends is a pure function of its durable inputs
  (market context, risk context, recalled memory) plus a fixed, versioned
  system persona. Adding new input sources is allowed; depending on
  ephemeral conversation state is not.
- R3. Provider/model configuration changes (OmniRoute `auto/*`, `lkgp`,
  explicit model names) must never change what the AI knows — only how fast
  or how well it answers.
- R4. When this invariant is extended (new reasoner, new memory kind, new
  context source), the determinism test in Rule 3 of the tests suite is
  updated in the same change. Docs drift is a bug.
- R5. OmniRoute stays the preferred gateway when configured (Constitution
  05 §230, 06 §188-197) but remains replaceable: nothing in this repo may
  depend on OmniRoute at the architecture level (Constitution 06 §200-209).

## Enforcement

- This document is referenced from `AGENTS.md` and `docs/ATI_BACKLOG.md`
  so every session encounters it.
- The prompt-determinism test lives in the application test suite and runs
  with the full suite (`py -3 -m pytest`).

## Session Log

- 2026-08-19 — Directive persisted by the chief-architect session at the
  operator's request (operator: "every switched AI must have the same
  knowledge as the last, so it looks like the AI never even switched").
  Next: add the prompt-determinism regression test proving the invariant.