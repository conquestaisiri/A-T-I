# ATI — CURRENT SYSTEM STATE & PRINCIPAL GAP AUDIT
## Repository examined
Trading-Intelligence / Autonomous Trading Intelligence (ATI)

## Audit basis
This audit is grounded in the uploaded repository snapshot, including:
- current source tree;
- tests;
- Git history and uncommitted working-tree changes;
- Engineering Constitution;
- AGENTS.md;
- Architecture Review;
- Integration Synthesis;
- ADRs;
- SQLite database state;
- dependency manifests;
- current wiring/composition roots.

This is an engineering audit, not a profitability claim.

---

# 1. EXECUTIVE VERDICT

ATI is no longer a toy repository.

It has a serious architecture, a real domain model, persistence, a decision proposal schema, deterministic paper execution, risk gating, reflection, multiple AI adapters, a CCXT venue adapter, alternative-data prototypes, microstructure prototypes, validation infrastructure, operator APIs, and a substantial test suite.

However, there is a very important distinction:

> **ATI has a substantial trading-system foundation, but it does not yet have a validated autonomous trading edge.**

The current repository is best described as:

**A tested autonomous-trading research and paper-trading platform with a partially implemented cognitive layer and several recently added integrations that are not yet fully wired, validated, or economically proven.**

It is NOT yet:

- a production autonomous trader;
- a validated profitable strategy;
- a reliable multi-strategy portfolio;
- a production-grade live execution system;
- a self-improving production AI;
- proof that any of the researched Sharpe ratios apply to ATI.

The most important next move is therefore NOT "add more AI."

The most important next move is:

> **Turn the current collection of promising components into one coherent, replayable, measurable, causally correct research-to-paper pipeline.**

---

# 2. WHAT IS ACTUALLY BUILT

## 2.1 Architecture

The repository uses Clean Architecture / ports and adapters.

Current layers:

- `backend/domain/`
- `backend/application/`
- `backend/infrastructure/`
- `backend/presentation/`

The architectural intention is strong.

The Constitution establishes:

1. AI is the trader.
2. Deterministic software is the workhorse.
3. Risk is independent and has veto authority.
4. Learning is sandboxed.
5. External providers are replaceable.
6. Everything important is observable.
7. The system must remain runnable.
8. New subsystems require architectural justification.

This is a good foundation.

---

# 3. CURRENT PIPELINE

The intended flow is:

```text
External observation
        ↓
Observation adapter
        ↓
ObservationEvent
        ↓
Durable observation storage
        ↓
Window manager
        ↓
Feature engine
        ↓
MarketContext
        ↓
DecisionReasoner
        ↓
DecisionProposal
        ↓
RiskGate
        ↓
Paper/Execution Gateway
        ↓
Position / Trade Ledger
        ↓
Reflection
        ↓
Episodic Memory
        ↓
Research / Learning
```

The current paper decision path is real.

The live path is not active.

---

# 4. CURRENT COMPONENT INVENTORY

## 4.1 Observation

Existing:

- `ObservationAdapter`
- Binance adapter
- CCXT adapter
- observation event schema
- observation bus
- SQLite observation repository
- bounded observation queue
- data freshness supervisor integration

Important files:

- `backend/domain/observation/`
- `backend/infrastructure/observation/`
- `backend/application/pipeline/context_pipeline_service.py`

The observation layer is substantially more mature than it was at the beginning of the project.

---

## 4.2 Persistence

SQLite currently stores:

- observation events;
- market contexts;
- decision proposals;
- trade ledger;
- episodic memory.

Current database:

`data/trading_intelligence.db`

Snapshot inspection showed:

- `observation_events`: 0 rows
- `market_contexts`: 0 rows
- `decision_proposals`: 3 rows
- `trade_ledger`: 0 rows
- `memory_episodes`: 0 rows

This is an extremely important fact.

The system has a persistence architecture, but the supplied database does NOT yet contain a meaningful history of actual trading outcomes.

Therefore:

> ATI has the memory infrastructure, but it does not yet possess the large body of market experience required for meaningful learned trading intelligence.

---

# 5. CURRENT REASONING SYSTEM

There are three reasoning paths:

## 5.1 RuleBasedSolver

`backend/application/decision/rule_based_solver.py`

This is deterministic.

It reasons from existing context features and creates a `DecisionProposal`.

It now includes a deterministic protective trade plan.

This is useful as:

- a baseline;
- a deterministic fallback;
- a research benchmark;
- a parity/reference implementation.

It should NOT be mistaken for the final intelligence.

---

## 5.2 AiOmniRouteReasoner

`backend/application/decision/omni_route_reasoner.py`

This provides LLM-backed structured reasoning through the OmniRoute gateway.

It can consume bounded episodic memory.

It produces a structured proposal.

It now receives a deterministic fallback protective bracket when required.

This is a development/backtest reasoning path.

It is not the production autonomous trader.

---

## 5.3 PydanticAIReasoner

`backend/application/ai/pydantic_ai_reasoner.py`

This provides another structured AI reasoning adapter.

It is useful because the model/provider is behind an application interface.

This is aligned with the replaceability Constitution.

Again:

> The existence of a reasoning adapter is not evidence that the reasoning has a profitable edge.

---

# 6. CRITICAL WIRING FACT

The application composition root currently uses:

```text
RuleBasedSolver
        ↓
CircuitBreakerRiskGate
        ↓
PaperTradingSimulator
```

in `backend/main.py`.

The AI reasoners exist, but the normal application startup path is not currently using the LLM reasoner as the default production-like decision path.

There are separate bootstrap functions for:

- rule-based;
- OmniRoute;
- PydanticAI.

This is good for isolation.

It also means the system's actual operational behavior is currently much more deterministic than the long-term vision suggests.

That is not a defect by itself.

It is preferable to having an unvalidated LLM directly control the system.

---

# 7. CURRENT DECISION MODEL

The Decision Proposal is one of the strongest pieces of the repository.

It includes:

- hypothesis;
- supporting evidence;
- opposing evidence;
- confidence;
- uncertainty;
- proposed actions;
- risk context;
- alternatives;
- rationale;
- pre-trade plan;
- post-trade plan.

This is the right direction.

The proposal should remain the central contract between intelligence and deterministic enforcement.

---

# 8. CURRENT RISK SYSTEM

The risk gate is one of the most important real components.

`backend/application/risk/circuit_breaker_risk_gate.py`

It currently contains:

- per-trade risk limit;
- per-symbol risk limit;
- portfolio risk limit;
- daily loss limit;
- monthly loss limit;
- drawdown limit;
- emergency total-loss limit;
- max position size;
- max open exposure;
- fractional Kelly cap;
- protective bracket requirement;
- risk-budget fraction rule;
- safety-action exemptions.

This is a meaningful risk layer.

The risk gate has veto authority.

That should remain non-negotiable.

---

# 9. CURRENT SUPERVISOR

A second safety authority now exists:

`backend/application/supervisor/supervisor_service.py`

It handles:

- operator kill switch;
- market-data freshness;
- healthy/degraded/halted states.

This is a good architectural addition.

The hierarchy is now:

```text
Platform Supervisor
        ↓
Risk Governor
        ↓
Execution
```

The supervisor protects the platform.

The risk governor protects capital.

The execution service performs approved actions.

---

# 10. CURRENT PAPER EXECUTION

The paper system contains:

- order contract;
- execution report;
- paper fill engine;
- paper trading simulator;
- position;
- trade record;
- ledger repository.

It supports:

- market orders;
- limit orders;
- maker/taker classification;
- post-only intent;
- time-in-force;
- protective stop/target plan;
- partial close;
- trade ledger;
- deterministic paper fills.

This is a meaningful simulation foundation.

However, it is still a simplified simulator.

---

# 11. MAJOR PAPER SIMULATOR LIMITATIONS

The current paper fill engine does NOT yet represent a realistic live order lifecycle.

Examples:

- post-only orders do not realistically rest and later fill;
- queue position is not modeled in the primary simulator;
- partial fills are not deeply modeled;
- cancellations are not a complete lifecycle;
- fees are currently set to zero in paper fills;
- funding is absent;
- slippage is simplistic;
- market impact is not integrated into the main simulator;
- order latency is not represented;
- exchange rejection semantics are simplified.

The separate validation harness is more sophisticated, but it is not the same thing as the production-equivalent paper execution path.

This distinction must be maintained.

---

# 12. CURRENT EXECUTION MEASUREMENT

`ExecutionReport` now contains:

- fee;
- venue;
- maker/taker;
- arrival price;
- slippage calculation.

This is an important improvement.

However:

> Having the fields does not mean execution economics are actually being measured correctly throughout the system.

The paper engine currently sets:

```text
fee = 0
```

The CCXT gateway can extract fees, but arrival price is currently not reliably captured at submission.

Therefore execution analytics are still incomplete.

---

# 13. CURRENT LIVE VENUE ADAPTER

A CCXT order gateway exists.

This is significant.

The architecture now has:

```text
OrderGateway
        ↓
CcxtOrderGateway
        ↓
CCXT exchange
```

This is the correct shape.

But the existence of a gateway does NOT mean live trading is ready.

Important missing production capabilities include:

- robust account-state synchronization;
- authoritative position reconciliation;
- order polling/streaming lifecycle;
- unknown-order recovery;
- fill aggregation;
- real arrival-price capture;
- fee normalization;
- funding accounting;
- venue-specific precision/minimum handling;
- idempotent submission guarantees;
- restart recovery;
- live credential isolation;
- full integration testing against sandbox/testnet;
- live canary procedure.

Live trading must remain disabled.

---

# 14. CURRENT MARKET FEATURES

The feature registry now includes:

- trend;
- momentum;
- volatility;
- volume;
- liquidity;
- sentiment;
- insider;
- order flow;
- micro-price;
- regime.

This is a substantial expansion.

However, feature presence is NOT equivalent to feature usefulness.

The next job is to establish:

```text
feature
    ↓
correct calculation
    ↓
timestamp correctness
    ↓
historical availability
    ↓
distribution
    ↓
conditional predictive value
    ↓
net-of-cost contribution
```

No feature should be promoted simply because a paper reported a high Sharpe elsewhere.

---

# 15. CRITICAL FEATURE-CONFIGURATION ISSUE

`config/context.yaml` currently explicitly configures only:

- trend;
- momentum;
- volatility;
- volume;
- liquidity.

But `ContextSettings.is_feature_enabled()` defaults unlisted features to ENABLED.

That means newly added features can become active without explicit configuration.

This is dangerous.

The correct policy should be:

> **Every feature must be explicitly declared in configuration. Unlisted features should default to disabled, not enabled.**

This prevents an experimental feature from silently entering the production decision context.

---

# 16. CRITICAL REGIME DETECTOR ISSUE

The current `RegimeFeature` contains:

```text
price = snapshot.end_timestamp.timestamp()
```

as a placeholder.

That means the regime detector is being fed the timestamp as though it were price.

This is not a legitimate market regime calculation.

This must be treated as a P0 correctness defect.

The regime detector must consume an actual price series from the observation snapshot.

Until this is fixed:

> Do not trust regime output for research, sizing, or trading.

---

# 17. CURRENT ORDER-FLOW IMPLEMENTATION

An OFI tracker exists.

It supports:

- best-level OFI;
- integrated OFI;
- L2 delta events;
- rolling windows.

However, the implementation is not yet equivalent to production-grade integrated OFI.

The current `_price_to_level()` returns level 0 for every price.

Therefore:

> Every level is currently being treated as level zero.

That makes the "multi-level integrated OFI" label misleading.

Also, update semantics need to distinguish:

```text
new size
```

from:

```text
change in size
```

A real delta-based OFI calculation must use the actual book transition.

This needs a correctness pass before OFI can be treated as alpha.

---

# 18. CURRENT L2 DELTA CAPTURE

The CCXT adapter now compares order-book snapshots and generates synthetic deltas.

That is useful as a transitional capability.

But it is NOT the same as receiving authoritative exchange-native incremental depth updates.

Important distinction:

```text
Snapshot A
Snapshot B
     ↓
Synthetic difference
```

versus:

```text
Exchange sequence N
Exchange sequence N+1
Exchange sequence N+2
...
```

Native deltas provide:

- sequence;
- exact update semantics;
- no ambiguity between intermediate states;
- better reconstruction.

The system should eventually support exchange-native L2 streams where available.

For research, the synthetic delta recorder may still be useful, but it must be labeled correctly.

---

# 19. CURRENT MICRO-PRICE

A micro-price feature exists.

It calculates:

- micro-price;
- mid;
- spread;
- imbalance;
- best bid;
- best ask;
- sizes.

This is directionally useful.

However, it uses a module-level global state.

The feature is also not currently wired through a formal state-update pipeline.

Search of the repository shows the event update function exists but is not centrally invoked.

Therefore the feature can remain cold/default unless an external caller manually updates it.

The agent must create one explicit event-to-state update path.

---

# 20. CURRENT SENTIMENT SYSTEM

GDELT + FinBERT exists.

The service:

- fetches recent GDELT articles;
- maps articles to symbols;
- runs FinBERT;
- caches sentiment;
- exposes sentiment to a feature.

However:

- the service is not started by the main application;
- the feature uses a global singleton;
- historical backtests cannot simply use the current live cache;
- the service uses wall-clock "now";
- timestamp-aligned historical sentiment is not implemented;
- article publication timestamp semantics need validation;
- no lag/availability model exists.

Therefore:

> The current sentiment feature is an integration prototype, not a valid historical alpha feature.

For backtesting, sentiment must be stored historically with publication/availability timestamps.

---

# 21. CURRENT SEC EDGAR SYSTEM

An EDGAR service exists.

It provides:

- insider transaction parsing;
- 13F parsing;
- cached signals.

But this needs serious conceptual review.

SEC insider/13F data is primarily about US public companies.

The current mapping includes proxy relationships such as:

- MicroStrategy → BTC;
- Coinbase → ETH;
- mining companies → BTC.

This can be a legitimate research hypothesis:

> "Equity proxy activity may contain information about crypto risk appetite."

But it must NOT be presented as:

> "SEC insider trading data for BTC."

The distinction is critical.

It is a proxy feature and must be validated as such.

---

# 22. CURRENT PORTFOLIO RISK

A portfolio risk manager exists using:

- HRP;
- CVaR;
- numpy;
- pandas;
- cvxpy;
- riskfolio.

This is a research component.

It is not yet clearly integrated into the live decision path.

The current per-trade risk gate remains the actual veto authority.

Therefore HRP/CVaR should currently be treated as:

```text
research / allocation proposal
```

not:

```text
production capital authority
```

until the integration contract is defined and tested.

---

# 23. CURRENT VALIDATION SYSTEM

The repository now includes:

- purged CV;
- walk-forward CV;
- combinatorial purged CV;
- backtest harness;
- tick recorder;
- replay infrastructure.

This is exactly the direction required.

However, the current validation implementations need a correctness audit before being trusted.

---

# 24. CRITICAL PURGED-CV ISSUE

The current `PurgedKFold` implementation is not a proper label-aware purged cross-validation implementation.

The implementation currently removes training samples based on the end of the test fold and then retains later samples.

That can create a temporal relationship where training data is later than the test data.

For financial research this is dangerous.

Correct validation should define:

```text
observation interval
label interval
training interval
test interval
embargo interval
```

and remove training observations whose information/label intervals overlap the test interval.

The implementation should be replaced with a genuinely label-aware splitter.

---

# 25. CURRENT TICK RECORDER ISSUE

The tick recorder was changed to:

```text
np.load(..., allow_pickle=False)
```

but it still writes:

```text
np.array(existing_events, dtype=object)
```

This is inconsistent.

Object arrays require pickle serialization in numpy's `.npy/.npz` mechanism.

The system must switch to a structured representation, such as:

- structured numpy dtype;
- Arrow/Parquet;
- JSONL for V1 simplicity;
- SQLite for small-scale capture.

Do NOT "fix" the security issue by disabling pickle while continuing to write object arrays.

The recorder needs a real safe storage design.

---

# 26. CURRENT DATABASE REALITY

The included SQLite database has almost no actual trading history.

This means the learning system cannot currently demonstrate:

- calibration;
- strategy expectancy;
- loss clustering;
- regime-conditional performance;
- execution quality;
- strategy drift;
- model drift;
- portfolio correlation;
- learned position sizing.

The architecture exists.

The evidence does not yet.

---

# 27. CURRENT LEARNING SYSTEM

Reflection exists.

It reads closed trades and writes episodic memory.

This is good.

But:

```text
3 proposals
0 trades
0 memory episodes
```

means there is currently no meaningful learning corpus in the supplied database.

The next learning step should therefore NOT be "build more memory."

The next learning step should be:

> Generate a large, clean, replayable, causally correct outcome dataset.

Only then should learning become sophisticated.

---

# 28. CURRENT AGENT RESEARCH

The repository contains research on:

- execution;
- risk;
- alternative data;
- microstructure;
- ML infrastructure;
- open-source projects.

There are also cloned repositories under `research/repositories/`.

The Constitution explicitly says research clones should not become core dependencies.

The extracted lessons should be retained.

The implementation should depend only on approved interfaces and selected integrations.

---

# 29. CURRENT GIT HISTORY

The development history shows a very fast progression:

- Phase 1 observation/persistence;
- Phase 2 execution/risk/simulation;
- Phase 3 reasoning;
- memory/reflection;
- CCXT;
- alternative data;
- microstructure;
- portfolio risk;
- validation;
- security fixes;
- supervisor;
- protective trade planning.

This is impressive progress.

But the speed creates a new danger:

> **Integration debt is now more dangerous than feature scarcity.**

There are enough components.

The next phase should be consolidation, wiring, correctness, validation and measurement.

---

# 30. THE MOST IMPORTANT ARCHITECTURAL CHANGE NOW

Stop treating the project as:

```text
"Add another feature"
```

Start treating it as:

```text
"Make the existing system truthful."
```

Every feature must answer:

1. Is it wired?
2. Is it correct?
3. Is it timestamp-safe?
4. Is it persisted?
5. Is it replayable?
6. Is it tested?
7. Is it economically useful?
8. Does it survive costs?
9. Does it improve a decision?
10. Can we remove it without damaging the system?

---

# 31. PRIORITY LEVELS

## P0 — Correctness / safety blockers

These must be addressed before serious alpha research.

1. Dependency manifest completeness.
2. Regime feature timestamp-as-price bug.
3. Purged CV correctness.
4. Tick recorder storage correctness.
5. Short unrealized PnL correctness.
6. Deterministic replay clock/reset behavior.
7. Explicit feature configuration.
8. Event-driven wiring for micro-price and OFI.
9. Fee inclusion in paper PnL.
10. Paper/live execution accounting parity.
11. Position/order reconciliation contract.
12. Full API protection for sensitive endpoints.
13. Validate live gateway behavior in sandbox.
14. Remove misleading "production" claims from unvalidated integrations.

---

# 32. P1 — Make the research engine real

1. Canonical historical dataset.
2. Feature snapshots with timestamps.
3. Label definition.
4. Purged/embargo validation.
5. Cost model.
6. Baseline strategies.
7. Feature attribution.
8. Regime-conditional evaluation.
9. Walk-forward evaluation.
10. Robustness matrix.
11. Experiment registry.
12. Research reports.

---

# 33. P2 — Make execution truthful

1. Real fee model.
2. Funding model.
3. Spread model.
4. Slippage model.
5. Latency model.
6. Partial-fill model.
7. Queue model.
8. Order lifecycle.
9. Arrival price.
10. Venue reconciliation.
11. Execution attribution.

---

# 34. P3 — Make intelligence useful

Only after P0-P2:

1. Calibrated prediction.
2. Scenario engine.
3. Strategy selection.
4. Historical analogs.
5. Model ensembles.
6. Meta-labeling.
7. regime-conditioned strategy selection.
8. drift detection.
9. controlled adaptation.

---

# 35. P4 — Controlled autonomy

Only after evidence:

1. Paper autonomy.
2. Long paper campaign.
3. Canary live deployment.
4. Small capital.
5. automated safe-mode.
6. rollback.
7. gradual scaling.
8. production model promotion.

---

# 36. THINGS THE AGENT MUST NOT DO NEXT

Do NOT:

- add another LLM provider just because it is available;
- add reinforcement learning;
- add deep learning because it sounds advanced;
- add another 20 indicators;
- add another exchange before the first path is correct;
- add high-frequency infrastructure before signal quality exists;
- add a vector database merely because "memory" sounds advanced;
- add NATS before the single-process event model is demonstrably insufficient;
- claim profitability from external papers;
- optimize for 5% daily return;
- tune parameters against the final test set;
- enable live trading;
- let the AI edit risk limits;
- let an LLM bypass the risk gate;
- let a research agent deploy its own model;
- use current live sentiment cache inside historical backtests;
- use timestamp-as-price regime outputs;
- call synthetic snapshot differences "native L2 deltas."

---

# 37. WHAT THE SYSTEM SHOULD BECOME

The target loop is:

```text
OBSERVE
  ↓
VALIDATE DATA
  ↓
BUILD MARKET STATE
  ↓
MEASURE REGIME + UNCERTAINTY
  ↓
RETRIEVE RELEVANT HISTORY
  ↓
GENERATE CANDIDATE THESIS
  ↓
GENERATE SCENARIOS
  ↓
ESTIMATE CONDITIONAL EDGE
  ↓
ACCOUNT FOR COST
  ↓
CHECK OPPORTUNITY COST
  ↓
RISK GOVERNOR
  ↓
EXECUTE
  ↓
MANAGE POSITION
  ↓
EXIT
  ↓
ATTRIBUTE OUTCOME
  ↓
STORE EXPERIENCE
  ↓
RESEARCH
  ↓
VALIDATE IMPROVEMENT
  ↓
PROMOTE VERSION
```

The critical difference is:

> Learning happens after evidence, not after emotion.

---

# 38. CURRENT READINESS SCORECARD

These are engineering maturity estimates, NOT probability-of-profit estimates.

| Area | Current estimate |
|---|---:|
| Architecture | 82% |
| Domain contracts | 78% |
| Persistence | 70% |
| Observation | 68% |
| Context engine | 62% |
| Deterministic reasoning | 55% |
| LLM reasoning | 45% |
| Risk controls | 70% |
| Paper execution realism | 42% |
| Live execution readiness | 12% |
| Research validation | 35% |
| Feature integration | 38% |
| Learning | 20% |
| Autonomous adaptation | 8% |
| Proven trading edge | 0% demonstrated |
| Production readiness | 0% |

The 0% for proven edge is intentional.

There is currently insufficient real outcome data to claim otherwise.

---

# 39. PRINCIPAL CONCLUSION

ATI's biggest risk is no longer that it lacks sophistication.

It already has substantial sophistication.

Its biggest risk is that the repository could become a beautiful architecture containing many partially validated ideas.

The next phase should therefore be called:

> **INTEGRATION + TRUTH PHASE**

The objective is to make every claim match reality.

A feature is not "done" because the class exists.

A strategy is not "done" because a backtest runs.

An AI is not "done" because it generates a proposal.

Learning is not "done" because memory can store a record.

Execution is not "done" because an exchange adapter exists.

The system is progressing correctly when:

```text
CODE
=
DOCUMENTATION
=
TESTS
=
REPLAY
=
MEASUREMENT
=
ACTUAL BEHAVIOUR
```

That is the standard the autonomous agent must now enforce.
