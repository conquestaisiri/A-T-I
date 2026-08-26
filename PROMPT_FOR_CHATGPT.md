# PROMPT FOR CHATGPT — Trading-Intelligence Project Handoff & Design Review

**How to use this:** send this file together with the project folder as a single ZIP, then paste the section between `### START OF PROMPT` and `### END OF PROMPT` into ChatGPT. The prompt below is the full, self-contained instruction.

---

### START OF PROMPT

You are a senior quantitative trading engineer and systems architect. You previously helped me start this project a few weeks ago. I have since brought in a second AI engineering partner (a coding agent with deep context on this exact repository), and we have had several long design conversations. I am now bringing you back in to review everything, learn what we decided, critique it, and help us continue building toward an institutional-grade, industrial trading intelligence system.

A complete ZIP of the repository is attached to this conversation. You can and should read every file you need: the code, the tests, the docs, the config, the research notes. Do not guess about the codebase — read it.

## 1. THE MISSION (unchanged)

Build an Autonomous Trading Intelligence, not another rule-based bot:
- Observe financial markets (real data, streaming).
- Understand market behaviour.
- Reason about opportunities.
- Plan and execute disciplined trades.
- Learn from outcomes and improve continuously.
- The AI is the trader. Rules exist only as safety constraints.

## 2. THE NON-NEGOTIABLE CONSTRAINT

**I have no money for AI usage. Free access only.**
This is not a preference, it is a hard constraint. It shapes everything below and I need your engineering recommendations to respect it.

## 3. CURRENT STATE OF THE REPO (verified, read it yourself to confirm)

- **Backend:** FastAPI, Clean Architecture (`domain` / `application` / `infrastructure` / `presentation` / `services`), Python, Pydantic v2.
- **Working deliverable so far — "Sprint 4A" Context Builder pipeline:**
  - `ObservationEvent` → thread-safe `InMemoryWindowManager` (per-symbol rolling window) → `ContextSnapshot` → `FeatureEngine` (5 features: trend, momentum, volatility, volume, liquidity) → immutable `MarketContext` → `MarketContextCreatedEvent` → `InMemoryEventBus`.
  - Feature registry with dependency/duplicate validation; deterministic, replayable features; strict YAML config validation (`config/context.yaml`).
  - Good unit + integration test coverage including replay determinism.
- **Two parallel ingestion designs that are NOT yet unified:**
  1. The newer observation/context pipeline (`ObservationEvent`, `ObservationAdapter`, real `BinanceAdapter` with exponential backoff reconnect, async `ObservationBus`).
  2. An older market-data pipeline (`MarketEvent`, `StreamMarketDataUseCase`, simulated Binance client, `AsyncQueuePublisher`).
- **`presentation/api/` is an empty stub**; `main.py` only exposes `/health` and `/context/config`.
- **No database, no ORM, no Redis, no persistent storage anywhere.**
- **No Docker, no CI, no git commits yet** (repo initialized but empty history).
- **Docs are strong:** `docs/System_Architecture.md` (~410 lines), `Technical_Blueprint.md`, `Brain.md`, `Knowledge_Model.md`, `Market_Philosophy.md`, `Vision.md`, 3 ADRs, Sprint 4A docs. There are also 5 byte-identical duplicated `System_Architecture - Copy*.md` files and 3 empty doc files (`Architecture.md`, `Principles.md`, `Roadmap.md`) to clean up.
- **`config/sources.yaml` is dead config** — nothing loads it into the `SourceRegistry`.
- **`research/repositories/`** contains 7 large cloned open-source prediction-market trading bots (PolyWeather/Clodds, polybot, polymarket_lp_tool, prediction-market toolkits, etc.) used as reference material.
- **`experiments/decisions/recommended_integrations.md`** already concluded: Redis = now, ClickHouse = later, Vercel SDK = later, Rust = never.

## 4. WHAT MY ENGINEERING PARTNER AND I DISCUSSED AND DECIDED

### 4a. Self-learning framework (adopted from Hermes Agent, Nous Research)
We want the AI to genuinely learn from its own trading outcomes over time. We studied Hermes Agent's self-improvement loop and decided to adopt its **framework only** — not the whole agent application. The four mechanisms we want to port, re-targeted from "conversations" to "trade sessions":

1. **Bounded persistent memory** — a small, hard-capped store of durable lessons (e.g. "vol breakouts in low-liquidity hours underperform 3x"). Cap forces consolidation instead of hoarding. (Hermes uses ~2200-char MEMORY.md + ~1375-char profile, agent-managed via add/replace/remove with dedup + security scanning.)
2. **Procedural skills** — loadable playbooks/procedures (Hermes `SKILL.md` format, progressive disclosure: index lists name+description only, full content loaded on demand). The agent auto-creates one after complex tasks, user corrections, or repeated dead-ends.
3. **Background self-improvement review** — after a session, an auxiliary LLM replay reviews what happened vs. outcome and *proposes* memory writes / skill patches. Must have human-approval gates (`write_approval` pattern) and run on a cheap model to control cost.
4. **Cross-session recall** — searchable store of past sessions/decisions (Hermes uses SQLite + FTS5) so the trader can ask "did we try this before and what happened?"

**Critical adaptation:** Hermes learns from conversational corrections. Our trader learns from market outcomes — noisy, delayed, non-stationary feedback. The framework does not fix a weak reward signal; it only makes the feedback loop persistent. We need your advice on how to define "what was learned" per session so the loop actually compounds instead of memorizing noise.

### 4b. Free AI access (adopted: OmniRoute)
To respect the no-money constraint, we will run **OmniRoute** (free, MIT, self-hosted AI gateway, diegosouzapw/OmniRoute) locally: one OpenAI-compatible endpoint (`localhost:20128/v1`) aggregating 268 providers, 90+ free, with 4-tier automatic fallback (subscription → paid key → cheap → free), multi-account round-robin, circuit breakers, quota tracking, and token compression. This gives us uninterrupted free model access during development.

**Our caveats (validate these):**
- Free tiers are for personal/dev use, not commercial production — accounts get banned. So: **free AI for building and backtesting, never for live execution without a paid-capable fallback path.**
- Free flash-tier models are weak for high-stakes reasoning. The AI decision layer must be **provider-agnostic behind an interface** so production can later swap in paid/self-hosted models with zero rewrite.
- A rate-limit mid-decision is a real financial event. The deterministic side must never depend on an AI call to do its safety job.

### 4c. Architecture principle: the AI is the trader, deterministic code is the workhorse
The user's vision (which I share): build the system **very large** so the AI manages literally everything — but the AI must not be saddled by load. Most of the system (data ingestion, feature computation, risk, execution, monitoring, persistence) is deterministic, scripted, fast, and cheap. The AI is a **thin reasoning layer** that:
- reads compact context snapshots (already produced by the existing pipeline),
- decides whether to act and why,
- submits orders through a deterministic, safety-enforced execution layer,
- and writes lessons through the learning loop.

AI calls should be small and rare (a few hundred tokens per decision), not streaming every tick. Keep decision latency dominated by the deterministic pipeline, not the LLM.

### 4d. Reliability & industrial-grade bar (what "tycoon-grade" means to us)
- **Speed:** the deterministic pipeline should be microseconds-to-milliseconds; the AI call is the only "slow" link and we minimize and parallelize around it.
- **Reasoning:** every decision logged with its context snapshot, model, reasoning, and outcome — full auditability.
- **Execution:** a clean execution layer with idempotency, sequence checking, retry, and explicit fail-safe behavior. The repo already has `MissingSequenceError`/`MalformedPayloadError` concepts — build on them.
- **Risk:** deterministic, hard-coded, non-negotiable safety layer: position sizing, max exposure, max drawdown, daily loss kill-switch. The AI cannot override it. This is the "rules as safety constraints" principle.
- **Observability from day 1:** every signal, decision, order, fill, and lesson journaled. If we can't explain a loss, we can't learn from it.
- **No real capital until the system survives extended paper trading** plus rigorous replay/backtest that does not fool itself (overfitting is the silent killer).

### 4e. My specific engineering opinions (I value these as much as — arguably more than — yours)
1. **Do not over-abstract.** The repo's Clean Architecture is good; keep it, but every module must justify its existence. No extra layers.
2. **Unify the two parallel pipelines** (legacy market-data vs. observation/context) into one observation pipeline before adding anything new. Two half-pipelines are worse than one complete one.
3. **Persistence is the next real dependency** — the learning loop and session recall need a store. The existing decision doc says Redis=now, ClickHouse=later. I lean SQLite first (zero-ops, transactional, FTS5 for recall), Redis later when pub/sub + hot state genuinely require it. Tell me if you disagree and why.
4. **Wire up the dead `sources.yaml` / `SourceRegistry`** — it's a good idea that was never connected.
5. **Phased roadmap, no skipping:** (0) repo hygiene: first git commit, dedupe the copy-pasted docs, fill/remove empty docs; (1) unify pipeline + add persistence; (2) LLM decision layer + router integration behind a provider-agnostic interface; (3) learning loop (memory + skills + background review with approval gates); (4) paper trading with full journaling; (5) risk-hardened execution; (6) tiny live capital only after months of forward results.
6. **The learning loop must be sandboxed:** it can propose memory/skill changes but never touch risk parameters or execution logic without human approval. A self-improving trader that can widen its own risk limits is how you lose the account.
7. **Never let the AI be a single point of failure.** If every model and router is down, the system should do nothing (or de-risk), never do something stupid.

## 5. WHAT I WANT FROM YOU (be specific, be critical, don't flatter me)

Read the whole repo, then deliver:

1. **Architecture critique** — what is genuinely good (keep), what is weak or wrong (change), what is missing for an institutional-grade system. Be honest. If my partner's plan has flaws, say so. If mine does, say so.
2. **A concrete next-90-days build plan** — phases, milestones, deliverables, in dependency order, sized so I can make real progress with a coding agent executing it.
3. **The LLM decision layer design** — the exact interface between deterministic context and the AI trader: what the prompt contains, what the model returns (structured decision schema, not prose), how we handle model fallback/quality, and how we evaluate whether the AI is adding value vs. noise (it must earn its place).
4. **The learning loop design** — concretely: what gets written to memory vs. skills, what defines a "session" for review, how outcomes are attributed to decisions, how we prevent overfitting to noise, and the approval gate UX.
5. **Execution & risk layer design** — order lifecycle, idempotency, sequence checks, kill-switch semantics, position sizing rules. Assume Binance-class REST + WebSocket.
6. **Performance strategy** — where the microseconds are and aren't worth chasing; how to keep AI-call latency from hurting decision latency; caching and async architecture.
7. **Red flags and risks** — including the ethical/ToS realities of free AI in live trading, and how a retail trader with no budget should think about this.
8. **Anything you would add that we haven't thought of.**

Format: start with a short executive summary, then address each numbered point. Use concrete references to files you actually read. Prefer a phased plan with clear "definition of done" per phase. Be direct — I would rather be told "this part is not going to make money and here's why" than be encouraged.

### END OF PROMPT
