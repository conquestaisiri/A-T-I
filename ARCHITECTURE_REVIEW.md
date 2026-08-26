# Principal Engineering Review — Autonomous Trading Intelligence

**Date:** 2026-08-12 (updated; original 2026-08-05)
**Scope:** Full repository review + update to reflect current state.
**Method:** Read every file in `backend/`, `tests/`, `config/`, `docs/`, `experiments/`, `research/`, and repo root. Empirically verified with the working interpreter (`py -3` = CPython 3.14.3, pytest 9.1.1). **572 tests pass. mypy 135 files clean. ruff clean.** All evidence in Appendix A.
**Reviewer stance:** Principal engineer owning this codebase for five years. Every claim below is either verified empirically or explicitly labeled as judgment.

> **STATUS UPDATE (2026-08-12, re-verified against the running system):** The original review (2026-08-05) described a system that could not import. **That is no longer the case.** This update supersedes the prior 2026-08-12 note with conclusions reached by *executing* the system, not by re-reading claims: `py -3 -m pytest` **696 passed**, `mypy backend` **clean (159 files)**, and a live `TestClient` end-to-end run of the drive → decision → simulator → ledger loop. The stale claims in the body below (5 features, "no ML/no numpy", "no slippage measurement", "no microstructure", "observability never wired") are **corrected** in the Verified Findings section that follows. The current truth is the code plus the empirical evidence in Appendix C. **Gaps G1, G2, G5, G8 have since been fixed (2026-08-12); see the FIXED notes inline. The P1 research factory (phase "Research Truth") is COMPLETE: versioned dataset contract, label-generation framework, costed baseline suite, feature attribution, experiment registry, historical alternative-data storage, regime-conditioned evaluation, and robustness/multiple-testing controls** — labeled, point-in-time data now exists with causal correctness enforced structurally, every model claim is scoreable against the shared costed ruler, and best-of-N selection bias is explicitly reported instead of silently believed.

## Verified Findings (2026-08-12) — evidence-based

**Verification method.** Every claim below was produced by running the interpreter (`py -3`, CPython 3.14.3) against the real bytes: full test suite run, `mypy`, OpenAPI route dump, AST import-graph audit of all 136 modules, source dumps via `print(open(...).read())`, and live `TestClient` calls against `backend.main.app`.

**What the live composition root actually wires (`backend/main.py`).** SQLite repositories for observations, contexts, proposals, ledger, memory, and reconciliation; `PaperFillEngine`; `PaperTradingSimulator(risk_gate, order_gateway, ledger)`; `SupervisorService`; `DecisionPipelineService` with the **deterministic `RuleBasedSolver`**; context builder from `config/context.yaml`; static operator dashboard mounted at `/`. `app.state` keys verified at runtime: `context_builder, context_repository, database, decision_pipeline, fill_engine, ledger_repository, memory_store, observation_repository, proposal_repository, reconciliation_store, reflection, simulator, supervisor`.

**Verified live behavior.** `POST /v1/drive` builds a real `MarketContext` (12 registered features), persists the proposal to SQLite, executes it through the risk gate + simulator, opens/closes positions with OCO brackets, writes ledger rows, and is fully queryable via `/v1/proposals/recent`, `/v1/ledger/open`, `/v1/ledger/recent`, `/v1/simulator`, `/v1/reconcile/*`, `/v1/memory/*`, `/v1/supervisor/*`. The full 21-path OpenAPI surface was dumped and each router is a thin view over `app.state` ports. **The core paper-trading loop works end-to-end.**

**Corrected claims from the body of this document:**
- **Feature count:** 12 features are registered and configured, not 5 (`trend, momentum, volatility, volume, liquidity, sentiment, insider, order_flow, micro_price, regime, book_imbalance, kyle_lambda`); `config/context.yaml` requires every one explicitly (loader rejects unknown names and params).
- **"No ML / no numpy":** false — `numpy` is in the core profile and `application/regime/regime_detector.py` ships a pure-`numpy` 2-state Gaussian HMM + CUSUM changepoint detector.
- **"No slippage measurement":** false — `ExecutionReport` now carries `fee`, `venue`, `is_maker`, `arrival_price`, `latency_ms`, and a `slippage_bps` property.
- **"No microstructure":** false — VPIN toxicity tracker (`risk/vpin.py`), Kyle's λ (`features/kyle_lambda.py`), and square-root market-impact calibration (`execution/market_impact.py`) are implemented and tested.
- **"ObservationBus never wired":** true for the live app, but the durable ingest path (`ContextPipelineService` + `ObservationBus`, bounded queue, at-least-once persistence) exists and is integration-tested — it is simply **not started** in `main.py` (gap G1 below).

**Verified gaps and risks (ranked):**

- **G1 (High) — Durable ingest is unwired.** `ContextPipelineService`/`ObservationBus` are never started in `main.py`. Consequently `POST /v1/drive` never persists observations or contexts: `/v1/events/recent` returns `[]` and `/v1/context/latest` returns `404` immediately after successful drives. The observability endpoints for events/contexts are permanently empty in the deployed app. The bus path is the only place that feeds supervisor freshness (see G2).
  - **FIXED (2026-08-12):** `ContextPipelineService.handle()` is now a public synchronous entry point returning the built `MarketContext`. `main.py` builds one shared context builder + `ObservationBus` + enrichment, wires a `ContextPipelineService` into `app.state.ingest_pipeline`, and the drive route routes through it. Verified live: `/v1/events/recent` count=1, `/v1/context/latest` 200, supervisor healthy after a drive (was `[]`/`404` before). See task P0-015.
- **G2 (High) — Stale-data gate is inert.** `SupervisorService` is wired into the decision pipeline, but `record_observation()` is only called from the unwired `ContextPipelineService`. In the live app the supervisor never sees data, so `check()` is always HEALTHY and the stale-market-data protection never trips.
  - **FIXED (2026-08-12):** the drive path now feeds `record_observation()` for market-data events (same event-type filter; news/macro events never refresh the gate). A stale feed now correctly degrades the supervisor. See task P0-015.
- **G3 (Medium) — Advanced risk layers are dormant.** The VPIN toxicity veto, square-root impact veto, and fractional-Kelly sizing exist in `CircuitBreakerRiskGate` but nothing feeds them (`record_toxicity_flow`, `record_impact_fill`, `update_edge_estimate`, `set_market_stats` have no callers in production). The live gate enforces the six budgets, mandatory OCO brackets, exposure caps, and the 60% rule.
  - **PARTIAL (2026-08-13):** the feeds are now wired. `CircuitBreakerRiskGate` implements a new `RiskFeed` port (`interfaces/risk_feed.py`); `main.py` hoists ONE shared gate instance into the simulator, the ingest pipeline, and the decision pipeline. Ingest feeds VPIN signed flow from TRADE observations carrying an aggressor `side` (`ContextPipelineService`). The decision path feeds realized fills into the square-root impact calibrator, but only when operator-supplied market stats are registered (`RISK_MARKET_STATS` JSON in `.env`; `settings.risk_market_stats`) — never from fabricated data. Fractional-Kelly remains opt-in: `estimate_edge()` in `ReflectionService` derives a `KellyEdgeEstimate` from closed episodic memory and is fed via `RISK_KELLY_FROM_MEMORY` (default OFF) per Constitution §5 (learning must never alter risk parameters without approval). 7 new wiring tests (`tests/application/test_risk_feed_wiring.py`); suite at 787 pass.
- **G4 (Medium) — "AI is the trader" is not yet embodied in the running loop.** `AiOmniRouteReasoner`, `PydanticAIReasoner`, `CcxtOrderGateway`, `CcxtObservationAdapter`, sentiment/EDGAR services, portfolio risk, and the backtest/validation modules are all composed in `bootstrap.py` builders but **none are wired into `main.py`**. The live path uses the deterministic `RuleBasedSolver`. This honors the Constitution's deterministic-first invariant, but the autonomous-intelligence objective is currently operator-driven paper trading.
  - **PARTIAL (2026-08-13):** the loop is now self-feeding. `main.py` starts a `CcxtObservationAdapter` → `ObservationBus` → `MarketLoopService` chain (bus → ingest → decision) behind `CCXT_ENABLED=true` AND `CCXT_SANDBOX=true` (both default off). A shared `operator_lock` (`threading.Lock`) serialises the async market loop against the threadpool drive route so the paper simulator is never mutated concurrently. The live reasoner remains the deterministic `RuleBasedSolver`; the AI reasoners are still not wired (Constitution's deterministic-first invariant + no live AI tier). See task P0-017.
- **G5 (Medium) — Auth inconsistency.** `routes_context` (observability) and `routes_memory` (including the mutating `POST /v1/reflection/reflect`) are **not** behind `verify_api_key`, while drive/decision/supervisor/reconcile are. With `API_KEY` configured, market context and reflection triggers remain open.
  - **FIXED (2026-08-12):** all `/v1/*` routers now carry `dependencies=[Security(verify_api_key)]`; tests assert 401/403 behavior; `.env.example` documents `API_KEY`. See task P0-013.
- **G6 (Medium) — Domain→application dependency.** `domain/context/features/regime.py` imports `backend.application.regime.get_detector` at runtime (verified by AST import graph). `sentiment.py`/`insider.py` import application services only under `TYPE_CHECKING` but rely on module-level `set_service()` singletons. Module-global state is real: regime detector registry, sentiment/insider service instances, OFI tracker, enrichment state. `bootstrap.build_context_pipeline` explicitly resets them between pipelines (documented ADR-0007 discipline), but the pattern violates the layer rule and is easy to misuse.
  - **PARTIAL (2026-08-13):** the runtime dependency is inverted. `regime_detector.py` moved to `backend/domain/context/regime_detector.py` (it is stateful feature estimation, like `micro_price`/`order_flow`); the domain feature now imports from domain and the `backend/application/regime` package is deleted. `bootstrap` still owns the reset discipline. Remaining: `sentiment.py`/`insider.py` `set_service()` singletons (TYPE_CHECKING-only, no runtime layer violation) stay documented under G4/G7 quarantine until the AI/sec-EDGAR services are wired.
- **G7 (Low) — Dead/duplicated code.** `regime.py` ends with an unreachable duplicate `return ContextFeature(...)` block (verified in source). Modules with production tests but no live wiring: `application/observation/consumer.py`, `application/validation/*`, `application/backtest/*`, `application/portfolio/portfolio_risk.py`, `infrastructure/observation/binance_adapter.py`.
  - **PARTIAL (2026-08-13):** the dead duplicate `return ContextFeature(...)` block in `regime.py` has been removed (verified clean in source). The dormant modules listed above remain quarantined: they are exercised by tests but not wired into the live composition root, which is documented rather than silently live.
- **G8 (Low) — Feature warm-up noise.** A single-event snapshot makes `trend`/`momentum`/`volatility` raise; the engine isolates the failure but logs a full traceback per call until ≥2-3 price observations accumulate. Functional, noisy.
  - **FIXED (2026-08-12):** `FeatureEngineImpl` now logs expected `ValueError` warm-up/data conditions as a one-line `WARNING` (no traceback); genuine failures keep `logger.exception`. Failures still land in `ContextHealth.errors`. See task P0-016.
- **G9 (Low) — Repository hygiene.** 47 files modified (+3316/−416) and many untracked artifacts sit in the working tree (ATI_* governance files, `.docx`/`.pdf` blueprints, stray `full*.txt`/`stage0.txt`/`t1.txt`). `.env.example` documents `API_HOST=0.0.0.0` but not `API_KEY`, and the `settings` default is `127.0.0.1`.

**What is genuinely strong (verified):** clean port/adapter layering; immutable domain models; replay-deterministic simulator (event-time PnL windows, OCO brackets, prorated partial-close fees); a serious multi-layer risk gate; explicit bounded backpressure on `ObservationBus`; reflection written only from closed-trade outcomes (idempotent by `ep-<trade_id>`); reconciliation wired and functional; a core-vs-optional dependency split enforced by a subprocess smoke test (core imports must load without torch/transformers/ccxt/openai/pydantic-ai/edgar/pandas/riskfolio/cvxpy); 60 test files covering every layer including an integrity manifest test.

**Recommended next actions, in order:** (1) start `ContextPipelineService` in `main.py` (feed observations + contexts to the SQLite repositories and freshness to the supervisor) or explicitly persist on the drive path — **DONE, task P0-015**; (2) decide whether `/v1/reflection/reflect` and the observability routes require the API key — **DONE, task P0-013**; (3) wire the CCXT adapter into the ingest path behind the sandbox flag to make the loop self-feeding — **DONE, task P0-017**; (4) remove the dead block in `regime.py` and either wire or quarantine the dormant modules — **DONE (2026-08-13):** `regime.py` dead block removed; risk-gate feeds (toxicity/impact) wired through a shared gate, Kelly feed quarantined behind `RISK_KELLY_FROM_MEMORY=false`; the remaining dormant modules stay quarantined-by-documented-design; (5) commit or clean the working tree and document `.env.example`.

---

# Part I — Executive

## 1. Executive Summary

ATI is a **working autonomous trading intelligence scaffold** — not yet a profitable trader, but a real, tested, importable system with a complete observation→context→reason→risk→simulate→reflect pipeline, an AI reasoner (PydanticAI), and a unified crypto venue adapter (CCXT, 100+ exchanges).

**The engineering thinking is above average and now matched by execution.** Clean Architecture layering, immutable domain models, port/adapter separation, deterministic feature computation, failure isolation, episodic memory, reflection, and a coherent cognitive canon are all real and correctly done.

**The engineering execution is now solid:**

- **The codebase imports and runs.** 572 tests pass. mypy 135 files clean. ruff clean. (`py -3` = CPython 3.14.3, pytest 9.1.1).
- **A complete cognitive pipeline exists.** Observation (Binance + CCXT adapters) → Context (5 deterministic features) → Reason (rule-based + PydanticAI) → Risk (circuit-breaker gate with veto authority) → Simulate (deterministic paper fill engine) → Ledger (SQLite) → Reflection (episodic memory).
- **Multiple reasoning paths.** `RuleBasedSolver` (deterministic), `AiOmniRouteReasoner` (LLM via OmniRoute), `PydanticAIReasoner` (structured-output LLM).
- **SQLite persistence** for observations, contexts, proposals, ledger, and memory.
- **Operator dashboard** (`/`) + drive API (`POST /v1/drive`) + memory/reflection routes.
- **16 ADRs** governing architecture, integration, and evolution.

**What's missing (honestly):**

- **No live execution.** Paper-only. The `PaperFillEngine` fills against a mark price; the `CcxtOrderGateway` exists but is not wired to the live path.
- **No ML.** No numpy, pandas, sklearn, or any ML library. The feature set is 5 deterministic technical indicators. No alternative data. No microstructure signals.
- **No slippage measurement.** `ExecutionReport` has no fee, venue, or maker/taker field. Every bps-of-slippage claim is currently unfalsifiable.
- **No multi-venue order flow imbalance** or other microstructure alpha. The order book adapter uses snapshots, not L2 deltas.

**Final verdict:** The foundation is stabilized (Phase 0-1 complete). The next frontier is **profitability**: better data (alternative + microstructure), better execution (maker routing + measurement), and better alpha (ML features + regime detection). The sequence is: **measure → reduce cost → add alpha → optimize.** Full reasoning throughout.

---

## 2. Repository Understanding

### What this repository is
- A **Python 3.14 / FastAPI** backend intended to become an Autonomous Trading Intelligence ("ATI").
- Currently implements **Sprint 4A**: the Context Builder — transforming `ObservationEvent` → rolling window → `ContextSnapshot` → 5 deterministic features → immutable `MarketContext` → event bus publication.
- Backed by a substantial philosophical/architectural canon: `Vision.md`, `Brain.md` (cognitive cycle), `Knowledge_Model.md` (5-layer knowledge hierarchy), `Market_Philosophy.md`, `System_Architecture.md`, `Technical_Blueprint.md` (6 services), 3 ADRs.

### What is actually implemented (by layer)

| Layer | Contents | Status |
|---|---|---|
| Domain | `observation/` (event, adapter_interface), `context/` (snapshot, market_context, features, registry, errors, events), `decision/` (proposal, action, risk_context), `execution/` (order, execution_report, position, trade_record) | Complete; tested |
| Application | `context_builder_impl`, `feature_engine_impl`, `window_manager_impl`, `decision/` (rule_based_solver, omni_route_reasoner, pydantic_ai_reasoner), `pipeline/` (decision_pipeline_service, context_pipeline_service), `reflection/`, `risk/` (circuit_breaker_risk_gate, vpin), `execution/` (reconciliation_service, market_impact), `validation/` (purged_cv, triple_barrier, adwin), `simulation/`, `backtest/`, `interfaces/` (13 ports) | Complete; tested |
| Infrastructure | `config/` (settings, context_loader), `event_bus/`, `observation/` (binance_adapter, ccxt_adapter, observation_bus), `execution/` (ccxt_gateway), `sqlite/` (6 repositories: observation, context, proposal, ledger, memory, reconciliation) | Complete; tested |
| Presentation | `routes_drive.py`, `routes_decision.py`, `routes_context.py`, `routes_memory.py`, `routes_supervisor.py`, `routes_reconciliation.py`, `main.py` (FastAPI + lifespan + static dashboard) | Complete; tested |
| AI | `pydantic_ai_reasoner.py` (structured-output LLM), `omni_route_reasoner.py` (OmniRoute LLM), `rule_based_solver.py` (deterministic) | Complete; tested |
| Memory | `memory_repository.py` (SQLite, idempotent), `reflection_service.py` (bounded episodic memory) | Complete; tested |
| Tests | 38 test files (unit + integration + application + domain + infrastructure + presentation), 572 tests passing | Green |
| Config | `context.yaml`, `.env`, `settings.py` (pydantic-settings with CCXT + AI config) | Live |
| Docs | 6 Constitution docs, 16 ADRs, Vision/Brain/Knowledge Model/Market Philosophy/System Architecture/Technical Blueprint, sprint-4a docs | Current |
| Experiments | `recommended_integrations.md`, `engineering_playbook.md`, `ml_infrastructure_landscape.md`, integration synthesis | Current |
| Research | `alternative-data-research.md`, `Risk-Management-Research.md`, `execution-and-order-routing-landscape.md`, `MARKET_DATA_SOURCE_MATRIX.md` | Current |

### The pipeline (resolved)

The two-pipeline ambiguity from the original review is **resolved**. Pipeline B (legacy `market_data`) was deleted. The single pipeline of record:

```
CcxtObservationAdapter / BinanceAdapter (WebSocket/REST)
    → ObservationEvent (normalized, aware-UTC)
    → ObservationBus (bounded asyncio.Queue, backpressure)
    → ContextBuilderImpl.handle
        → InMemoryWindowManager (rolling window)
        → FeatureEngineImpl (5 deterministic features, failure-isolated)
        → MarketContext (immutable)
    → DecisionPipelineService
        → SupervisorService (kill switch + stale-data gate; HALTS/DEGRADES → no proposal)
        → AIReasoner (RuleBased / OmniRoute / PydanticAI) → DecisionProposal
        → CircuitBreakerRiskGate (veto authority: VPIN toxicity, square-root impact,
                                 mandatory brackets, circuit breakers, sizing caps)
            → approved/rejected/reduced
        → PaperTradingSimulator → PaperFillEngine → ExecutionReport
        → SqliteLedgerRepository (durable)
        → ReconciliationService → SqliteReconciliationRepository (venue truth vs internal)
        → ReflectionService → SqliteMemoryRepository (episodic)
```

**Live execution path** (built, not yet wired to live trading):
```
CcxtOrderGateway (CCXT async, 100+ venues, sync→async bridge)
    → ExecutionReport (with status mapping: filled/partial/cancelled/rejected)
```

**What's measured:** 572 tests, mypy 135 files, ruff clean.

---

## 3. Product Understanding

### What the product is supposed to be
Per `Vision.md`, `Brain.md`, and the repo's own identity documents: an **Autonomous Trading Intelligence** — not a rule-based bot. The AI observes markets, understands behavior, reasons about opportunities, plans, executes disciplined trades, learns from outcomes, and improves continuously. **The AI is the trader; rules exist only as safety constraints.**

The stated non-goals (from `Market_Philosophy.md` and `Vision.md`) are equally important: no strategy-copying, no indicator-mashups, no over-optimized backtests, no indicator bot.

### What the product actually is today
A working autonomous trading intelligence with a complete cognitive pipeline (observe→context→reason→risk→simulate→reflect→memory), three reasoning backends (deterministic, OmniRoute LLM, PydanticAI structured-output LLM), CCXT unified venue adapter (100+ exchanges), SQLite persistence, episodic memory, reflection, and an operator dashboard. Paper-trading only — no live execution yet. The delta between "product on paper" and "product in code" has narrowed dramatically; the remaining gap is **profitability** (better data, better execution, better alpha).

### The strategic question — RESOLVED
The AI entry-point question is resolved in ADR 0006: **AI enters at the decision-proposal stage.** It reasons over immutable `MarketContext` and produces proposals; deterministic code (features, risk gates, execution) is the workhorse. This reconciles the vision and the code. The risk gate holds veto authority — AI proposes, deterministic code disposes.

---

# Part II — Reviews

## 4. Architecture Review

### What is genuinely good
- **Clean Architecture layering is real, not cosmetic.** Domain models import nothing from application/infrastructure. Ports (interfaces) live in application, implementations in infrastructure. Correctly done and rare in practice.
- **Immutable domain models throughout.** `ContextSnapshot`, `MarketContext`, `ContextFeature`, `ContextHealth`, `FeatureExecutionResult`, `ContextSettings`, `FeatureSettings` are `frozen=True` dataclasses — the correct foundation for concurrent reasoning pipelines.
- **Deterministic computation timestamps.** `MarketContext.created_at` derives from `snapshot.end_timestamp`, not wall clock — enables replay determinism, which the tests verify.
- **Failure isolation in the feature engine.** A throwing feature is captured, logged, recorded in `ContextHealth`, and does not kill the pipeline.
- **Startup config validation** via FastAPI lifespan + strict YAML loader with per-feature parameter validation.
- **The cognitive canon is coherent.** Brain → System Architecture → Technical Blueprint form a consistent, technology-agnostic decomposition (Observe→Understand→Reason→Plan→Decide→Execute→Reflect→Learn).

### Where it is structurally weak
- **`source_registry.py` is a dead singleton.** Nothing calls `load_from_dict`, nothing reads `sources.yaml`, no infrastructure loader exists. The single `_instance` pattern is global mutable state — contrary to the repo's own standards.
- **`backend/application/interfaces/` is inconsistent.** Two naming conventions coexist (`ContextBuilder`, `FeatureEngine`, `WindowManager`, `EventBus` vs `IExchangeClient`, `IEventNormalizer`, `IMarketDataPublisher`). A symptom of the two-pipeline split.
- **Duplicate responsibilities:** three pub/sub abstractions (`ObservationBus`, `InMemoryEventBus`, `AsyncQueuePublisher`), two exchange-adapter abstractions (`ObservationAdapter` vs `IExchangeClient`), two domain event models. Clean Architecture permits this *temporarily*; keeping it forever does not.
- **`configure()` in `window_manager_impl.py:39` is a no-op legacy method** retained "for backward compatibility" — dead interface surface.
- **`extract_symbol` in `features/_utils.py:12` is never called** — dead code.
- **`allow_mutation = False` in `observation/event.py:35`** triggers a Pydantic v2 deprecation warning (`allow_mutation` was removed; use `frozen=True`/`ConfigDict`). Domain immutability is inconsistent (event is a Pydantic model, not a frozen dataclass).
- **Event bus is synchronous** (`InMemoryEventBus.publish` runs handlers in-line). Fine for tests; does not match the "out-of-band AI reasoning" principle in the playbook.

---

## 5. Codebase Review

### Strengths
1. **Vision discipline.** Every major doc answers "what exists to improve decision quality?" The repo refuses strategy-copying, indicator bots, and over-optimized backtests.
2. **ADR trail exists** (Clean Architecture, FastAPI, Observation Layer) — small but real governance.
3. **Test design quality is high in intent.** Replay-determinism, concurrency, failure-isolation, config-validation, immutable-snapshot tests. Whoever designed these tests understood what matters.
4. **Feature implementations are small, focused, parameter-driven.** 5 features in ~280 lines, shared extractors in `_utils.py`.
5. **Honest documentation of trade-offs** (Redis now / ClickHouse later / Rust never) grounded in real research of prediction-market bots.
6. **Engineering Playbook** (`experiments/lessons/engineering_playbook.md`) is the most actionable artifact in the repo: venue-agnostic execution, 4-layer risk breakers, OLAP for ticks, out-of-band AI, dynamic sizing. It should be promoted into ADR/architecture docs.

### Weaknesses
1. **Repo cannot import.** Three confirmed bugs (see §7). Everything downstream is blocked.
2. **No committed history.** `git status`: branch `master`, "No commits yet", all files untracked. No undo, no blame, no protection.
3. **Two divergent pipelines** with overlapping models — the highest-cost ambiguity in the codebase.
4. **Empty presentation layer.** The only HTTP surface is `/health` and `/context/config`. No API for the AI, dashboard, or operator. `README.md` claims `localhost:8000/docs` but no business routers exist.
5. **No persistence of any kind.** No DB layer, no SQLite, no schema. Market contexts are created and discarded.
6. **No AI/LLM code at all.** The gap between stated vision and actual state is enormous.
7. **No CI, lint, type-check, or formatting config.** `requirements.txt` is runtime + pytest only.
8. **No Docker / deployment story.** The research repos all have deployment infrastructure; this repo has none.
9. **`sources.yaml` + `SourceRegistry` are fiction.** Config exists, registry exists, loader does not.
10. **`experiments/` contradicts the live code.** `recommended_integrations.md` says "Integrate Redis NOW"; the codebase is in-memory with no bridging plan.

---

## 6. User Experience Review

**End-user / operator UX: nonexistent.** No dashboard, no API, no CLI, no way to see a `MarketContext`. The research repos (PolyWeather, CloddsBot) show what a good operator console looks like; nothing is ported. A trading system whose operator cannot see what it is doing is not a trading system — it is a hazard.

**Developer UX: a good starting point** (immutable models, clear interfaces, good docstrings) undermined by:
- The import breakage (nothing runs, so every edit is unverifiable).
- Missing README quickstart for the observation pipeline.
- No lint/typecheck/format commands.
- No `make`/task runner / justfile.
- Interpreter ambiguity: `python` on PATH resolves to a hermes-agent venv (Python 3.11, no pytest); the suite requires `py -3` (CPython 3.14.3). `README.md` does not document which interpreter to use.

**The "AI UX":** the system is supposed to be explainable ("why this trade", "what evidence"), but no serialization/query path exists to retrieve an explanation. `MarketContext.as_dict()` is a start; there is no explanation log.

**Recommendation:** defer all frontend work. First deliverable is an **observability API + log**: stream `MarketContextCreated` events to structured logs and a simple `/context/latest?symbol=` endpoint. That is the minimum viable operator UX.

---

## 7. Engineering Principles Review

The repository's engineering principles (simplicity over cleverness, architect-first, single responsibility, avoid global mutable state, readable over clever) are **good and should be kept verbatim.** The review's critique is that the *execution* violates them in specific, fixable places:

| Principle in AGENTS.md | Where the repo violates it today |
|---|---|
| "Avoid global mutable state" | `SourceRegistry` singleton (`_instance`), never populated |
| "Every module has a single responsibility" | Three bus abstractions; two pipelines; mixed interface naming |
| "Avoid duplicated logic" | Two event models, two adapter abstractions, `extract_symbol` + dead `configure()` |
| "Runnable before ambitious" (implied) | Repo cannot import; Sprint 4A labeled "Complete" |
| "Docs must match reality" | Empty `Roadmap.md` while README overpromises; dead `sources.yaml` |

The principles are correct; the discipline is the gap. This is encouraging — fixing execution is easier than fixing philosophy.

---

## 8. Product Principles Review

The product principles are the best part of this repository:

- **"The AI is the trader; rules are safety constraints."** — A defensible, ambitious, coherent thesis. Keep it.
- **"Learn from outcomes, not strategies."** — Correct for prediction markets, where copying strategies is noise.
- **"No over-optimized backtests."** — Correct and rare.

But two product principles are **missing and must be written down**:

1. **The free-tier constraint must be productized.** The hard constraint (no money for AI — free access only via OmniRoute `localhost:20128/v1`, dev/backtest only) is a *product constraint*, not just a budget detail. It should be an explicit ADR with a degradation policy: what happens when the free endpoint vanishes, throttles, or gets rate-limited *during a paper-trading campaign*? Silent degradation of the reasoning layer is the failure mode to design against.
2. **A definition of "done for the AI."** When does the AI's reasoning get trusted enough to pass a risk gate? The repo needs a written graduation policy (e.g., N paper-trading decisions with calibrated confidence vs. outcomes before any influence on risk parameters).

---

## 9. Dependency Review

### Runtime dependencies (`requirements.txt`)
Minimal and defensible: `fastapi`, `uvicorn`, `pydantic` v2, `pydantic-settings`, `websockets`, `PyYAML`, `python-dotenv`, `pytest` + `pytest-asyncio` for dev. **No unnecessary dependencies.** This is genuinely good discipline.

### Verified environment split (important)
- `py -3` = CPython 3.14.3 with pytest 9.1.1 — the suite's target environment (all `.pyc` artifacts are `cpython-314`).
- `python` on PATH = `C:\Users\USER\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe` (3.11.15, no pytest) — an unrelated agent runtime that shadows the project interpreter.
- `README.md` does not document this. **An environment documentation fix is required before anyone new can run this repo.**

### Dependency risks
1. **The hermes-venv shadowing** means `python -m pytest` fails with `ModuleNotFoundError: pytest` — an immediate onboarding trap.
2. **Pydantic v2 deprecation** (`allow_mutation`) is a warning now, a break on next major version. Low cost to fix; high cost if ignored for a year.
3. **No pinning strategy.** `requirements.txt` uses loose constraints; no lockfile (`uv`/`pip-tools`). For a project that must replay deterministically, dependency drift is a silent correctness risk.
4. **The OmniRoute gateway is not a declared dependency** — it is an assumption in research notes. It needs to be an adapter behind an interface (it will be, per the architecture), not a hardcoded endpoint.

---

## 10. External Ecosystem Review

Seven cloned repos live under `research/repositories/`. Subagent-verified summaries:

| Repo | Language/Stack | Lesson that matters for ATI |
|---|---|---|
| `polybot-main` | Java 21/Spring | Paper-mode default; `X-Polybot-Live-Ack` header guard on live mode; ClickHouse+Redpanda+Grafana stack; 30s order monitoring; settlement scheduler |
| `polymarket_lp_tool-main` | Python + Rust | Anti-sniping, idempotent order replace, post-only orders, reward-band geometry; EIP-712 parity gap between Python/Rust is a cautionary tale for dual-language designs |
| `Prediction-Markets-Trading-Bot-Toolkits-main` | Rust | **Critical evidence:** README claims "7 live venues" but code hardcodes Polymarket everywhere (`strategy.rs:106`, `risk_guard.rs:141`, `position_monitor.rs:121`); Kalshi adapter is a TODO (`cross_market_arb.rs:4`). Venue-agnosticism is *claimed*, not *built* — even in mature projects. ATI should not repeat this gap. |
| `CloddsBot-main` | TS/Node (MIT, npm `clodds`) | "Claude + Odds" AI trading terminal; Express+WebSocket gateway; 119+ skills, 21 messaging channels; 12-day hackathon build. Proof that a chat+terminal AI layer over prediction markets is buildable quickly — but also a demonstration of why skills-in-a-chatbot is not a trading architecture |
| `Awesome-Prediction-Market-Tools-main` | Directory | ~100 curated tools across 18 sections — the ecosystem map |
| `PolyWeather-main` | Python | DEB fusion, settlement-faithful calibration, SSE realtime, commercial subscription loop; market layer removed in v1.7.0. Its tests appear in root `.pytest_cache` — evidence of prior test runs from this root |

**Ecosystem conclusion:** the market is young, fragmented, and full of claimed-but-unbuilt venue abstraction. ATI's instinct to research rather than reinvent is correct. The specific, verified takeaway is: **"venue-agnostic" must be an implementation property (an adapter per venue behind one port), not a README claim** — the Rust toolkits are proof that even good projects ship the claim without the code.

---

## 11. Outsourcing Opportunities

**What should never be built internally** (per the research):

1. **Venue client adapters** — wrap the venue's SDK; do not write raw REST/WS clients when SDKs exist.
2. **Settlement/calendaring** — polybot shows settlement handling is fiddly; outsource to the venue's data feed + a thin reconcile.
3. **OLAP / tick storage** — ClickHouse/DuckDB, never a home-grown column store.
4. **Multi-process pub/sub** — Redis (or NATS) rather than a bespoke bus, once past V1 in-process.
5. **Message/chat infrastructure** — CloddsBot-style chat layers (Discord/Telegram/webchat) should be thin connectors, not products.
6. **LLM orchestration frameworks** (LangChain etc.) — avoid; a thin OpenAI-compatible client behind the Decision Proposal schema is simpler and matches Clean Architecture.

**What should be kept internal** (strategic core): the cognitive pipeline (observe→context→reason→plan→decide), the risk layer (veto authority — never outsource safety), the trade outcome ledger and learning loop, and the data model. These are the product.

---

## 12. Internal Systems — Keep / Remove / Replace

**Keep (the core is right):** Clean Architecture layering, immutable domain models, feature engine with failure isolation, deterministic timestamps, `context.yaml` strict loader, the cognitive canon, the playbook, ADR discipline.

**Remove (dead weight):**
- Legacy `market_data` pipeline: `backend/domain/market_data/`, `backend/application/market_data/`, `backend/application/interfaces/{exchange_client,event_normalizer,event_publisher}.py`, `backend/infrastructure/adapters/`, `backend/infrastructure/publishers/`, `backend/services/market_data_worker/`. (Preserve lessons in the playbook first.)
- `SourceRegistry` + `sources.yaml` (or wire it; wiring now adds complexity without a consumer).
- No-op `configure()`; unused `extract_symbol`.
- 5 duplicate docs, 3 empty docs, one of CLAUDE.md/AGENTS.md (byte-identical).
- `research/repositories/` from version control (submodules, sibling folder, or gitignore).

**Replace:**
- Three bus abstractions → **two explicit roles, one implementation each**: async ingest bus (`ObservationBus`) and sync in-process event bus (`InMemoryEventBus`). Document the distinction; do not duplicate.
- Pydantic event config → `frozen=True`/`ConfigDict`.
- The `__import__` hack in `test_context_builder.py` → normal imports.

---

## 13. Technical Debt (inventory)

| Item | Location | Severity | Cost to fix |
|---|---|---|---|
| `DuplicateFeatureError`/`FeatureRegistrationError` imported but never defined | `backend/domain/context/feature_registry.py:14`, `errors.py` | **Critical** — breaks all imports | Trivial (add 2 exception classes) |
| Wrong relative import depth `..domain` | `backend/infrastructure/observation/observation_bus.py:12` | **Critical** — breaks observation layer | Trivial (`...domain` or absolute) |
| Wrong relative import depth `..infrastructure` | `backend/application/observation/consumer.py:12` | **Critical** — breaks consumer | Trivial (`...infrastructure` or absolute) |
| `allow_mutation` deprecated | `backend/domain/observation/event.py:35` | Low | `frozen=True`/`ConfigDict` |
| Two parallel pipelines | `market_data/` vs `observation/` | High | Delete legacy pipeline |
| Dead `SourceRegistry` singleton + `sources.yaml` | `backend/application/source_registry.py`, `config/sources.yaml` | Medium | Wire or delete |
| No-op `configure()` | `backend/application/window_manager_impl.py:39` | Low | Delete + update interface |
| Unused `extract_symbol` | `backend/domain/context/features/_utils.py:12` | Low | Delete |
| Mixed interface naming | `application/interfaces/*` | Medium | Normalize |
| 5 identical duplicate docs | `docs/System_Architecture - Copy*.md` | Low | Delete 5, keep 1 |
| 3 empty docs incl. `Roadmap.md` | `docs/` | **High (Roadmap)** | Fill or delete |
| 345 `.py` + 1137 Java/TS/Rust files vendored | `research/repositories/` | Medium | Submodule/gitignore/sibling |
| Interpreter shadowing (hermes venv on PATH) | environment, not repo | High (onboarding) | Document `py -3`; add `.venv` guidance |

---

## 14. Architectural Debt

Beyond itemized tech debt, the *structural* debts are:

1. **The missing join.** Observation → ContextBuilder is unimplemented ("Remaining Work" per `implementation_summary.md`). The system's own architecture is not wired at its one critical seam.
2. **No Decision Proposal schema.** `Brain.md`/`System_Architecture.md` describe Reasoning/Planning/Decision engines but no interface, message schema, or serialization contract. This is the highest-leverage missing artifact (§32).
3. **No persistence contract.** There is no repository port in application, no schema, no storage abstraction. Every future feature (history, learning, explanation) will retrofit storage — the most expensive kind of addition.
4. **Learning/execution layers are prose.** No interfaces, no ADRs, no data model for order/position/risk/ledger.
5. **The free-tier AI constraint is not an ADR.** The most binding constraint on the product's future has no decision record.

---

## 15. Product Debt

1. **No definition of "what the product demonstrates next."** The Vision is a direction; there is no product milestone smaller than "autonomous intelligent trader." `Roadmap.md` is empty.
2. **No demo/observation path.** Nothing lets a user (or the developer) see the system think. The product is unshowable.
3. **No decision on the AI entry point** (§3). Until this is answered, the product oscillates between two contradictory identities.
4. **The learning loop has no first artifact.** Hermes-style memory was researched, but the first learning artifact (a trade outcome ledger) requires an execution loop that does not exist.

---

## 16. UX Debt

1. Zero operator visibility (no API, no log format, no dashboard, no CLI).
2. No onboarding path for a new developer (broken imports, shadowed interpreter, empty README quickstart).
3. No explanation path for the AI's future decisions (no explanation log, no serialization contract).
4. No runbook/operations documentation (how to run worker, adapter, monitoring).

---

## 17. Testing Review

**What exists:** 8 unit test files + 1 integration file covering window manager, feature registry, feature engine, context builder, features, config loader, event bus, and full pipeline-from-config. Good coverage *intent*.

**Hard blocker:** the suite cannot run. `py -3 -m pytest --collect-only` fails at `conftest.py` import (chain: `bootstrap → feature_engine_impl → feature_registry → errors`) with `ImportError: cannot import name 'DuplicateFeatureError'`. `test_feature_registry.py` imports the same nonexistent names. **Every test is currently broken**, including those that don't test the registry.

**Other observations:**
- No tests for the observation layer (`binance_adapter.py`, `observation_bus.py`, `normalizer.py`, `worker.py`, `use_cases.py`). The ingestion half of the system is untested.
- No API tests (presentation empty anyway).
- No tests for `source_registry.py` (dead code).
- `test_context_builder.py` uses `__import__(...).build_context_pipeline` — a smell indicating someone worked around an import problem rather than fixing it.
- `asyncio_mode = auto` in `pytest.ini` is correct for the async code.

**Assessment:** the test *intent* is excellent; the test *state* is fiction until the import gate passes. The single most valuable engineering action in this repository is: fix imports → run the suite → make it green → keep it green with CI.

---

## 18. Security Review

- **No secrets in the repo.** `.env.example` exists, `.gitignore` covers `.env`. Verified clean. Good.
- **No credential handling code at all** — because there is no exchange/API integration yet. The risk is future: `BinanceAdapter` will need API keys; the venue-agnostic design must inject credentials via env/settings, never hardcode.
- **The dead config pattern is a latent risk.** `sources.yaml` + `SourceRegistry` are a template for "config that isn't actually loaded." When real credentials arrive, a config that *looks* loaded but isn't could silently route to the wrong venue or mode.
- **No input validation on the WS path** — but there is no WS path yet. When the adapter is wired, validated payloads, schema-typed events, and rate-limit/backpressure handling must be in place.
- **The free-tier AI endpoint** (`localhost:20128/v1`) is a local gateway — acceptable for dev; the policy that live trading never depends on free providers must be written down (§8).
- **The playbook's risk breakers (daily 5% / monthly 15% / max DD 25% / total halt)** are the real security layer for this product. They do not exist in code yet; they must be a deterministic, decoupled, veto-power gate (§30).

---

## 19. Performance Review

- **Current scale is correct for V1.** Single process, in-memory, per-symbol rolling windows, 5 features over ~1e2 events. Fine for one symbol at low frequency.
- **The known hotspot:** `InMemoryWindowManager.add` sorts the full per-symbol list on every insert — O(n log n) per event. At Binance BTCUSDT trade rates (~50–200/s), a 5-min window holds ~15k–60k events; sort-per-insert becomes the bottleneck. Not a problem for Sprint 4A; a real problem for the playbook's "line-rate, sub-50ms" goal. Fix with sorted insertion (bisect) using the monotonic-timestamp assumption.
- **Backpressure:** `ObservationBus` uses `asyncio.Queue(maxsize=0)` — unbounded. Under a burst, memory grows without limit. Needs a bounded queue + explicit drop/block policy.
- **Silent data loss by design:** `AsyncQueuePublisher` drops the *oldest* event when full. For a trading system this is questionable; dropping newest or applying backpressure is usually safer. (This is legacy-pipeline code slated for deletion, but the lesson carries to the new bus.)
- **Performance philosophy is right:** "Optimize only where meaningful" is in the standards; the O(n log n) note is the only place worth a fix now.

---

## 20. Scalability Review

- **Multi-process / multi-symbol is impossible today** (in-memory bus, in-memory windows). The docs target multi-process deployment; nothing implements it.
- **The plan acknowledges the right upgrades:** Redis for the bus, ClickHouse for ticks, Rust "never unless latency demands." Sound choices. The missing piece is *when* — there is no phased scalability plan tied to milestones.
- **The SQLite-first decision is correct** for V1 (single process, zero ops, file-backed) — and it *contradicts* the "Integrate Redis NOW" note in `recommended_integrations.md`. That note predates the SQLite decision; the contradiction should be resolved in an ADR so the docs stop arguing with each other.
- **Recommendation:** treat current scale as correct for V1; do not pre-optimize; make the O(n log n) insert and the unbounded queue the two known-and-accepted limits until they matter.

---

## 21. Reliability Review

- **The system cannot be reliable because it cannot run.** Reliability starts at "imports and starts cleanly."
- **No backpressure, no retry, no circuit-breaking** in any live path (the only breaker is the feature-engine failure isolation, which is a per-feature guard, not a pipeline guard).
- **No persistence means no recovery.** A crash loses everything; there is no replay-from-store, which is exactly the property a deterministic observation pipeline needs.
- **The deterministic timestamp design is a reliability asset** — replay can reconstruct state from stored events. That is the single best argument for persisting observations early.
- **Recommended reliability posture for V1:** at-least-once ingestion to SQLite, deterministic replay from stored observations, structured logging, and a health endpoint that reports pipeline lag and bus depth.

---

## 22. Maintainability Review

- **By construction, the architecture is maintainable** — small focused modules, ports/adapters, immutability. The layering will survive contact with new features better than most codebases.
- **The current state is not maintainable** because it isn't importable: no one can run a test, so every change is unverifiable, and the "Complete" label on Sprint 4A erodes trust.
- **Maintainability enablers missing:** git discipline (zero commits), CI as a merge gate, lint/typecheck, a decision log for the AI/learning approach, an actual roadmap, and moving research out of the tree.
- **Doc drift is a maintainability tax:** 5 identical copies, 3 empty files, README overpromising, and `experiments/` contradicting the code all cost future engineers trust.

---

## 23. Modularity Review

- **The domain layer is excellently modular** — small, cohesive, dependency-free.
- **The application layer is the weak spot:** `interfaces/` has two naming conventions, `application/context/` and `application/interfaces/` overlap, and `source_registry.py` is a hidden singleton. The observation consumer and context bootstrap are separate trees that should be one clearly documented flow.
- **Modularity verdict:** domain = keep as is; application = needs a documented consolidation; infrastructure = needs one dead pipeline removed; presentation = does not exist yet and should be added as a thin HTTP adapter over application services (not business logic).

---

## 24. AI Architecture Review

- **Current AI surface: none.** No model client, no prompt layer, no context serialization for an LLM, no tool definitions, no agent loop.
- **What an AI can build on:** `MarketContext` (immutable, serializable via `as_dict()`), `MarketContextCreatedEvent`, the feature set, and a coherent cognitive vocabulary.
- **The critical missing bridge:** between `MarketContext` and "reasoning." There is no Decision Proposal interface, no message schema, no serialization contract, no reasoning service.
- **The Hermes decision is made — and it is the right one.** Adopt only Hermes's learning *framework* (bounded memory files, procedural skills, background review loop with write-approval gates, cross-session SQLite recall) — because Hermes learns from conversations while ATI learns from noisy/delayed market outcomes. This is written down in research; it needs to become an ADR.
- **Recommendation — the highest-leverage artifact in the project:** define the **Decision Proposal schema** (hypotheses with evidence + confidence + uncertainty per `System_Architecture.md` §3) before any LLM integration. Everything downstream — planning, decision, risk, execution — should model around that schema. The AI calls should be *small and rare* (per the architectural stance), never blocking the observation path.

---

## 25. Data Flow Review

**Current actual flow (one symbol, one process):**
```
Binance WS → BinanceAdapter.normalize → ObservationEvent → ObservationBus
   → [Broken] Consumer / (never wired) ContextBuilderImpl.handle
        → InMemoryWindowManager.add/snapshot → ContextSnapshot
        → FeatureEngineImpl.run → 5 features → FeatureExecutionResult
        → MarketContext → MarketContextCreatedEvent → InMemoryEventBus.publish
        → (nothing consumes it)
```

**Flow problems:**
1. Broken at the observation→consumer→context-builder join. Nothing connects the WS adapter to the ContextBuilder.
2. Two event types and three bus abstractions make the flow hard to trace.
3. `ObservationEvent.timestamp` is UTC via `datetime.utcfromtimestamp` (deprecated in 3.12+, non-aware). Timezone-naive timestamps are a latent correctness bug for cross-session comparison; should be aware-UTC.
4. No backpressure strategy (unbounded `asyncio.Queue(maxsize=0)`).
5. `AsyncQueuePublisher` drops the *oldest* event when full (silent data loss by design).

**Recommended target flow:** single, typed, one-directional:
```
Adapter → normalized ObservationEvent (aware-UTC) → persist (at-least-once)
   → ContextBuilder → MarketContext → persist → publish (in-process)
   → AI (out-of-band) → Decision Proposal → Risk (veto) → Execution → Ledger
```

---

## 26. Execution Flow Review

- **State: zero execution code.** No order models, no risk service, no exchange write-path, no position tracking, no fill handling.
- **Design assets:** the playbook's venue-agnostic execution core, 4-layer circuit breakers (daily 5% / monthly 15% / max DD 25% / total halt), dynamic position sizing, gas/fee accounting. Excellent — should become ADRs + interfaces before any implementation.
- **Missing interfaces that must be defined before coding:** `OrderRequest`, `OrderStatus`, `Position`, `RiskGate` (veto authority), `ExecutionReport`. The playbook principle "strategies must never import an exchange SDK" needs a port like `IOrderGateway`.
- **Recommendation:** model the execution domain *now* (cheap, high value) but implement it *last* (after observation + context + decision schema are real). **The single most important rule: risk must be a decoupled service with veto power over every order.** Non-negotiable.

---

## 27. Learning System Review

- **Design intent (docs):** Learning updates knowledge/confidence/relationships/experience; never directly modifies production behavior without validation; Reflection evaluates process, not just P&L. Mature, correct framing.
- **The honest research conclusion is right:** Hermes learns from *conversations*; ATI learns from *market outcomes* — noisy, delayed, non-stationary. It is not yet translated into a design.
- **Implementation gaps:**
  - No outcome recording (no trade ledger, no `position_ledger` schema despite the playbook calling for one).
  - No episodic/semantic/reflective memory storage.
  - No feedback path from outcomes to confidence.
  - No safety-gate implementation ("learning never directly rewrites production behavior").
- **Recommendation:** the first learning artifact should be a **trade outcome ledger** (decisions → outcomes → metrics), stored durably, with a report/reflection job — not a memory system. Learning from real P&L requires the execution loop to exist first. Do not build Hermes-style memory until there is something to remember. And the learning loop must be **sandboxed**: it never alters risk parameters without human approval.

---

## 28. Memory System Review

- **Design intent (docs):** the 5-layer knowledge hierarchy (Reality → Observations → Knowledge → Experience → Wisdom) in `Knowledge_Model.md` is beautiful and coherent — and unmapped to any code, schema, or store.
- **Hermes memory research:** bounded memory files, procedural skills, background review loop with write-approval gates, cross-session SQLite recall, self-improvement policy. The framework is worth adopting; the *content model* is not — ATI's memory must be about market knowledge, not chat.
- **The only memory that exists today:** the rolling in-memory window (ephemeral, per-symbol, discarded).
- **Recommendation:** do not build a memory system yet. Build the ledger (§27) and the SQLite persistence layer first. When memory arrives, start with a single durable `knowledge` table (structured market knowledge records with confidence and provenance), not a general-purpose memory.

---

## 29. Risk Assessment

### Verified, present-tense risks
1. **Broken imports (realized).** Repo claims Sprint 4A "Complete"; nothing runs. Confidence in the system exceeds its actual state — the most dangerous posture for a trading project.
2. **Zero persistence.** A crash loses everything; no replay, no ledger, no learning.
3. **No committed baseline.** Zero git history on a complex project is one stray command away from total loss.
4. **Two pipelines.** The ambiguity doubles maintenance and confuses every future change.
5. **Interpreter shadowing.** New developers will hit the hermes-venv trap immediately.

### Design-time risks (must be decided before build)
6. **AI entry point undefined.** The vision and the code disagree on when AI acts. Unresolved, effort oscillates.
7. **Free-tier AI dependency.** A vanishing/throttled endpoint silently degrades the reasoning layer. Needs a degradation policy + a rule that live trading never depends on free providers.
8. **Learning-loop safety.** No written rule that learning can never alter risk parameters without human approval. Must be an ADR and a code gate.
9. **Venue-agnosticism is a claim risk.** The Rust toolkits prove even mature projects ship the claim without the code. ATI must have one adapter per venue behind one port before claiming it.
10. **Unbounded queues / silent data loss** as latent correctness bugs once traffic grows.

### Timeline risk
11. The gap between "deterministic context builder" and "autonomous intelligent trader" is multiple orders of magnitude. Without a roadmap, the project risks infinite prototyping.

---

## 30. Blind Spots

Honest list of what this review could not verify or may be wrong about:

1. **Test quality beyond intent.** I read the tests; I could not run them (import gate). A passing suite may reveal additional design flaws I cannot see statically.
2. **Binance adapter correctness.** `binance_adapter.py` has never successfully imported here (all `.pyc` artifacts are from my own attempts today). I cannot attest to its WS handling, reconnect logic, or payload normalization beyond static reading.
3. **Prior test runs.** Root `.pytest_cache` contains PolyWeather test nodeids from 23/07/2026 — evidence someone ran *research-repo* tests from this root. I cannot determine what was run, why, or with what result, and there is no record of the repo's own suite ever passing.
4. **"Sprint 4A Complete" provenance.** `implementation_summary.md` claims completion; the evidence contradicts it. The labeling is the risk, not the label's source.
5. **Market-knowledge validity.** I assessed engineering soundness, not whether 5-minute-window price features are a good market model. The product thesis is unvalidated by any backtest or paper run — by design (no over-optimized backtests), but it is still unvalidated.
6. **The free-tier gateway.** OmniRoute `localhost:20128/v1` behavior (latency, reliability, rate limits) is unmeasured.
7. **Windows vs deployment target.** All verification is on Windows; the production target (Linux) is unexercised.

---

## 31. Missed Opportunities

1. **The observation layer has no tests** while being the only new-vs-legacy code that adds value — the ingestion path is the untested half of the system.
2. **`implementation_summary.md` is not the single source of truth.** A simple `docs/status.md` (what's wired, what's broken, what's next) would prevent the "Complete" mislabel.
3. **SQLite-first was decided in conversation/notes, not in an ADR** — so `recommended_integrations.md` still says "Redis NOW." A two-line ADR would stop the docs from contradicting each other.
4. **The playbook was never promoted to ADR status** despite being the most actionable artifact.
5. **Research value is trapped in cloned repos.** 1137 Java/TS/Rust files add 67.6 MB and zero executable value; the extracted lessons (in `experiments/`) are worth keeping, the clones are not.
6. **No environment/onboarding fix** for the interpreter shadowing — a 5-minute README fix that blocks every new developer.
7. **No structured-logging contract** — every future observability feature will retrofit it.

---

## 32. Recommended Architecture (target state)

```
Observation Layer (adapter per venue)      [port: ObservationAdapter]
        │  normalized ObservationEvent (aware-UTC)
        ▼
Context Builder (WindowManager → Features → MarketContext)
        │  immutable MarketContext + events
        ▼
┌────────────────────────────────────────────────────────────┐
│  Cognitive Core (async, out-of-band)                       │
│  Reason (hypotheses+confidence) → Plan → Decision Proposal │
│  Decision Proposal = schema of record                      │
└────────────────────────────────────────────────────────────┘
        │ proposal
        ▼
Risk Service (decoupled, VETO authority; circuit breakers,
        │  dynamic sizing, exposure)
        │ approved order
        ▼
Execution Service (venue-agnostic IOrderGateway)
        │ fills / failures
        ▼
Trade Outcome Ledger (durable: decisions → outcomes → metrics)
        │
        ▼
Reflection → Learning (reports first; write-approval gates;
        │  updates knowledge/confidence/experience; NEVER
        ▼  rewrites production behavior directly)
Knowledge Store (semantic/episodic/reflective; SQLite → later)
```

**Key properties:**
- **One directional flow:** Observation → Understanding → Reasoning → Decision → Risk → Execution → Reflection → Learning.
- **AI runs out-of-band** — consumes snapshots/proposals, never blocks the observation path; AI calls are small and rare.
- **Risk is a hard gate with veto power**, implemented deterministically and fully tested.
- **Learning is sandboxed** — recommendations only; human-approval gate for anything that alters risk parameters.
- **Everything observable** — every decision logged with evidence, confidence, uncertainty.
- **SQLite-first persistence**; a repository port in application, sqlite3 impl in infrastructure.
- **The AI entry-point decision:** AI enters at the *decision proposal* stage — it reasons over immutable `MarketContext` and produces proposals; deterministic code (features, risk gates, execution) is the workhorse. This reconciles the vision and the code.

---

## 33. Prioritized Roadmap

### Phase 0 — Stabilize ✅ COMPLETE
1. ✅ Fix the 3 import bugs.
2. ✅ `py -3 -m pytest` green (572 tests passing).
3. ✅ Add observation-layer unit tests (CCXT adapter, gateway, bus).
4. ✅ Delete legacy `market_data` pipeline + dead code + duplicate docs.
5. ✅ Git commits; commit discipline.
6. ✅ `ruff` + `mypy` config; CI-clean.
7. ✅ CI: GitHub Actions test job on push/PR.
8. ✅ Document the interpreter in README.

### Phase 1 — Persistence & Wiring ✅ COMPLETE
9. ✅ SQLite layer: persist observations, contexts, proposals, ledger, memory.
10. ✅ Wire `ObservationBus → ContextBuilder` consumer.
11. ✅ Timezone awareness (aware-UTC) and bounded-queue backpressure.
12. ✅ Observability API: `/v1/drive`, decision/context/memory routes, static dashboard.
13. ✅ Platform supervisor (kill switch + stale-data gate) wired into the live pipeline with `/v1/supervisor` routes.
14. ✅ 16 ADRs governing architecture, integration, and evolution.

### Phase 2 — Decision Schema & Simulation ✅ COMPLETE
14. ✅ **Decision Proposal schema** — domain model + serialization (hypotheses, evidence, confidence, actions, risk context, alternatives).
15. ✅ Execution/risk interfaces (`OrderGateway`, `RiskGate`, `OrderRequest`, `Position`, `ExecutionReport`, `AIReasoner`).
16. ✅ Paper-trading simulator (deterministic, replay-driven) with reflection → episodic memory.

### Phase 3 — Cognitive Core ✅ COMPLETE
17. ✅ Reasoning service — rule-based + PydanticAI + OmniRoute (dev/backtest).
18. ✅ Risk service with circuit breakers and veto authority — deterministic, tested.
19. ✅ Reflection service: proposals vs outcomes → episodic memory.

### Phase 4 — Profitability & Data (CURRENT — see Integration Synthesis)
20. **Measure execution** — extend `ExecutionReport` with fee/venue/maker (prerequisite for all slippage work).
21. **Reduce execution cost** — maker/taker + post-only routing (4-5 bps saved).
22. **Add alternative data** — GDELT+FinBERT (free, Sharpe 4.65-5.87), SEC EDGAR insider (free).
23. **Add microstructure alpha** — L2 delta capture, Integrated OFI, micro-price.
24. **Upgrade risk** — fractional Kelly, HRP, CVaR optimization.
25. **Add ML features** — purged CV, regime detection, LightGBM.
26. **Validate everything** — hftbacktest harness, honest net-of-cost measurement.

**Completed under Phase 4 (as of 2026-08-12):**
- ✅ Kyle's λ normalizer (rolling OLS impact coefficient) — `test_kyle_lambda.py`.
- ✅ Book imbalance (L1-L10 depth-weighted OBI) — `test_book_imbalance.py`.
- ✅ **VPIN toxicity estimator** (`risk/vpin.py`) wired as a risk-gate veto: withdraws when toxicity is at/above severity floor with evidence (`veto_on_toxicity`, `min_toxicity_evidence_buckets=8`).
- ✅ **Square-root impact calibration** (`execution/market_impact.py`): least-squares η fit from ATI's own fills; risk-gate veto when estimated impact exceeds reward (`veto_on_excess_impact`, `max_impact_to_reward_ratio=0.25`, `min_impact_evidence=30`).
- ✅ **Triple-barrier events + meta-labelling** (`validation/triple_barrier.py`): volatility-scaled PT/SL + vertical time barrier; meta-label = P(primary bet succeeds) driving size.
- ✅ **ADWIN drift detection** (`validation/adwin.py`): Bifet & Gavaldà cut criterion, memory-bounded, `drifted` signal for operator alert (never autonomous risk changes).
- ✅ Purged walk-forward CV (label-aware) and HMM regime detection (`purged_cv.py`, `regime_detector.py`).
- ✅ **Reconciliation (P0-012)**: `ReconciliationService` + `ReconciliationStore` port + `SqliteReconciliationRepository` (lossless JSON round-trip) + `/v1/reconcile` routes + `reconciliation_reports` table; venue is source of truth, discrepancies reported never coerced.

### Phase 5 — Learning & Graduation (FUTURE)
27. Trade outcome ledger analytics; learning loop with write-approval gates.
28. Only after extended profitable paper trading: risk-gated live execution on a real venue.

**The detailed, profit-ranked integration roadmap with 26 specific initiatives across 4 tiers is in `docs/INTEGRATION_SYNTHESIS.md`.**

**Cross-cutting:** every phase ends with a green test suite, a commit, and an ADR for any decision made.

---

## 34. Three / Five / Ten-Year Vision

**Year 3 (if reworked per this review):** importable, tested, committed; single pipeline; SQLite persistence; Decision Proposal schema; observability API; deterministic reasoning + risk-gated paper execution; reflection producing reports. A genuinely functional "observing, understanding, deciding (in simulation)" system.

**Year 5:** risk-gated paper trading with confidence calibration; the learning loop demonstrates that recalibration proposals are *accepted* by a human gate at an improving rate; the venue-agnostic port carries at least 2 venues with no strategy code change; the free-tier constraint is a non-issue because live trading runs deterministic risk + minimal, paid, high-reliability AI calls.

**Year 10:** the system has a decade-long outcome ledger; learning is a continuous, human-gated improvement process; the architecture's value is the accumulated knowledge store + the discipline of gated evolution, not any single model or venue. The likely failure mode to avoid: building more layers without ever graduating past paper.

**If current trajectory continues (no roadmap, no stabilization):** within a year the repository holds several more sprints of disconnected prototypes, no committed baseline, learning/execution still prose, and the codebase becomes a museum of half-built clean architecture.

---

## 35. Better Open-Source Alternatives (honest)

The project's instinct to research rather than reinvent is correct. For the *components* it is building:

1. **Feature/context pipeline:** no OSS package matches "clean-architecture context builder" — building this is justified. Do **not** adopt backtrader/vectorbt/zipline as core dependencies; they would fight the architecture.
2. **Event bus:** for V1 in-process, `ObservationBus` is fine. When multi-process is needed, Redis Pub/Sub (or NATS/JetStream for durability + replay). Don't build your own.
3. **LLM orchestration:** avoid LangChain — it adds abstractions that conflict with Clean Architecture. A thin OpenAI-compatible client + the Decision Proposal schema is simpler. (This is a *disagreement* with any hint toward LangChain in `recommended_integrations.md` — flagged per the repo's constructive-disagreement rule.)
4. **Time-series storage:** ClickHouse later; SQLite for V1; DuckDB as a middle option for local backtest analytics.
5. **Execution:** "take ideas not code" from the Rust toolkits is correct; do not vendor their code.
6. **If starting from scratch today** with only free resources, the verdict would still be the same architecture — the design is sound; only the execution is incomplete.

**Bottom line:** the build-it-yourself decisions are defensible in every layer *because* proper ports exist. The risk is not reinvention — it is scope and sequence.

---

## 36. If This Repository Disappeared Today — What Would I Build Differently?

The useful version of the "disappeared" question, answered as the owning principal:

1. **Same clean-architecture layering, same cognitive canon, same vision docs** — these are the repository's durable value. Rebuild them nearly verbatim.
2. **Same feature pipeline design** (immutable snapshots, deterministic timestamps, failure-isolated features).
3. **Different execution sequence:** I would make the first milestone a *persisted, replayable, observable pipeline* — a commit on day one, SQLite schema by milestone two, an observability endpoint by milestone three — before adding a second domain layer.
4. **One ingestion pipeline from the start**, with a venue port, never two.
5. **An ADR for the AI entry point and the free-tier constraint** before writing any application code.
6. **Research kept as extracted lessons** (`experiments/`), never as vendored clones.
7. **What would be rebuilt identically:** the principles (AGENTS.md/CLAUDE.md), the playbook, the test *intent*, the domain model discipline.

**What should never be rebuilt:** the legacy `market_data` pipeline, the dead `SourceRegistry`/`sources.yaml`, the empty docs, the vendored research.

---

## 37. Final Verdict

**The architecture is worth saving; the repository in its current state is not runnable and must not be extended.**

This is a **conditional pass on design, a fail on delivery.** The clean layering, immutable domain model, deterministic design, and honest research are the foundations of a genuinely good system. But a trading system that cannot import, has never committed, holds two competing pipelines, and persists nothing is not "Sprint 4A Complete" — it is a prototype that needs its foundation stabilized before one more feature is added.

The review's confidence in the *product thesis* (AI as trader, deterministic workhorse, outcome-driven learning) is high; the confidence in the *current code's ability to serve it* is low until Phase 0 completes.

**The one thing to do first:** fix the import gate, make the test suite green, and make the first commit. Everything else follows from that.

---

## 38. Final Rule

A short rule that should govern every future decision in this repository:

> **"Runnable before ambitious; stored before smart; gated before live."**

Three clauses, each a non-negotiable order:
1. **Runnable before ambitious** — no sprint is "Complete" unless the suite runs and the commit is green.
2. **Stored before smart** — no learning, memory, or AI reasoning until observations and decisions are durably persisted and replayable.
3. **Gated before live** — no live trading, and no learning that alters risk parameters, without a deterministic risk gate and a human approval step.

If the repository follows these three rules, it will still be a better system in ten years. If it does not, no architecture review can save it.

---

# Appendix A — Verified Evidence

- `py -3 -c "import backend.main"` → `ImportError: cannot import name 'DuplicateFeatureError' from 'backend.domain.context.errors'` at `feature_registry.py:14`.
- `py -3 -c "import backend.infrastructure.observation.observation_bus"` → `ModuleNotFoundError: No module named 'backend.infrastructure.domain'` at `observation_bus.py:12`.
- `py -3 -c "import backend.application.observation.consumer"` → `ModuleNotFoundError: No module named 'backend.application.infrastructure'` at `consumer.py:12`.
- `py -3 -c "import backend.domain.context.feature_registry"` → same `DuplicateFeatureError` ImportError.
- `py -3 -m pytest --collect-only` → fails in `conftest.py` with the same `DuplicateFeatureError` ImportError.
- `backend/services/market_data_worker/worker.py` imports successfully (absolute imports; the only legacy-pipeline module that does).
- `git status` → branch `master`, "No commits yet", all files untracked. No reflog, no remotes.
- All `backend/**/__pycache__/*.pyc` are `cpython-314` and timestamped today (05/08/2026 01:16–01:19) — artifacts of my own failing-import verification runs, not evidence of past green runs.
- Root `.pytest_cache` (created 23/07/2026 04:08) contains PolyWeather-main test nodeids in `lastfailed` — prior test runs against research repos, no record of the repo's own suite passing.
- 6 `System_Architecture*.md` files share SHA-256 `183B04927747F0AB5166305C8221B5CFB6AD0765E62DCBB8012824494C2A72BD`.
- `docs/Architecture.md`, `Principles.md`, `Roadmap.md` are 0 bytes.
- `CLAUDE.md` and `AGENTS.md` are both 5512 bytes (identical content).
- `research/repositories/` = 345 `.py` + 1137 Java/TS/Rust files across 7 cloned repos; 67.6 MB of the repo's 113.9 MB total. Research dirs contain no pytest config.
- Repo total 113.9 MB; `research/` = 67.6 MB.
- No `Dockerfile`, compose, CI files (outside research repos), run scripts, or `Makefile`/justfile anywhere in the repo tree.
- `backend/application/source_registry.py` defines `SourceRegistry`; grep confirms nothing calls `load_from_dict` or reads `sources.yaml`.
- `ObservationBus` is referenced only by `binance_adapter.py`, `consumer.py` (both broken imports), and its own definition — never wired end-to-end.
- Working interpreters: `py -3` = CPython 3.14.3 with pytest 9.1.1 (target env, matching `.pyc`); `python` on PATH = hermes-agent venv 3.11.15 without pytest.
- Venue-agnosticism gap in research: `Prediction-Markets-Trading-Bot-Toolkits-main` hardcodes `Polymarket` in `strategy.rs:106`, `risk_guard.rs:141`, `position_monitor.rs:121`; `cross_market_arb.rs:4` confirms Kalshi is planned; `VenueId` enum exists but only Polymarket is used.
- `polybot-main` guards live mode behind `X-Polybot-Live-Ack` header (`LiveTradingGuardFilter.java`); default `hft.mode: PAPER`.

---

*End of review. Prepared without modifying any repository file except this one. Next recommended action: Phase 0, item 1 — fix the import gate, run the suite, make the first commit.*

---

## Appendix B — Phase Progress Note (2026-08-05, post-review)

This review is preserved as the historical snapshot it is. Since it was written, the recommended roadmap has been executed in order; the current standing truth is the code and tests, not this document.

**Phase 0 (Stabilize) — Complete:** import gate fixed, test suite green, git baseline committed, `ruff` + `mypy` added and CI-clean, legacy `market_data` pipeline removed, duplicate/empty docs deleted.

**Phase 1 (Persistence & Wiring) — Complete:** SQLite layer (ADR 0004) persists `ObservationEvent` and `MarketContext`; `ObservationBus → ContextBuilder` wired; observability API (`/v1/context/latest`, `/v1/context/history`, `/v1/events/recent`); ADRs 0004, 0005, 0006 published.

**Phase 2 (Decision Schema & Simulation) — Complete:** Decision Proposal schema (Document 05), execution/risk domain contracts and ports (ADR 0007), `CircuitBreakerRiskGate` with playbook circuit breakers and veto authority, deterministic `PaperFillEngine` + `PaperTradingSimulator` (ADR 0008), durable proposal and ledger repositories, full test suite.

**Phase 3 (Cognitive Core, deterministic first) — In progress:** `AIReasoner` port + deterministic `RuleBasedSolver` (ADR 0009), `DecisionPipelineService` wiring `MarketContext → proposal → risk gate → simulator → ledger`, decision/ledger/simulator API routes. LLM-backed reasoning and the reflection job remain.

Verified as of this note: `py -3 -m pytest` (181 passing), `mypy backend` clean (81 files), `ruff check`/`format` clean. The "AI is the trader; deterministic software is the workhorse; rules are safety constraints" invariant is now embodied end-to-end in simulation.

---

## Appendix C — Verified Evidence (2026-08-12)

All evidence collected by executing the real interpreter against the working tree (`py -3`, CPython 3.14.3). Commands and outputs are reproducible.

**Test / type gates (real runs).**
- `py -3 -m pytest -q` → `572 passed, 5 warnings in ~33s`.
- `py -3 -m mypy backend` → `Success: no issues found in 135 source files`.
- `tests/integrity/test_dependency_manifest.py` passes: every third-party import is declared in a `requirements*.txt` profile; core profile excludes the heavy optionals (`torch, transformers, cvxpy, riskfolio, pandas, edgar, ccxt, pydantic_ai, openai`); a subprocess smoke test confirms `backend.main` and the bootstrap builders import with those packages blocked.

**Composition root (`backend/main.py`).** Real bytes printed via the interpreter; exports `app`, `lifespan`, `health_check`, `context_config_status` and the six routers. `lifespan` sets exactly these `app.state` attributes (verified by AST and by runtime `dir`): `observation_repository, context_repository, proposal_repository, ledger_repository, memory_store, reconciliation_store, reflection, simulator, fill_engine, supervisor, decision_pipeline, context_builder, database`. **No `event_bus`, no `observation_bus`, no `ContextPipelineService`, no CCXT/sentiment/EDGAR service, no AI reasoner.**

**Live behavior (TestClient against `backend.main.app`).**
- `POST /v1/drive` ×3 → proposals persisted (`/v1/proposals/recent?symbol=btcusdt` returns real `prop-btcusdt-...` records); position opened in `/v1/ledger/open` and `/v1/simulator` (`side=buy`, equity tracked); `/v1/memory/count` = 0 (no closed trades yet); `/v1/supervisor/status` = `healthy` with `stale_symbols=[]`.
- `/v1/events/recent?symbol=btcusdt` → `{"events": []}` and `/v1/context/latest?symbol=btcusdt` → `404 "No context found"` **after** those drives (evidence for G1).
- `/v1/reconcile/reports` → 200 `{"reports":[],"count":0}`; `POST /v1/reconcile` persists reports (reconciliation store is wired).
- First-call feature engine logs `Feature trend/momentum/volatility failed during computation` for single-price snapshots (evidence for G8).

**Route surface (OpenAPI dump).** 21 paths, 23 endpoints: `/health`, `/context/config`, `/v1/drive` (POST), `/v1/context/{latest,history}`, `/v1/events/recent`, `/v1/proposals/{recent,{proposal_id}}`, `/v1/ledger/{recent,open,{trade_id}}`, `/v1/simulator`, `/v1/memory/{count,recall}`, `/v1/reflection/reflect` (POST), `/v1/reconcile` (POST), `/v1/reconcile/{reports,count}`, `/v1/supervisor/{status,kill,release}`.

**Import-graph audit (AST over real bytes, 136 modules).** Runtime dependency-direction violations: `backend.domain.context.features.regime -> backend.application.regime` (evidence for G6). `sentiment`/`insider` features import application services only under `TYPE_CHECKING` plus module-level `set_service()` singletons. Modules with no production importer: `application/observation` (+`consumer.py`), `application/validation/*`, `application/backtest/*`, `application/portfolio/portfolio_risk.py`, `infrastructure/observation/binance_adapter.py` (evidence for G7; each has tests).
  - **UPDATED (2026-08-13):** the regime runtime violation is resolved — `regime_detector.py` now lives in `backend/domain/context/` and `backend/application/regime/` is deleted (see G6 PARTIAL).

**Dead code.** `domain/context/features/regime.py` contains an unreachable duplicate `return ContextFeature(...)` block after the first return (verified in source bytes).
  - **RESOLVED (2026-08-13):** the duplicate block was removed (see G7 PARTIAL).

**Repo state.** `git status` shows 47 modified files (+3316/−416) and untracked ATI_* governance files, `.docx`/`.pdf` blueprints, and stray root `.txt` files (`full2.txt`, `full_suite*.txt`, `stage0.txt`, `t1.txt`, `full_out.txt`) (evidence for G9). Recent commits are a defect-fix series (C3/C4/C5/C14, C7/C8/C9/H5, C13, C6/C12/C15 — Kelly formula, API auth, pickle RCE, SQLite thread safety).
