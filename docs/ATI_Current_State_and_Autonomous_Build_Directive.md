# ATI Current State and Autonomous Build Directive

## Page 1

ATI — CURRENT STATE, GAP ANALYSIS
& AUTONOMOUS BUILD DIRECTIVE
Principal Engineering Audit + Repository-Specific Agent Operating System
Prepared from the uploaded Trading-Intelligence repository snapshot.
This document is an engineering truth document, not a profitability guarantee.
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 1

---

## Page 2

1. EXECUTIVE VERDICT
ATI is a substantial autonomous-trading research and paper-trading foundation. It has moved far beyond a
prototype, but it has not yet demonstrated a validated trading edge and it is not ready for live autonomous
capital.
The central engineering problem has changed. Early in the project, the problem was building the architecture.
The repository now contains enough architecture. The dominant problem is integration debt: components exist
but several are not yet correctly wired, causally correct, economically measured, replay-safe, or validated.
The correct next phase is therefore INTEGRATION + TRUTH, not FEATURE EXPANSION.
The single most important conclusion
Do not make ATI more sophisticated until the existing sophistication is truthful.
 Make every data source timestamp-correct.
 Make every feature actually wired.
 Make every research split causally correct.
 Make every PnL calculation correct.
 Make execution costs real.
 Make replay deterministic.
 Make live state reconcilable.
 Then research which components actually improve net expectancy.
2. WHAT I FOUND IN THE REPOSITORY
2.1 Architecture
Clean Architecture / hexagonal ports-and-adapters is present and well established. Domain, application,
infrastructure and presentation responsibilities are separated. The Constitution is unusually explicit and
provides a strong governance layer for AI-assisted development.
2.2 Current cognitive pipeline
Observation
↓
Persistence
↓
Context
↓
Reasoning
↓
Decision Proposal
↓
Risk Gate
↓
Paper Simulator
↓
Ledger
↓
Reflection
↓
Episodic Memory
This is real architecture. The long-term intelligence loop is not yet fully realized, but the skeleton is legitimate.
2.3 Current reasoning
 RuleBasedSolver — deterministic baseline.
 AiOmniRouteReasoner — LLM reasoning adapter.
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 2

---

## Page 3

 PydanticAIReasoner — structured LLM adapter.
 DecisionProposal — central structured contract.
The normal FastAPI composition root currently uses RuleBasedSolver, while separate bootstrap functions can
construct the AI-backed pipelines. This is a safe default and should not be changed merely for novelty.
2.4 Current safety
 CircuitBreakerRiskGate exists.
 Supervisor exists.
 Kill switch exists.
 Data freshness gate exists.
 Protective brackets exist.
 Risk sizing has fractional Kelly support.
 Risk gate has veto authority.
These are meaningful capabilities. They should now be hardened and tested rather than replaced.
3. CURRENT DATABASE EVIDENCE
The supplied SQLite database contains almost no actual trading experience:
 0 observation events
 0 market contexts
 3 decision proposals
 0 trade ledger records
 0 memory episodes
This matters enormously. The architecture has memory, but the supplied system does not yet possess a
meaningful historical outcome corpus. Therefore no learned performance claim can currently be made from
the local ledger.
4. THE BIGGEST CURRENT GAPS
P0 — Dependency manifest incomplete: The code imports packages not represented in the core requirements
manifest. Fresh installation reproducibility is therefore not guaranteed.
P0 — Regime input is wrong: RegimeFeature currently feeds timestamp-as-price. Regime output cannot be
trusted.
P0 — Feature configuration is unsafe: Unlisted features default to enabled, allowing new experimental
features to silently enter context.
P0 — Microstructure state is not centrally wired: OFI and micro-price update functions exist but are not
connected to one canonical observation enrichment path.
P0 — OFI is not yet truly multi-level: Price-to-level mapping is placeholder and update semantics need
old/new size handling.
P0 — Tick recorder format is inconsistent: Object arrays plus pickle-disabled reads is not a valid secure
structured-data design.
P0 — Purged CV is incorrect: The current splitter is not properly label-aware and can create temporal
leakage.
P0 — Replay uses wall clock in risk snapshot: Daily/monthly resets use current time rather than replay time.
P0 — Short unrealized PnL is wrong: The simulator uses the long formula for shorts.
P0 — Fees are not fully included in paper economics: ExecutionReport has fee fields, but paper PnL still
treats fees as zero.
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 3

---

## Page 4

P0 — Arrival price is not captured reliably: CCXT execution report cannot currently provide a trustworthy
pre-submission arrival price.
P0 — Live reconciliation is incomplete: Order and position state need explicit reconciliation before live
authority.
P0 — API protection is incomplete: Some sensitive observability/control surfaces are not uniformly protected.
5. FEATURE-BY-FEATURE TRUTH CHECK
Trend — Implemented: Baseline context feature; requires historical evaluation.
Momentum — Implemented: Baseline context feature; requires historical evaluation.
Volatility — Implemented: Useful risk/context input; needs timestamp and cost-aware evaluation.
Volume — Implemented: Context only until incremental predictive value is measured.
Liquidity — Implemented: Currently partially proxy-based; should be separated into true book liquidity vs
trade-size proxy.
Sentiment — Prototype: GDELT+FinBERT exists, but historical timestamped storage and replay integration
are missing.
SEC/13F proxy — Prototype: Useful research hypothesis only; current mapping is a proxy from public-
company activity to crypto.
Order Flow — Prototype/Incorrectly wired: Needs event wiring, level mapping and delta semantics.
Micro-price — Prototype/Incorrectly wired: Formula exists, but update path is not centrally connected.
Regime — Incorrect until fixed: Current feature uses timestamp as price.
6. EXECUTION TRUTH
The repository has both a paper gateway and a CCXT gateway. That is excellent architecturally. The problem is
economic realism.
 Paper fills use zero fees.
 Paper post-only orders do not model resting/future fills.
 Queue position is in a separate validation harness rather than the main paper execution path.
 Funding is absent.
 Latency is absent.
 Arrival price is not reliably captured live.
 Venue reconciliation is incomplete.
 Unknown order state handling is incomplete.
The system should not move to live capital until these are resolved or explicitly bounded by a small, operator-
controlled canary.
7. RESEARCH TRUTH
The project has correctly started adding validation infrastructure. But validation infrastructure itself must be
validated.
Purged CV
The current implementation is not a sufficient financial purged-CV implementation because it lacks explicit
label intervals and can retain future observations relative to a test fold. Replace it with label-aware interval
purging.
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 4

---

## Page 5

Tick data
The recorder must use a safe structured representation. The objective is not simply disabling pickle; it is
creating a durable, replayable L2 dataset.
Backtest
A backtest should be treated as a simulator experiment, not a prediction certificate. It must include realistic
costs and preserve information availability.
8. THE REAL NEXT PHASE
PHASE P0
Correctness + Safety
↓
PHASE P1
Research Truth
↓
PHASE P2
Execution Truth
↓
PHASE P3
Decision Intelligence
↓
PHASE P4
Controlled Autonomy
The temptation will be to jump directly into P3 because the architecture is exciting. Do not. P0 and P1 are
where the system earns the right to become intelligent.
9. AGENT OPERATING SYSTEM
The accompanying control pack contains a repository-specific operating system for the coding agent. It
converts the architecture into a dynamic workflow rather than a static prompt.
The agent must continuously do five things
1. Inspect the repository.
2. Determine the highest-priority incomplete task.
3. Implement the smallest safe change.
4. Verify the change with tests and static checks.
5. Update the task/state system so the next session knows what to do.
Task selection algorithm
IF safety defect:
fix it
ELSE IF correctness defect:
fix it
ELSE IF component is unwired:
wire it
ELSE IF component is untested:
test it
ELSE IF measurement is missing:
measure it
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 5

---

## Page 6

ELSE IF validation is missing:
validate it
ELSE:
select highest-value research task
This is the behavior you wanted when you said you wanted something that automatically tells the agent what
to do. The queue and operating system turn that requirement into a repeatable engineering control loop.
10. CURRENT FIRST TASK
The first task should be dependency reproducibility.
P0-001 — Complete dependency manifest
 Audit all third-party imports.
 Separate core from optional heavy integrations.
 Make a fresh installation reproducible.
 Add import smoke tests.
 Do not make every research dependency mandatory for the core.
11. SECOND WAVE
6. Fix regime price source.
7. Make feature configuration explicit.
8. Wire micro-price/OFI/tick recorder through one observation enrichment path.
9. Correct OFI semantics.
10. Replace tick storage.
11. Correct purged CV.
12. Make simulation clock deterministic.
13. Fix short PnL.
14. Integrate fees.
15. Capture arrival price.
16. Implement reconciliation.
17. Protect sensitive APIs.
12. WHAT THE AGENT MUST NOT BUILD YET
 Reinforcement learning.
 DeepLOB/CNN-LSTM.
 FPGA/kernel-bypass infrastructure.
 Complex smart order routing.
 NATS before the single-process bus is proven insufficient.
 ClickHouse before SQLite limits are demonstrated.
 Large vector-memory systems before structured trade memory is useful.
 More exchanges before the first venue lifecycle is correct.
 More LLM providers before model evaluation is defined.
 More indicators before feature attribution exists.
13. WHAT SUCCESS WILL LOOK LIKE
The system will not be considered successful merely because it produces trades. The first major success
milestone is when the entire paper pipeline becomes a trustworthy scientific instrument.
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 6

---

## Page 7

 A historical event stream can be replayed.
 The same replay gives the same result.
 Every feature is timestamp-correct.
 Every decision is reproducible.
 Every order has a cost.
 Every trade has net PnL.
 Every loss can be classified.
 Every strategy can be compared.
 Every feature can be ablated.
 Every experiment is versioned.
 Every model can be promoted or rejected.
 Every production change is reversible.
14. LONG-TERM AUTONOMY
Only after the scientific foundation is trustworthy should the system be allowed to become increasingly
autonomous.
OBSERVE
→ UNDERSTAND
→ REASON
→ DECIDE
→ RISK
→ EXECUTE
→ REFLECT
→ RESEARCH
→ VALIDATE
→ PROMOTE
The AI should eventually be able to run the research loop continuously. It should not be able to grant itself
production authority.
15. FINAL PRINCIPAL JUDGMENT
The project is in a much better position than a normal AI trading bot. The architecture is serious, the repository
has real boundaries, and the recent agent work has added many useful components.
But the next leap will not come from adding another model. It will come from making the current system
honest.
The most dangerous state for ATI would be a system that looks extraordinarily intelligent while its backtest is
leaking, its regime input is wrong, its fees are zero, its microstructure features are cold, its validation is flawed,
and its outcome ledger is empty.
The agent operating system is therefore designed around one principle:
MAKE IT TRUE → MEASURE IT → VALIDATE IT → THEN MAKE IT SMARTER.
That is the path from the current repository to the autonomous trader envisioned by the project.
APPENDIX — IMMEDIATE TASK QUEUE
18. P0-001 Complete dependency manifest
19. P0-002 Fix regime detector price source
20. P0-003 Make feature configuration explicit
21. P0-004 Create canonical observation enrichment path
22. P0-005 Correct OFI semantics
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 7

---

## Page 8

23. P0-006 Replace unsafe tick recorder format
24. P0-007 Replace purged CV with label-aware validation
25. P0-008 Make simulator risk snapshots replay-time deterministic
26. P0-009 Fix signed PnL
27. P0-010 Integrate fees into net PnL
28. P0-011 Capture execution arrival state
29. P0-012 Build reconciliation contract
30. P0-013 Protect sensitive API routes
31. P0-014 Validate live gateway in sandbox only
ATI — CURRENT STATE & AUTONOMOUS BUILD DIRECTIVE • 8
