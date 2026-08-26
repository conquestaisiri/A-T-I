# Autonomous Trader Technical Implementation Specification

## Page 1

THE AUTONOMOUS TRADER
TECHNICAL IMPLEMENTATION SPECIFICATION
Construction Manual for a Research-First, Risk-Governed, Autonomous AI Trading
System
This document converts the project's conceptual vision into implementation requirements, interfaces,
schemas, controls, testing gates, and build order.
CRITICAL: This specification does not guarantee profitability. The system must earn deployment authority
through testing and evidence.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 1

---

## Page 2

DOCUMENT CONTROL
Document: Autonomous Trader — Technical Implementation Specification
Purpose: Construction-grade specification for the autonomous trading research and execution platform
Primary audience: AI coding/building agent, software engineers, quantitative researchers, system architects,
reviewers
Relationship to prior documents: 10K Deep Dive = WHY; 80K Master Blueprint = WHAT; this document = HOW
Primary rule: Do not skip validation gates or grant live capital authority to unvalidated components
Deployment philosophy: Research first → simulation → paper → controlled live → measured scaling → bounded
autonomy
Default autonomy: No unrestricted self-modification; production changes require explicit validation gates
HOW THE BUILDING AGENT MUST USE THIS DOCUMENT
 Treat MUST statements as mandatory requirements.
 Treat SHOULD statements as recommended requirements unless a documented engineering reason exists to
change them.
 Treat MAY statements as optional extensions.
 Do not invent missing behavior where this document defines a safety boundary; stop and record the
ambiguity for review.
 Do not connect live capital before all production-readiness gates pass.
 Do not allow an LLM, research agent, strategy model, or reinforcement-learning component to bypass the
independent risk engine.
 Do not optimize the system for a fixed daily return target such as 5%. Daily return is an observation, not the
governing objective.
 Build deterministic interfaces first. Add adaptive AI behavior only behind those interfaces.
 Every important decision must be observable, timestamped, versioned, and reproducible.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 2

---

## Page 3

MASTER BUILD MAP
PART 0 — NON-NEGOTIABLE REQUIREMENTS
0.1 System objective
0.2 Definitions
0.3 Requirement language
0.4 Safety invariants
0.5 Scope boundaries
0.6 Build order
0.7 Definition of done
PART 1 — SYSTEM ARCHITECTURE
1.1 Architecture overview
1.2 Service boundaries
1.3 Data flow
1.4 Event flow
1.5 Control plane vs data plane
1.6 Research vs production isolation
1.7 State management
PART 2 — DATA PLATFORM
2.1 Data source registry
2.2 Market data
2.3 Order book
2.4 Trades
2.5 Derivatives
2.6 Funding and open interest
2.7 Liquidations
2.8 Macro and event data
2.9 News
2.10 On-chain data
2.11 Data normalization
2.12 Timestamps
2.13 Quality controls
2.14 Historical storage
2.15 Real-time storage
2.16 Replay
PART 3 — MARKET STATE ENGINE
3.1 State object
3.2 Feature registry
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 3

---

## Page 4

3.3 Multi-timeframe state
3.4 Volatility
3.5 Trend
3.6 Structure
3.7 Liquidity
3.8 Participation
3.9 Positioning
3.10 Cross-asset context
3.11 Event risk
3.12 Regime detection
3.13 Uncertainty
PART 4 — TRADING KNOWLEDGE MODEL
4.1 Human trader decomposition
4.2 Thesis model
4.3 Setup model
4.4 Invalidation
4.5 No-trade conditions
4.6 Historical analogs
4.7 Trade memory
4.8 Strategy cards
PART 5 — RESEARCH ENGINE
5.1 Experiment registry
5.2 Hypothesis lifecycle
5.3 Feature research
5.4 Backtesting
5.5 Leakage prevention
5.6 Multiple testing
5.7 Walk-forward
5.8 Robustness
5.9 Monte Carlo
5.10 Research scoring
5.11 Promotion gates
PART 6 — STRATEGY ENGINE
6.1 Strategy interface
6.2 Signal generation
6.3 Strategy eligibility
6.4 Ensemble
6.5 Strategy health
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 4

---

## Page 5

6.6 Strategy suspension
6.7 Strategy versioning
PART 7 — DECISION ENGINE
7.1 Candidate trade
7.2 Scenario engine
7.3 Probability
7.4 Expected value
7.5 Opportunity cost
7.6 Confidence
7.7 Abstention
7.8 Decision record
PART 8 — PORTFOLIO AND RISK
8.1 Risk constitution
8.2 Account state
8.3 Position sizing
8.4 Portfolio exposure
8.5 Correlation
8.6 Leverage
8.7 Drawdown
8.8 Liquidity risk
8.9 Tail risk
8.10 Kill switches
8.11 Safe mode
8.12 Risk approvals
PART 9 — EXECUTION ENGINE
9.1 Order intent
9.2 Order manager
9.3 Execution policies
9.4 Slippage
9.5 Fees
9.6 Partial fills
9.7 Cancellation
9.8 Reconciliation
9.9 Execution attribution
PART 10 — POSITION MANAGEMENT
10.1 Live thesis
10.2 Position state
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 5

---

## Page 6

10.3 Add/reduce rules
10.4 Exit engine
10.5 Emergency exit
10.6 Position journal
PART 11 — LEARNING AND MEMORY
11.1 Trade memory
11.2 Outcome classification
11.3 Loss diagnosis
11.4 Model drift
11.5 Regime drift
11.6 Strategy drift
11.7 Controlled adaptation
11.8 Model registry
PART 12 — LLM / AI ORCHESTRATION
12.1 LLM role
12.2 Structured outputs
12.3 Retrieval
12.4 Tool permissions
12.5 Prompt/version governance
12.6 Hallucination controls
12.7 Human review hooks
PART 13 — MONITORING AND OBSERVABILITY
13.1 Logs
13.2 Metrics
13.3 Traces
13.4 Alerts
13.5 Dashboards
13.6 Audit trail
13.7 Incident records
PART 14 — SECURITY AND OPERATIONAL SAFETY
14.1 Secrets
14.2 Permissions
14.3 Exchange access
14.4 Network controls
14.5 Data integrity
14.6 Backup
14.7 Disaster recovery
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 6

---

## Page 7

14.8 Failure drills
PART 15 — TESTING
15.1 Unit tests
15.2 Integration tests
15.3 Replay tests
15.4 Backtest tests
15.5 Risk tests
15.6 Execution tests
15.7 Chaos tests
15.8 Regression tests
PART 16 — DEPLOYMENT
16.1 Environments
16.2 Configuration
16.3 CI/CD
16.4 Versioning
16.5 Rollback
16.6 Paper trading
16.7 Live canary
16.8 Scaling
PART 17 — AUTONOMOUS OPERATING LOOP
17.1 Observe
17.2 Validate
17.3 Interpret
17.4 Generate
17.5 Evaluate
17.6 Risk approve
17.7 Execute
17.8 Manage
17.9 Diagnose
17.10 Research
PART 18 — ACCEPTANCE CRITERIA
18.1 System-level gates
18.2 Strategy gates
18.3 Model gates
18.4 Risk gates
18.5 Execution gates
18.6 Autonomy gates
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 7

---

## Page 8

PART 19 — BUILD ROADMAP
19.1 Milestone sequence
19.2 Deliverables
19.3 Agent instructions
19.4 Review checkpoints
APPENDICES
A. Canonical schemas
B. State machines
C. Error taxonomy
D. Reason codes
E. Configuration examples
F. Test matrix
G. Research checklist
H. Production checklist
I. First build sprint
J. Final implementation prompt
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 8

---

## Page 9

PART 0 — NON-NEGOTIABLE REQUIREMENTS
0.1 System Objective
Build a modular autonomous trading platform that can observe market data, construct a time-consistent market
state, identify validated strategy opportunities, generate conditional trade theses, estimate scenario distributions
and expected value, pass proposals through an independent risk governor, execute approved orders, manage
open positions, record every decision, diagnose outcomes, and feed evidence into a separate research process.
The system is not defined by whether it produces a buy or sell signal. It is defined by whether it can perform the
entire decision lifecycle safely and reproducibly. A trade signal without context, risk, execution and post-trade
diagnosis is incomplete.
0.2 Definitions
 Market State: the timestamped representation of observable market conditions available at a specific
decision time.
 Strategy: a validated decision process with declared inputs, assumptions, regimes, outputs, risk characteristics
and failure modes.
 Candidate Trade: a proposed action that has not yet received portfolio/risk approval.
 Trade Thesis: a conditional explanation of why a candidate has positive expected value and what would
invalidate it.
 Risk Governor: an independent service that can approve, resize, reject, pause or force safe mode regardless
of model confidence.
 Production Model: a versioned model authorized to influence live decisions.
 Research Artifact: an experiment, model, feature, strategy or result that has not received production authority.
 Safe Mode: a state in which new risk is disabled or materially restricted until predefined recovery conditions
are satisfied.
 Autonomy: the ability of the system to operate without continuous user intervention inside predefined
authority boundaries.
 Promotion Gate: a formal evidence checkpoint required before an artifact can move from research toward
live capital.
0.3 Requirement Language
 MUST = mandatory. A build is incomplete if this is missing.
 SHOULD = strong default. Deviations require documentation.
 MAY = optional capability.
 BLOCKER = failure prevents progression to the next milestone.
 OBSERVABILITY = the behavior must be logged and measurable.
 REPRODUCIBLE = the same inputs, versions and configuration should reconstruct the same decision path
where deterministic components are used.
0.4 Safety Invariants
 The AI must never be able to bypass the risk governor.
 Research code must not have production trading credentials.
 Production credentials must be least-privilege and environment-specific.
 An unvalidated model must not directly place live orders.
 Missing or stale critical data must never silently become a valid value.
 Unknown system state must default toward reduced risk, not increased risk.
 Daily return targets must never be used as a compulsory trading quota.
 Loss recovery must never be implemented as automatic risk escalation.
 Model disagreement and out-of-distribution conditions must be allowed to produce abstention.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 9

---

## Page 10

 All live orders must be attributable to a specific decision, strategy version and risk approval.
 Emergency shutdown must remain possible independently of the main AI.
 Every deployment must be reversible.
0.5 Scope Boundaries
The initial build should focus on one exchange/venue family, a deliberately small instrument universe, a limited
set of validated strategy interfaces, and a controlled execution environment. Breadth should be added only after
the core lifecycle works end-to-end. The first system should optimize for correctness, observability and
reproducibility rather than feature count.
0.6 Build Order
1. Repository and environment setup.
2. Configuration and secret-management layer.
3. Canonical data models and schemas.
4. Historical data ingestion and validation.
5. Market-state engine with deterministic features.
6. Replay engine.
7. Strategy interface and one baseline strategy.
8. Backtest engine with costs.
9. Risk governor.
10. Paper execution engine.
11. Decision and trade journal.
12. Monitoring and audit trail.
13. Paper-trading orchestration.
14. Controlled live adapter with hard disabled-by-default switch.
15. Research engine.
16. Historical memory and retrieval.
17. Model/LLM orchestration behind structured interfaces.
18. Controlled adaptation and promotion pipeline.
19. Autonomy gates and scaling.
0.7 Definition of Done
A milestone is not complete because the code runs. It is complete when it has implementation, tests,
observability, documentation, reproducible configuration, failure handling, and a clear demonstration that the
component behaves correctly under normal and adversarial conditions.
PART 1 — SYSTEM ARCHITECTURE
1.1 Reference Architecture
┌──────────────────────────────┐
│ EXTERNAL DATA SOURCES │
│ market / derivatives / news │
│ macro / on-chain / events │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ DATA INGESTION │
│ normalize / timestamp / QC │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ MARKET STATE │
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 10

---

## Page 11

│ features / regimes / context │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ STRATEGY ENGINE │
│ signals / candidates │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ DECISION ENGINE │
│ scenarios / EV / uncertainty │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ RISK GOVERNOR │
│ limits / sizing / veto │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ EXECUTION ENGINE │
│ orders / fills / reconciliation│
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ POSITION MANAGEMENT │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ MEMORY / DIAGNOSIS / METRICS │
└──────────────┬───────────────┘
↓
┌──────────────────────────────┐
│ RESEARCH SANDBOX │
│ test / validate / promote │
└──────────────────────────────┘
1.2 Service Boundaries
 data-service: ingestion, normalization, quality checks, storage and replay.
 state-service: feature computation and market-state construction.
 regime-service: regime probabilities and transition detection.
 strategy-service: strategy plugins and candidate generation.
 decision-service: scenarios, expected value, confidence and abstention.
 risk-service: portfolio limits, sizing, approvals, safe mode and emergency controls.
 execution-service: order lifecycle, fills, reconciliation and execution attribution.
 position-service: open-position state, thesis state and exit management.
 memory-service: trade history, historical analogs and retrieval.
 research-service: experiments, backtests, validation and promotion.
 model-service: model registry, inference, versioning and health.
 orchestrator: schedules and coordinates the lifecycle.
 monitoring-service: metrics, logs, alerts and audit.
 control-plane: configuration, feature flags, permissions and deployment state.
1.3 Architectural Rule
No downstream component should silently perform a responsibility belonging to an upstream component. For
example, an execution adapter must not invent position sizing; a language model must not directly bypass the
risk governor; and a strategy plugin must not directly write to an exchange API. Clear boundaries reduce hidden
coupling and make the system testable.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 11

---

## Page 12

1.4 Control Plane vs Data Plane
The data plane handles live market and trading activity. The control plane handles configuration, permissions,
model promotion, strategy activation, environment state and emergency controls. Production trading should
continue only when the control-plane state authorizes it.
1.5 Research/Production Isolation
 Research credentials MUST be unable to place production orders.
 Production data snapshots used for model evaluation MUST be immutable/versioned.
 Experimental models MUST execute in a sandbox or paper environment.
 Promotion MUST create an explicit artifact/version.
 Rollback MUST be possible without rebuilding the entire system.
PART 2 — DATA PLATFORM
2.1 Data Source Registry
Create a registry in which every data source has: provider identifier, instrument mapping, timestamp semantics,
update frequency, historical coverage, expected schema, quality rules, authentication requirements,
licensing/usage notes, and fallback behavior. Do not hard-code assumptions about source behavior throughout
the application.
2.2 Canonical Market Data
 OHLCV candles with open, high, low, close, volume and explicit close timestamp.
 Trades/ticks with price, size, side where reliably available, exchange timestamp and ingestion timestamp.
 Order-book snapshots and/or incremental depth updates with sequence numbers when supported.
 Mark/index/reference prices where relevant.
 Derivatives metadata, funding, open interest, liquidations and basis where applicable.
 Exchange status and maintenance information where available.
2.3 Timestamp Rules
 Store source timestamp and ingestion timestamp separately.
 Never replace source time with local machine time.
 Normalize all internal timestamps to UTC while preserving source timezone metadata if useful.
 Every feature must declare its lookback and information-availability rule.
 Historical replay must reproduce what would have been known at decision time.
 Clock drift must be monitored.
2.4 Data Quality State
DATA_QUALITY =
VALID
DEGRADED
STALE
MISSING
INCONSISTENT
OUT_OF_ORDER
UNKNOWN
Critical strategy inputs in STALE/MISSING/UNKNOWN state
must block or materially reduce risk according to policy.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 12

---

## Page 13

2.5 Data Storage
Use a layered storage model: immutable raw data, normalized canonical data, derived features, market states,
and research artifacts. Raw data should remain available so that feature definitions can be recomputed. Derived
data must record the source-data and feature versions used to produce it.
2.6 Replay Engine
The replay engine is a first-class component. Given a historical timestamp, it must reconstruct the data state
available at that time and feed it through the same state, strategy, decision, risk and execution interfaces used in
paper/live operation. This is the main defense against research/live divergence.
2.7 Data Failure Policy
 Never forward-fill critical price data across a trading decision without an explicit policy.
 Never fabricate order-book depth.
 Never treat a missing event as proof that no event occurred.
 Mark uncertain features as uncertain.
 Pause or reduce strategies whose required data is unavailable.
 Record the exact data-quality reason for every blocked decision.
PART 3 — MARKET STATE ENGINE
3.1 Canonical Market State
MarketState {
timestamp
asset
venue
data_quality
price_state
volatility_state
trend_state
structure_state
liquidity_state
participation_state
positioning_state
cross_asset_state
event_state
regime_state
uncertainty_state
feature_versions[]
}
3.2 Feature Registry
Every feature must have a unique name, definition, source fields, lookback, normalization method, availability
timestamp, expected range, missing-data behavior, version, and tests. Feature code should not be duplicated
across strategies.
3.3 Multi-Timeframe State
Represent each timeframe separately and then derive relationships between them. For example: higher-
timeframe trend, intermediate structure, lower-timeframe trigger. The state should distinguish alignment, conflict
and neutrality rather than forcing all timeframes into one label.
3.4 Volatility
 Realized volatility.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 13

---

## Page 14

 Range-based measures.
 Volatility regime.
 Volatility expansion/contraction.
 Volatility relative to asset history and current regime.
 Volatility impact on expected move, stop distance and sizing.
3.5 Trend and Structure
The system should maintain multiple descriptions rather than one hard-coded trend label. Examples include
directional slope, breakout state, range state, structural highs/lows, trend persistence and failed-breakout
conditions. Each representation should be testable independently.
3.6 Liquidity
Represent visible depth, spread, recent traded volume, historical liquidity zones and abnormal liquidity changes.
Liquidity state should influence both opportunity quality and execution feasibility.
3.7 Participation and Positioning
Maintain features for volume, aggressive flow where available, open interest, funding, basis, liquidation activity
and other validated positioning measures. Do not assign universal meanings; the research engine must establish
conditional relationships.
3.8 Regime Detection
Implement a baseline deterministic regime classifier first. Later, add probabilistic or learned regime models
behind the same interface. A regime output should include state probabilities, confidence, transition probability
and model version.
3.9 Out-of-Distribution State
The market-state engine must be able to report that the current state is far outside the training/reference
distribution. This should feed uncertainty and risk reduction rather than being forced into the nearest known
regime.
3.10 State Validation
 Replay/live parity tests.
 Feature timestamp tests.
 Missing-data tests.
 Extreme-value tests.
 Unit tests for every derived feature.
 Version consistency checks.
 Distribution monitoring in production.
PART 4 — TRADING KNOWLEDGE MODEL
4.1 Human Trader Decomposition
The research program should convert human trading knowledge into explicit components: context, regime, setup,
evidence, thesis, invalidation, entry, size, management, exit, and review. Do not encode vague statements as rules
until they have measurable definitions.
4.2 Thesis Object
TradeThesis {
thesis_id
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 14

---

## Page 15

timestamp
strategy_id
direction
setup_id
context_summary
supporting_evidence[]
opposing_evidence[]
scenarios[]
invalidation_conditions[]
expected_holding_period
monitoring_variables[]
confidence
uncertainty
version
}
4.3 No-Trade Conditions
 Insufficient evidence.
 Expected value below threshold after costs.
 Regime incompatible with strategy.
 Data quality insufficient.
 Execution cost too high.
 Portfolio exposure too concentrated.
 Out-of-distribution state.
 Event risk outside validated strategy conditions.
 Risk budget unavailable.
 System health degraded.
4.4 Historical Analog Retrieval
Retrieve prior market states using structured similarity across regime, volatility, liquidity, positioning, trend,
structure and event context. Similarity should be measured and stored; retrieval must not simply return visually
similar price charts.
4.5 Strategy Card
 Strategy ID and version.
 Purpose.
 Allowed instruments/timeframes.
 Required inputs.
 Preferred regimes.
 Known failure regimes.
 Entry logic.
 Exit logic.
 Sizing interface.
 Execution requirements.
 Expected holding period.
 Cost sensitivity.
 Capacity estimate.
 Validation history.
 Suspension criteria.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 15

---

## Page 16

PART 5 — RESEARCH ENGINE
5.1 Experiment Registry
Every experiment must have an experiment ID, author/agent, hypothesis, data snapshot, feature versions,
strategy/model versions, training period, validation period, test period, parameter space, metrics, costs, and final
conclusion. The system must record failed experiments as well as successful ones.
5.2 Hypothesis Lifecycle
20. Create hypothesis.
21. Define expected mechanism.
22. Pre-register evaluation method.
23. Run baseline.
24. Run candidate.
25. Run robustness checks.
26. Run out-of-sample.
27. Run walk-forward.
28. Record conclusion.
29. Promote, reject or archive.
5.3 Backtest Engine Requirements
 Event/time-driven simulation.
 Historical data availability fidelity.
 Realistic fees.
 Spread/slippage model.
 Funding/borrow costs where applicable.
 Partial fills where relevant.
 Order latency assumptions.
 Position limits.
 Portfolio constraints.
 Exchange/instrument rules.
 Exact versioned configuration.
 Trade-by-trade audit output.
5.4 Leakage Prevention
The backtester must enforce information timestamps. A feature computed from a completed candle cannot be
used before that candle's information was available. Economic data must use release timestamps rather than later
revised values when historical availability matters. Any feature that cannot prove information availability should
be blocked from production research until its semantics are clarified.
5.5 Multiple Testing
The research platform must record the experiment universe and warn when a result is being selected after many
alternative trials. Test-set reuse must be controlled. A spectacular result after thousands of undisclosed
experiments is weak evidence.
5.6 Walk-Forward
Support rolling or expanding training windows and strictly subsequent test windows. Store each fold
independently. Report fold distribution, not just aggregate return. A strategy that works in one fold and fails
everywhere else should not be presented as robust.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 16

---

## Page 17

5.7 Robustness Matrix
 Parameter perturbation.
 Fee stress.
 Slippage stress.
 Latency stress.
 Missing-data stress.
 Alternative sampling.
 Different time windows.
 Different instruments.
 Different volatility regimes.
 Different liquidity regimes.
 Execution degradation.
 Trade-order resampling.
5.8 Research Scoring
Use a multi-dimensional score rather than one return number. Suggested dimensions: net expectancy, drawdown,
tail loss, stability, out-of-sample consistency, cost sensitivity, capacity, calibration, execution quality and regime
diversity. The score must not allow raw return to dominate safety criteria.
5.9 Promotion Gates
30. Research completeness.
31. Data integrity.
32. Backtest sanity.
33. Leakage audit.
34. Out-of-sample evidence.
35. Walk-forward evidence.
36. Robustness evidence.
37. Paper-trading evidence.
38. Risk review.
39. Execution review.
40. Production canary approval.
PART 6 — STRATEGY ENGINE
6.1 Strategy Interface
Strategy.evaluate(market_state, portfolio_state, strategy_state)
-> StrategyCandidate | NO_SIGNAL
StrategyCandidate {
strategy_id
version
timestamp
asset
direction
thesis
trigger
invalidation
expected_holding_period
scenario_inputs
confidence
uncertainty
suggested_size_hint
}
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 17

---

## Page 18

6.2 Strategy Rules
 Strategy code may generate candidates but MUST NOT place orders.
 Strategy code MUST declare required inputs.
 Strategy code MUST declare valid regimes.
 Strategy code MUST expose a version.
 Strategy code MUST be independently testable.
 Strategy code MUST support replay.
 Strategy code MUST emit reason codes.
6.3 Strategy Ensemble
The ensemble layer should rank candidates, estimate correlation with existing positions, compare opportunity
cost, and pass the best candidates to the decision engine. It should not simply select the highest predicted return.
6.4 Strategy Health
StrategyHealth {
recent_expectancy
long_term_expectancy
drawdown
calibration
execution_quality
regime_fit
sample_size
drift_score
capacity_state
health_state
}
6.5 Suspension
Strategies should have automatic suspension conditions, but suspension must be explainable and logged.
Examples include severe degradation, data mismatch, execution degradation, out-of-distribution conditions or
risk-budget exhaustion. Re-entry should require explicit recovery criteria.
6.6 Baseline Strategy Requirement
Before building complex AI strategies, implement at least one transparent baseline. The purpose is not
necessarily to make money; it establishes whether the data, backtest, execution simulator and evaluation stack
are functioning coherently.
6.7 Strategy Versioning
A strategy version is immutable once promoted. New logic creates a new version. Historical trades must always
reference the exact strategy version that produced them.
PART 7 — DECISION ENGINE
7.1 Candidate Trade
TradeCandidate {
candidate_id
timestamp
asset
venue
strategy_id
strategy_version
market_state_id
thesis_id
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 18

---

## Page 19

direction
scenarios[]
expected_value
uncertainty
confidence
estimated_cost
suggested_entry
suggested_exit_logic
risk_requirements
}
7.2 Scenario Engine
Do not reduce the market to a binary win/loss. Represent multiple plausible paths: continuation, failure, range,
acceleration, reversal, and event-driven shock where relevant. Each scenario should include probability, expected
outcome, key assumptions and invalidation triggers.
7.3 Probability
Probability outputs must be calibrated and monitored. The system should preserve raw model outputs and post-
hoc calibration outputs so researchers can distinguish model behavior from calibration behavior.
7.4 Expected Value
Expected value should include scenario-weighted outcomes, transaction costs and relevant funding/borrow costs.
The system should also calculate sensitivity: if a small change in probability, slippage or outcome assumption
makes the trade negative, classify the opportunity as fragile.
7.5 Opportunity Cost
The decision engine should compare a candidate with waiting, reducing existing exposure, or using capital
elsewhere. A trade is not good merely because it is positive in isolation.
7.6 Confidence and Uncertainty
 Confidence = strength of evidence under the model's calibration framework.
 Uncertainty = quality/novelty/conflict of information and model disagreement.
 High confidence + high uncertainty is not automatically a strong trade.
 Out-of-distribution states should increase uncertainty.
 Model disagreement should be visible.
7.7 Abstention
ABSTAIN is a valid decision. The engine must support NO_TRADE as a successful output and record the reason.
Research should later measure whether abstention improved portfolio quality.
7.8 Decision Record
Every approved or rejected candidate must produce an immutable decision record with the input state, model
versions, strategy version, scenarios, risk result and final action. This is essential for auditing and learning.
PART 8 — PORTFOLIO AND RISK
8.1 Risk Constitution
RiskConstitution {
max_position_notional
max_position_risk
max_portfolio_notional
max_leverage
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 19

---

## Page 20

max_daily_loss
max_drawdown
max_asset_concentration
max_factor_concentration
min_liquidity
max_slippage
max_open_positions
event_risk_policy
emergency_policy
safe_mode_policy
}
8.2 Independent Authority
The risk service must be able to reject a candidate even when every model predicts profit. It must not depend on
the model's self-reported risk estimate. It calculates or verifies exposure independently.
8.3 Position Sizing
Sizing should be a function of estimated edge, uncertainty, volatility, liquidity, stop/invalidation distance,
portfolio exposure, correlation, drawdown state and strategy limits. Do not implement "double after loss" or any
recovery-escalation logic.
8.4 Portfolio Risk
 Gross exposure.
 Net exposure.
 Directional exposure.
 Asset concentration.
 Factor concentration.
 Correlated strategy exposure.
 Liquidity-adjusted exposure.
 Stress loss.
 Potential margin/liquidation exposure.
8.5 Leverage
Leverage limits are hard constraints. A strategy cannot increase leverage because it believes its signal is unusually
strong. Any leverage policy change must occur through a configuration/version approval process outside the
trading model.
8.6 Drawdown
Define staged risk states, for example NORMAL, CAUTION, DEFENSIVE, HALTED. Thresholds must be configurable
and tested. Transitions should be deterministic and logged.
8.7 Tail Risk
Stress the portfolio against sudden price moves, spread expansion, liquidity loss, correlation convergence,
exchange downtime and delayed execution. The objective is survival rather than perfect prediction.
8.8 Kill Switch
 Manual kill switch.
 Automated kill switch.
 Data-integrity kill switch.
 Execution-anomaly kill switch.
 Unexpected-position kill switch.
 Drawdown kill switch.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 20

---

## Page 21

 Credential/API anomaly kill switch.
8.9 Safe Mode
In safe mode, the system must stop generating new risk unless a specific recovery policy permits it. Existing
positions must be reconciled and managed under conservative rules. Safe mode must survive failure of the main
AI model.
8.10 Risk Approval States
RISK_APPROVED
RISK_REDUCED
RISK_REJECTED
SAFE_MODE
EMERGENCY_STOP
MANUAL_REVIEW_REQUIRED
PART 9 — EXECUTION ENGINE
9.1 Order Intent
OrderIntent {
decision_id
position_intent
side
quantity
order_type
limit_price
urgency
max_slippage
time_in_force
execution_policy
risk_approval_id
}
9.2 Order Lifecycle
CREATED → SUBMITTED → ACKNOWLEDGED → PARTIALLY_FILLED → FILLED
↘ REJECTED
↘ CANCELED
↘ EXPIRED
↘ UNKNOWN → RECONCILIATION_REQUIRED
9.3 Execution Policies
 Passive: prioritize price and accept non-fill risk.
 Aggressive: prioritize fill certainty under a bounded cost.
 Urgent: use only when thesis/risk requires rapid reduction.
 Slice: split larger orders to manage impact.
 Emergency: reduce risk according to emergency policy.
9.4 Slippage and Fees
The execution engine must estimate expected costs before order submission and measure actual costs afterward.
A trade should be marked for execution review when realized cost materially exceeds expected cost.
9.5 Reconciliation
Internal position state must be periodically reconciled with venue/exchange state. Any mismatch must block new
exposure until resolved or handled by a documented safe policy.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 21

---

## Page 22

9.6 Execution Attribution
 Decision price.
 Expected fill price.
 Actual fill price.
 Spread.
 Slippage.
 Fees.
 Funding/borrow cost.
 Latency.
 Fill ratio.
 Market impact estimate.
9.7 No Direct Model-to-Exchange Path
No AI model, LLM, strategy plugin or research script may hold direct production order permissions. Only the
execution service, after risk approval, can submit orders. This is a critical security boundary.
PART 10 — POSITION MANAGEMENT
10.1 Position State
PositionState {
position_id
asset
venue
quantity
average_entry
unrealized_pnl
realized_pnl
thesis_id
scenario_state
invalidation_state
health_state
risk_state
management_actions[]
}
10.2 Live Thesis
The position manager continuously compares the current market state with the thesis that justified entry. It
should track whether supporting evidence persists, weakens, or disappears.
10.3 Add/Reduce
 Adding requires a separately evaluated positive-expectancy condition.
 Adding must pass the risk governor again.
 Adding because the position is losing is prohibited unless the strategy explicitly defines and validates that
behavior.
 Reducing may be triggered by thesis deterioration, risk changes, liquidity changes or portfolio constraints.
 Every action needs a reason code.
10.4 Exit Engine
 Target achieved.
 Thesis invalidated.
 Expected value deteriorated.
 Scenario probability changed materially.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 22

---

## Page 23

 Time horizon expired.
 Risk limit reached.
 Portfolio rebalance.
 Emergency condition.
 Execution/venue issue.
10.5 Position Journal
The position journal should preserve the full path from entry to exit. This is especially important for training
future models on management behavior, not only entry signals.
PART 11 — LEARNING AND MEMORY
11.1 Trade Memory
Store every trade and meaningful rejected candidate. Rejected opportunities are valuable because they reveal
whether the system's filters were useful.
11.2 Outcome Classification
OutcomeType =
EXPECTED_WIN
EXPECTED_LOSS
THESIS_FAILURE
MODEL_ERROR
REGIME_ERROR
EXECUTION_ERROR
DATA_ERROR
RISK_INTERVENTION
OPERATIONAL_ERROR
UNKNOWN
11.3 Loss Diagnosis
The diagnostic engine should compare the observed path with the expected scenario distribution. A normal loss
should not automatically trigger retraining. A repeated cluster of unexpected losses may indicate regime drift,
model degradation, execution problems or a broken assumption.
11.4 Model Drift
 Feature distribution drift.
 Prediction distribution drift.
 Calibration drift.
 Residual/error drift.
 Regime-conditional drift.
 Performance drift.
11.5 Strategy Drift
Monitor whether the strategy's expected edge, trade frequency, holding period, execution cost and regime
distribution differ from validation expectations.
11.6 Controlled Adaptation
LIVE OBSERVATION
↓
RESEARCH HYPOTHESIS
↓
EXPERIMENT
↓
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 23

---

## Page 24

VALIDATION
↓
PAPER TEST
↓
SMALL LIVE CANARY
↓
PROMOTION REVIEW
↓
PRODUCTION VERSION
11.7 Model Registry
 Model ID.
 Version.
 Training data snapshot.
 Features/version.
 Training code version.
 Validation results.
 Known limitations.
 Promotion status.
 Deployment timestamp.
 Rollback target.
 Current health.
11.8 The Learning Rule
The production system must not learn by directly changing critical behavior after individual trades. It learns
through accumulated evidence processed by the research pipeline. This is the key distinction between controlled
adaptation and chaotic self-modification.
PART 12 — LLM / AI ORCHESTRATION
12.1 Appropriate LLM Roles
 Research summarization.
 Hypothesis generation.
 News/event extraction.
 Trade-thesis drafting.
 Post-trade narrative organization.
 Documentation.
 Research-question generation.
 Retrieval-assisted analysis.
 Code assistance inside the sandbox.
12.2 Restricted LLM Roles
 LLM must not directly place production orders.
 LLM must not alter risk limits.
 LLM must not approve its own strategy for production.
 LLM must not fabricate missing market data.
 LLM must not be treated as a numerical oracle.
 LLM outputs must be schema-validated before downstream use.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 24

---

## Page 25

12.3 Structured Output
LLMOutput {
task
source_refs[]
extracted_facts[]
hypotheses[]
uncertainty
recommended_action
prohibited_assumptions[]
model_version
}
12.4 Tool Permissions
Use least-privilege tools. A research agent may read datasets and run backtests. A production monitoring agent
may read live state. An execution service may submit approved orders. No single general-purpose agent should
receive every permission.
12.5 Hallucination Controls
 Require source references for factual external claims.
 Validate numerical values against structured data.
 Reject malformed outputs.
 Do not let narrative confidence override quantitative gates.
 Store prompts and model versions for reproducibility.
12.6 Prompt Governance
Prompts that influence research or production decisions should be versioned like code. Changes should be
evaluated because prompt changes can change system behavior.
PART 13 — MONITORING AND OBSERVABILITY
13.1 Required Logs
 Data ingestion logs.
 Feature computation logs.
 Strategy evaluation logs.
 Candidate generation logs.
 Decision logs.
 Risk approvals/rejections.
 Order lifecycle logs.
 Position reconciliation logs.
 Model inference logs.
 System health logs.
 Configuration/deployment logs.
13.2 Metrics
 Net PnL.
 Gross PnL.
 Drawdown.
 Exposure.
 Leverage.
 Turnover.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 25

---

## Page 26

 Fees.
 Slippage.
 Win/loss distribution.
 Expectancy.
 Strategy health.
 Model calibration.
 Data freshness.
 Order failure rate.
 Latency.
 Position mismatch rate.
13.3 Alerts
Alerts should be categorized as INFO, WARNING, CRITICAL and EMERGENCY. CRITICAL and EMERGENCY
conditions should have explicit automated actions where safe to do so.
13.4 Audit Trail
For any live trade, an auditor should be able to answer: what data did the system see, what state did it construct,
which strategy proposed the trade, what thesis was formed, what probabilities were estimated, what risk checks
passed, which model versions were used, what orders were sent, what happened during the position, and why it
exited.
13.5 Dashboard Groups
 Capital and risk.
 Open positions.
 Strategy health.
 Model health.
 Execution quality.
 Data quality.
 System health.
 Research pipeline.
 Deployment state.
PART 14 — SECURITY AND OPERATIONAL SAFETY
14.1 Secrets
 Never hard-code API keys.
 Use environment-specific secret storage.
 Separate read-only market keys from trading keys where supported.
 Use the minimum required permissions.
 Rotate credentials.
 Audit access.
14.2 Production Permissions
Production order permissions must be isolated from development and research. The safest default is that all new
environments start with trading disabled.
14.3 Network and API Safety
 Allow-list production endpoints where practical.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 26

---

## Page 27

 Validate exchange responses.
 Rate-limit requests.
 Handle retries idempotently.
 Prevent duplicate order submission.
 Detect unexpected API responses.
14.4 Backup and Recovery
Back up configuration, model registry, trade records, research artifacts and critical state. Recovery must include
position reconciliation before new orders are allowed.
14.5 Failure Drills
 Disconnect market data.
 Delay data.
 Return malformed data.
 Drop exchange connection.
 Duplicate an order response.
 Corrupt local position state.
 Force model failure.
 Force risk-service failure.
 Restart components during an open position.
PART 15 — TESTING
15.1 Testing Pyramid
END-TO-END
/------------- / CHAOS / REPLAY /------------------- /
INTEGRATION TESTS /----------------------- / UNIT TESTS /--------
-------------------\
15.2 Unit Tests
 Feature calculations.
 Position sizing.
 Risk limits.
 Fee calculations.
 Slippage calculations.
 Order-state transitions.
 PnL calculations.
 Portfolio exposure.
 Regime labels.
 Schema validation.
15.3 Integration Tests
 Data → state.
 State → strategy.
 Strategy → decision.
 Decision → risk.
 Risk → execution.
 Execution → position.
 Position → memory.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 27

---

## Page 28

 Research → model registry.
15.4 Replay Tests
Replay the same historical window through research and live-like pipelines. Outputs should match where the
same deterministic components and configuration are used. Any discrepancy must be explained.
15.5 Risk Tests
 Attempt to exceed position limit.
 Attempt to exceed leverage.
 Attempt to exceed daily loss.
 Attempt to trade with stale data.
 Attempt to bypass risk.
 Attempt to trade in safe mode.
 Attempt to submit duplicate order.
 Attempt to use an unapproved model.
15.6 Chaos Tests
Deliberately break infrastructure and verify safe behavior. The system should fail closed around financial risk.
Chaos testing should become a recurring release requirement.
15.7 Regression Tests
Every bug that causes or could cause incorrect trading behavior should become a regression test. This turns
incidents into permanent engineering protection.
15.8 Acceptance Standard
No production capability is complete until tests demonstrate correct normal behavior, correct boundary behavior
and safe failure behavior.
PART 16 — DEPLOYMENT
16.1 Environments
LOCAL_DEV
↓
RESEARCH
↓
BACKTEST
↓
PAPER
↓
CANARY
↓
PRODUCTION
↓
SAFE_MODE / EMERGENCY
16.2 Configuration
Configuration must be externalized and versioned. Separate code from environment-specific settings. Risk limits
must be explicit and auditable.
16.3 CI/CD
 Run unit tests.
 Run schema checks.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 28

---

## Page 29

 Run integration tests.
 Run replay tests.
 Run security checks.
 Build immutable artifact.
 Deploy to non-production.
 Run smoke tests.
 Require promotion gate for production.
16.4 Rollback
Every production deployment must identify the previous known-good version. Rollback must be executable
without editing code manually during an incident.
16.5 Paper Trading
Paper trading should use the same state, strategy, decision, risk and execution interfaces as production. Only the
final order adapter differs.
16.6 Live Canary
The first live deployment of a new strategy or model should use a small predefined risk budget. Canary
performance and operational behavior must be reviewed before scaling.
16.7 Scaling
Scaling is a separate validation problem. Increasing capital changes execution and market impact. The system
should measure whether expected net edge survives increasing size.
PART 17 — AUTONOMOUS OPERATING LOOP
17.1 OBSERVE
Collect current market, portfolio, execution and event state.
17.2 VALIDATE
Check data freshness, integrity, system health and venue status.
17.3 INTERPRET
Construct market state, regime probabilities, liquidity and uncertainty.
17.4 GENERATE
Run eligible strategies and create candidate theses.
17.5 EVALUATE
Generate scenarios, probabilities, expected value and opportunity cost.
17.6 RISK APPROVE
Independently verify exposure, limits, leverage, correlation and safe-state conditions.
17.7 EXECUTE
Submit bounded order intents through the execution service.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 29

---

## Page 30

17.8 MANAGE
Monitor thesis health, scenario changes, risk and execution.
17.9 DIAGNOSE
Record outcome, classify result and attribute performance.
17.10 RESEARCH
Feed accumulated evidence into the research pipeline without directly mutating production.
17.11 Canonical State Machine
SYSTEM:
STARTUP
↓
SELF_CHECK
↓
DATA_READY? ──NO──> SAFE_MODE
↓YES
MARKET_STATE_READY
↓
STRATEGY_SCAN
↓
CANDIDATE_FOUND? ──NO──> WAIT
↓YES
DECISION_EVALUATION
↓
RISK_CHECK
├──REJECT──> LOG + WAIT
├──REDUCE──> EXECUTION
├──APPROVE──> EXECUTION
└──EMERGENCY──> SAFE_MODE
↓
POSITION_MANAGEMENT
↓
EXIT / FLAT
↓
DIAGNOSIS
↓
MEMORY
↓
WAIT / NEXT_EVENT
PART 18 — ACCEPTANCE CRITERIA
18.1 System-Level Gates
41. All critical data has timestamp semantics.
42. Replay can reconstruct market state.
43. Every trade is attributable to a strategy/model version.
44. Risk governor can veto any trade.
45. Production credentials are isolated.
46. Position reconciliation works.
47. Safe mode works.
48. Kill switch works.
49. Logs and audit trail are complete.
50. Backtests include realistic costs.
51. Paper trading uses production-equivalent decision flow.
52. Rollback is tested.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 30

---

## Page 31

18.2 Strategy Gate
 Defined thesis.
 Defined entry.
 Defined invalidation.
 Defined exit.
 Defined eligible regimes.
 Defined no-trade conditions.
 Out-of-sample evidence.
 Walk-forward evidence.
 Robustness evidence.
 Execution-cost evidence.
 Strategy card completed.
18.3 Model Gate
 Model version registered.
 Training data snapshot registered.
 Features versioned.
 Validation completed.
 Calibration evaluated.
 Drift monitoring defined.
 Failure conditions documented.
 Rollback model identified.
18.4 Risk Gate
 Hard limits enforced.
 Limit tests pass.
 Safe mode tests pass.
 Kill switch tests pass.
 Correlation/concentration checks pass.
 Drawdown behavior tested.
 Emergency reconciliation tested.
18.5 Execution Gate
 Order lifecycle correct.
 Duplicate-order prevention.
 Partial-fill handling.
 Reconciliation.
 Slippage measurement.
 Fee accounting.
 Venue failure handling.
 Emergency exit behavior.
18.6 Autonomy Gate
Full autonomy is allowed only after the system demonstrates that it can operate through normal, adverse and
abnormal conditions without violating the risk constitution. The autonomy gate should consider not only
profitability but operational reliability, uncertainty handling, abstention behavior, model stability, strategy health
and incident history.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 31

---

## Page 32

PART 19 — BUILD ROADMAP
M0 — FOUNDATION
Create repository, configuration, logging, schemas, environments, secret boundaries and test harness.
M1 — DATA
Implement canonical data ingestion, validation, storage and historical replay.
M2 — STATE
Implement market-state objects, deterministic features, multi-timeframe context and baseline regime detection.
M3 — RESEARCH
Implement experiment registry, baseline backtester, cost model and research reports.
M4 — STRATEGY
Implement strategy interface, one transparent baseline, candidate objects and strategy cards.
M5 — DECISION
Implement scenarios, expected value, uncertainty, abstention and decision records.
M6 — RISK
Implement risk constitution, sizing, portfolio checks, drawdown states and kill switches.
M7 — PAPER EXECUTION
Implement order simulator, order state machine, position reconciliation and paper trading.
M8 — OBSERVABILITY
Complete dashboards, alerts, audit trail and incident workflow.
M9 — LIVE CANARY
Enable a tightly bounded live adapter only after all prior gates pass.
M10 — MEMORY
Implement trade memory, historical analog retrieval and loss diagnosis.
M11 — AI/LLM
Add structured AI research and unstructured-data assistance behind permission boundaries.
M12 — CONTROLLED LEARNING
Add research-to-production promotion and model/strategy health.
M13 — SCALING
Test capacity, execution impact and gradual risk scaling.
M14 — BOUNDED AUTONOMY
Enable autonomous strategy selection and controlled research only after evidence supports it.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 32

---

## Page 33

19.1 Build Rule
The coding agent must finish and test one milestone before treating the next milestone as active. Do not
implement a large amount of unfinished functionality simply because the final architecture is known. The system
should become progressively more capable while remaining runnable at each stage.
19.2 Agent Review Checkpoint
 State what was built.
 State what was intentionally not built.
 List tests run and results.
 List unresolved risks.
 List configuration changes.
 List database/schema migrations.
 List new permissions.
 List known limitations.
 Confirm whether live trading remains disabled.
 Provide the next smallest safe milestone.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 33

---

## Page 34

APPENDIX A — CANONICAL SCHEMAS
MarketState
{
state_id, timestamp, asset, venue,
data_quality, price_state, volatility_state,
trend_state, structure_state, liquidity_state,
participation_state, positioning_state,
cross_asset_state, event_state,
regime_state, uncertainty_state,
feature_versions[]
}
TradeCandidate
{
candidate_id, timestamp, asset, venue,
strategy_id, strategy_version, market_state_id,
thesis_id, direction, scenarios[],
expected_value, confidence, uncertainty,
estimated_cost, entry_plan, exit_plan,
risk_requirements
}
RiskDecision
{
risk_decision_id, candidate_id, timestamp,
state, approved_size, rejected_reasons[],
exposure_before, exposure_after,
leverage_before, leverage_after,
limit_snapshot_id, risk_engine_version
}
OrderIntent
{
order_intent_id, decision_id, position_id,
side, quantity, order_type, limit_price,
urgency, max_slippage, time_in_force,
execution_policy, risk_approval_id
}
TradeRecord
{
trade_id, position_id, decision_id,
strategy_id, strategy_version, model_versions[],
entry_time, exit_time, entry_price, exit_price,
quantity, fees, funding, slippage,
pnl, max_favorable_excursion,
max_adverse_excursion, exit_reason,
outcome_type, diagnosis_version
}
Experiment
{
experiment_id, hypothesis, data_snapshot,
feature_versions[], strategy_versions[],
train_window, validation_window, test_window,
parameters, metrics, robustness_results[],
leakage_audit, conclusion, promotion_state
}
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 34

---

## Page 35

APPENDIX B — STATE MACHINES
B.1 Strategy Lifecycle
DRAFT → RESEARCH → VALIDATING → PAPER → CANARY → PRODUCTION
↘ REJECTED
PRODUCTION → DEGRADED → SUSPENDED → REVALIDATION → CANARY/ARCHIVE
B.2 Model Lifecycle
TRAINED → VALIDATED → REGISTERED → PAPER → CANARY → PRODUCTION
↘ REJECTED
PRODUCTION → DRIFT_ALERT → REVIEW → ROLLBACK / REVALIDATE
B.3 System Lifecycle
BOOT → SELF_CHECK → READY → ACTIVE
↘
SAFE_MODE → RECOVERY_CHECK → READY
↘
EMERGENCY_STOP
APPENDIX C — ERROR TAXONOMY
 DATA_STALE
 DATA_MISSING
 DATA_OUT_OF_ORDER
 DATA_SCHEMA_MISMATCH
 FEATURE_FAILURE
 REGIME_UNCERTAIN
 MODEL_UNAVAILABLE
 MODEL_DRIFT
 STRATEGY_DISABLED
 RISK_LIMIT
 CORRELATION_LIMIT
 LEVERAGE_LIMIT
 DRAWDOWN_LIMIT
 LIQUIDITY_LIMIT
 ORDER_REJECTED
 ORDER_UNKNOWN
 RECONCILIATION_MISMATCH
 EXCHANGE_UNAVAILABLE
 DUPLICATE_ORDER
 SLIPPAGE_EXCEEDED
 SYSTEM_HEALTH_DEGRADED
 SAFE_MODE
 EMERGENCY_STOP
APPENDIX D — REASON CODES
NO_TRADE_LOW_EV — Expected value below threshold
NO_TRADE_HIGH_UNCERTAINTY — Uncertainty too high
NO_TRADE_REGIME_MISMATCH — Strategy not validated for current regime
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 35

---

## Page 36

NO_TRADE_DATA — Required data unavailable
NO_TRADE_RISK — Risk budget unavailable
NO_TRADE_EXECUTION — Execution cost/liquidity unacceptable
EXIT_THESIS_INVALID — Thesis invalidated
EXIT_RISK — Risk constraint requires reduction
EXIT_TARGET — Target condition satisfied
EXIT_TIME — Maximum holding period reached
RISK_REDUCED — Risk governor resized
RISK_REJECTED — Risk governor vetoed
SAFE_MODE_DATA — Critical data problem
SAFE_MODE_EXECUTION — Execution anomaly
SAFE_MODE_SYSTEM — System health problem
APPENDIX E — CONFIGURATION PRINCIPLES
 Keep risk limits outside model code.
 Keep environment configuration separate from strategy code.
 Version all production configurations.
 Never make a secret part of source control.
 Require explicit enablement for live trading.
 Use conservative defaults.
 Fail closed around capital risk.
 Document every configuration change.
APPENDIX F — TEST MATRIX
Data | Missing data | Expected: Block/reduce | Acceptance: Pass
Data | Stale feed | Expected: Safe mode or strategy block | Acceptance: Pass
State | Timestamp leakage | Expected: Test must fail | Acceptance: Pass
Strategy | Unapproved strategy | Expected: Reject | Acceptance: Pass
Decision | High uncertainty | Expected: Abstain/reduce | Acceptance: Pass
Risk | Over-leverage | Expected: Reject | Acceptance: Pass
Risk | Drawdown limit | Expected: Reduce/stop | Acceptance: Pass
Execution | Duplicate order | Expected: Prevent/reconcile | Acceptance: Pass
Execution | Partial fill | Expected: Track correctly | Acceptance: Pass
Execution | Exchange outage | Expected: Safe mode | Acceptance: Pass
Position | State mismatch | Expected: Block new risk | Acceptance: Pass
Model | Missing model | Expected: No live decision | Acceptance: Pass
Model | Drift alert | Expected: Review/reduce | Acceptance: Pass
System | Kill switch | Expected: Stop new risk | Acceptance: Pass
Deployment | Rollback | Expected: Restore prior version | Acceptance: Pass
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 36

---

## Page 37

APPENDIX G — RESEARCH CHECKLIST
 Is the hypothesis explicit?
 Was it defined before testing?
 Is the data timestamp-correct?
 Could future information leak in?
 How many alternatives were tested?
 Does it survive costs?
 Does it survive parameter perturbation?
 Does it survive different periods?
 Does it survive different regimes?
 Does it survive execution degradation?
 Is there out-of-sample evidence?
 Is there walk-forward evidence?
 Is the result economically meaningful?
 Is capacity known?
 Are failure modes documented?
APPENDIX H — PRODUCTION CHECKLIST
 Strategy card approved.
 Model version approved.
 Data sources healthy.
 Risk constitution loaded.
 Kill switch tested.
 Safe mode tested.
 Position reconciliation tested.
 Execution adapter tested.
 Monitoring active.
 Alerts tested.
 Rollback version available.
 Paper evidence reviewed.
 Canary budget defined.
 Live trading explicitly enabled by authorized deployment step.
APPENDIX I — FIRST BUILD SPRINT
53. Create repository and environment separation.
54. Create canonical schemas.
55. Create configuration loader with safe defaults.
56. Create logging/audit framework.
57. Create data source interface.
58. Create historical data adapter.
59. Create data-quality validator.
60. Create MarketState object.
61. Create replay engine skeleton.
62. Write unit tests for timestamps and schema validation.
63. Keep live trading disabled.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 37

---

## Page 38

The first sprint should end with a runnable research skeleton that can ingest historical data, validate it, construct
a versioned market state and replay that state deterministically. It should not attempt autonomous live trading.
APPENDIX J — FINAL IMPLEMENTATION DIRECTIVE FOR THE
BUILDING AGENT
You are building the Autonomous Trader described by this specification and the project's two preceding
reference documents. Treat the 10K document as the original reasoning and vision, the 80K document as the
master conceptual blueprint, and this document as the implementation authority for software structure and
operational controls.
Build incrementally. Do not skip milestones. Do not connect live capital prematurely. Do not assume that
profitability has been demonstrated. Do not create fake performance to satisfy a target. Do not hard-code
a 5% daily return objective. Do not allow any model, LLM, research agent or strategy plugin to bypass the
risk governor.
Every important component must be modular, versioned, testable, observable and replayable. Every live
decision must be attributable to market-state, strategy, model, decision and risk versions. Every order
must be attributable to a risk approval. Every production change must be reversible.
When an implementation detail is genuinely unspecified, choose the simplest safe implementation, document
the assumption, and isolate it behind an interface so it can be replaced. Do not silently invent financial
logic.
Build the research system before the autonomous trading system. Build deterministic foundations before
adaptive components. Build paper execution before live execution. Build risk controls before granting
strategies meaningful capital. Build the learning pipeline as a controlled research loop, not as
unrestricted self-modification.
At the end of every milestone, report:
1. what was built;
2. what was tested;
3. test results;
4. what remains incomplete;
5. known risks;
6. configuration changes;
7. data/schema changes;
8. permissions introduced;
9. whether live trading is still disabled;
10. the exact next milestone.
The final objective is not a bot that trades constantly. The objective is a system that can observe,
understand, abstain, act, manage risk, execute correctly, learn from evidence and remain safe when its
assumptions fail.
FINAL DESIGN PRINCIPLE
The autonomous trader should be engineered as a governed decision organization, not as a single predictive
model. The strongest possible version of the system combines machine-scale memory and computation with the
discipline of professional trading: context before signal, thesis before entry, risk before size, evidence before
adaptation, and survival before scaling.
The machine should have enough intelligence to find opportunities, but the architecture should be strong
enough to prevent intelligence from becoming recklessness. That is the central engineering requirement of the
entire project.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 38

---

## Page 39

PART 20 — DETAILED IMPLEMENTATION CONTRACTS
This part expands the architecture into implementation contracts. The goal is to remove ambiguity between
components. The coding agent should treat each contract as an agreement: what goes in, what comes out, what
can fail, what must be logged, and what authority the component has. A component that cannot clearly state
these things is not ready to become part of the production trading path.
The most important architectural principle is separation of authority. Components that discover opportunities
should not control capital. Components that control capital should not discover their own rules. Components
that learn should not silently change production. This separation is intentional and should remain even when the
system becomes more advanced.
20.1 Data Service Contract
The data service is responsible for obtaining source information and converting it into canonical records. It must
not interpret whether the data is bullish or bearish. Its job is fidelity. Each record should preserve source
timestamp, ingestion timestamp, source identifier, instrument identifier, sequence information when available,
and quality status.
 Input: source configuration and requested time window.
 Output: canonical records plus quality metadata.
 Failure: explicit error state; never fabricated values.
 Authority: read external data and write approved data stores only.
 Forbidden: strategy decisions, order placement, risk-limit modification.
The data service should expose both historical and streaming interfaces. The historical interface should return
immutable snapshots. The streaming interface should emit ordered events when possible. Consumers should be
able to subscribe by asset, venue and data type without knowing provider-specific schemas.
20.2 Market State Service Contract
The market-state service transforms canonical data into a timestamped state. It should be deterministic for
deterministic features. If a learned component contributes to state, its model version must be recorded.
 Input: canonical market records and approved external context.
 Output: MarketState with feature versions and quality state.
 Failure: degraded or unavailable state, never silent substitution.
 Authority: calculate state; no capital authority.
 Required: replay compatibility.
The state service should expose get_state(timestamp, asset, venue) for replay and get_latest_state(asset, venue)
for live operation. The same feature implementation should serve both paths to reduce research/live divergence.
20.3 Strategy Service Contract
The strategy service receives a market state and portfolio context and produces zero or more candidate
opportunities. It should not know exchange API details.
 Input: MarketState, PortfolioState, StrategyConfig.
 Output: StrategyCandidate or NO_SIGNAL.
 Failure: candidate rejected and error recorded.
 Authority: propose only.
 Forbidden: direct order submission, direct leverage changes, direct production configuration changes.
A strategy may be complex internally, but its external behavior should remain predictable. Every candidate should
contain enough information for downstream components to understand what the strategy wants to do and why.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 39

---

## Page 40

20.4 Decision Service Contract
The decision service is responsible for comparing candidates against scenarios, expected value, uncertainty,
portfolio opportunity cost and strategy health. It does not have final authority over capital.
 Input: candidate, market state, portfolio state, strategy health, historical evidence.
 Output: DecisionRecord.
 Failure: abstention.
 Authority: recommend APPROVE/REJECT/REDUCE; risk service has final authority.
 Required: reason codes and model versions.
Decision generation should be idempotent for the same state/version where deterministic components are used.
If stochastic models are used, the random seed and sampling configuration must be recorded when
reproducibility is required.
20.5 Risk Service Contract
The risk service is the independent financial safety boundary. It receives a proposed decision and independently
calculates or verifies exposure.
 Input: DecisionRecord, live portfolio, account state, risk constitution, liquidity state.
 Output: RiskDecision.
 Failure: reject or safe mode depending on severity.
 Authority: approve, resize, reject, safe mode, emergency stop.
 Forbidden: being overridden by a model confidence score.
The risk service should be deliberately simpler than the research layer where practical. Its job is not to be clever.
Its job is to be hard to surprise. Every risk rule should have a direct test that attempts to violate it.
20.6 Execution Service Contract
The execution service receives only risk-approved order intents. It manages the order lifecycle and reports fills.
 Input: approved OrderIntent.
 Output: OrderState, FillEvents and reconciliation status.
 Failure: UNKNOWN state followed by reconciliation.
 Authority: submit/cancel/modify approved orders within limits.
 Forbidden: changing the strategy thesis or increasing risk beyond approval.
The execution service must assume APIs can behave unexpectedly. It should never infer that an order was filled
merely because a request was accepted. Actual state must be confirmed and reconciled.
20.7 Position Service Contract
The position service maintains the authoritative internal representation of open positions and their associated
theses.
 Input: fills, market state, thesis updates, risk state.
 Output: PositionState and management intents.
 Failure: reconciliation required.
 Authority: propose or execute management actions only through risk/execution boundaries.
Position state should survive process restarts. Recovery should reconstruct positions from durable state and
external venue state before new risk is allowed.
20.8 Research Service Contract
The research service is isolated from production capital. It manages experiments, backtests, validation and
candidate promotion.
 Input: immutable data snapshots, code/model versions, hypotheses.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 40

---

## Page 41

 Output: ExperimentRecord and PromotionCandidate.
 Failure: experiment invalid or incomplete.
 Authority: research only; no direct production order authority.
The research service should make reproducibility a first-class feature. A researcher should be able to select an
experiment ID and reconstruct the data, code, parameters and metrics that produced the result.
20.9 Memory Service Contract
The memory service stores structured market experiences and retrieves relevant prior cases.
 Input: state vectors, trade records, research artifacts.
 Output: ranked historical analogs and evidence records.
 Failure: no retrieval or low-confidence retrieval, not fabricated analogs.
 Authority: informational only.
Historical retrieval should return similarity scores and timestamps. The decision engine must be able to
distinguish 'strong analog' from 'weak analog' and should not treat a weak match as evidence equivalent to a
strong match.
20.10 Model Service Contract
The model service handles loading, inference, health checks and version management for approved models.
 Input: schema-validated feature/state object.
 Output: prediction plus model version and uncertainty metadata.
 Failure: model unavailable; downstream system abstains or uses an explicitly approved fallback.
 Authority: inference only.
 Forbidden: changing its own production status.
Model loading should be atomic. A new model should be loaded into an isolated process or versioned runtime,
health-checked, and only then made available for approved inference.
PART 21 — DATABASE AND STORAGE DESIGN
The system needs durable storage for raw data, normalized data, state snapshots, trades, orders, experiments,
models, configurations, audit events and monitoring. The exact database technology can be selected by the
implementation agent based on scale, but the logical separation must remain.
21.1 Raw Data Store
Immutable source records. Never overwrite historical raw records merely because a parser improves.
21.2 Canonical Data Store
Normalized records used by state construction and replay.
21.3 Feature Store
Versioned derived features with explicit feature definitions.
21.4 Market State Store
Timestamped MarketState snapshots for replay, auditing and research.
21.5 Trading Ledger
Orders, fills, positions, fees, funding and realized/unrealized PnL.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 41

---

## Page 42

21.6 Research Store
Experiments, hypotheses, model artifacts, validation results and promotion decisions.
21.7 Configuration Store
Risk constitution, strategy activation, environment configuration and feature flags.
21.8 Audit Store
Immutable or append-only records of critical decisions, deployments and permissions.
21.9 Metrics Store
Time-series metrics for system, model, strategy and execution monitoring.
Every durable object should have an identifier, creation timestamp, update/version semantics and provenance.
Critical records should not be deleted as part of normal operation. Retention policies should be explicit.
21.10 Database Rules
 Use UTC timestamps internally.
 Use unique identifiers rather than ambiguous natural keys.
 Record schema versions.
 Use transactions for state transitions where appropriate.
 Make order submission idempotent.
 Use append-only audit events for critical financial actions.
 Back up critical stores.
 Test restoration.
 Keep research and production namespaces separate.
PART 22 — EVENT BUS AND ASYNCHRONOUS PROCESSING
The platform should support event-driven processing because markets generate continuous streams of updates.
A message/event layer can decouple producers and consumers, but event-driven design must not introduce
uncontrolled race conditions.
MarketDataEvent
A new normalized market observation is available.
MarketStateUpdated
A new validated state is available.
RegimeUpdated
Regime estimate changed materially.
StrategyCandidateCreated
A strategy produced a candidate.
DecisionCreated
Decision engine produced a decision.
RiskDecisionCreated
Risk service approved/rejected/resized.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 42

---

## Page 43

OrderSubmitted
Execution submitted an order.
OrderFilled
A fill occurred.
OrderStateChanged
Order changed state.
PositionChanged
Position changed.
RiskStateChanged
Portfolio risk state changed.
SafeModeEntered
System entered safe mode.
ModelHealthChanged
Model health state changed.
StrategyHealthChanged
Strategy health state changed.
ExperimentCompleted
Research experiment completed.
PromotionRequested
Artifact requested production promotion.
DeploymentChanged
Production version changed.
Every event should contain event ID, event type, event timestamp, producer, schema version, correlation ID and
payload. Financial events should also contain relevant asset, venue and position identifiers. Consumers should be
idempotent where duplicate delivery is possible.
22.1 Ordering
Where order matters, use sequence numbers or causal identifiers. Do not assume that network arrival order
equals market-event order.
22.2 Dead-Letter Handling
Malformed or repeatedly failing events should enter a dead-letter path with alerts. They should not disappear
silently and should not be retried indefinitely without bounded behavior.
22.3 Backpressure
When downstream components cannot keep up, the system should prioritize safety-critical state and preserve
enough information for recovery. It must never solve overload by silently dropping financial events.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 43

---

## Page 44

PART 23 — CONFIGURATION AND FEATURE-FLAG DESIGN
Configuration is part of the trading system. A configuration change can change financial behavior even if no
source code changes. Therefore, configuration must be versioned and audited.
 TRADING_ENABLED
 PAPER_MODE
 CANARY_MODE
 SAFE_MODE
 EMERGENCY_STOP
 STRATEGY_ENABLED:<strategy_id>
 MODEL_ENABLED:<model_id>
 MAX_POSITION_NOTIONAL
 MAX_LEVERAGE
 MAX_DAILY_LOSS
 MAX_DRAWDOWN
 MAX_SLIPPAGE
 MIN_LIQUIDITY
 EVENT_RISK_POLICY
Production should start with TRADING_ENABLED=false. Enabling it should require an explicit deployment action
and should be visible in the audit trail. Risk limits should not be changed by model inference.
23.1 Configuration Precedence
DEFAULT_SAFE
↓
ENVIRONMENT
↓
VERSIONED_CONFIG
↓
EXPLICIT_DEPLOYMENT_OVERRIDE
↓
RUNTIME_STATE
Runtime state must never silently override hard safety limits. Where precedence could produce an unsafe result,
the safer value wins.
PART 24 — PORTFOLIO ACCOUNTING
The system needs a consistent accounting engine independent from strategy predictions. It must calculate
balances, available capital, reserved margin, realized PnL, unrealized PnL, fees, funding, leverage and exposure
from authoritative fills and account data.
24.1 Accounting Rules
 Never infer realized PnL from a strategy prediction.
 Use actual fills for realized execution accounting.
 Track fees separately.
 Track funding/borrow costs separately.
 Track realized and unrealized PnL separately.
 Record mark-price source and timestamp.
 Reconcile internal balances with venue balances.
 Flag unexplained discrepancies.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 44

---

## Page 45

24.2 Exposure Definitions
Define gross exposure, net exposure, notional exposure, risk exposure and liquidity-adjusted exposure explicitly.
The same word should not mean different things in different services.
24.3 PnL Attribution
At minimum, decompose PnL into directional market movement, strategy selection, timing, sizing, execution, fees
and funding where the data permits. This helps prevent a profitable market environment from being mistaken for
model skill.
PART 25 — POSITION SIZING ENGINE
Position sizing is one of the most important bridges between intelligence and survival. The sizing engine should
be deterministic given its inputs and should be independently testable.
25.1 Inputs
 Candidate expected value.
 Uncertainty.
 Volatility.
 Invalidation distance.
 Liquidity.
 Portfolio exposure.
 Correlation.
 Drawdown state.
 Strategy maximum.
 Account capital.
 Execution cost estimate.
25.2 Sizing Process
64. Calculate maximum allowed risk from the risk constitution.
65. Calculate strategy-specific risk budget.
66. Estimate trade-specific risk.
67. Adjust for volatility and liquidity.
68. Adjust for portfolio concentration/correlation.
69. Apply drawdown state.
70. Apply execution constraints.
71. Choose the minimum of all permitted limits.
72. Return approved size with an explanation.
25.3 Anti-Revenge Invariant
The sizing engine must not take prior loss magnitude as a reason to increase risk. Historical losses can affect a
drawdown state or strategy-health state, but the system must never implement an emotional recovery rule such
as 'increase size until the loss is recovered.'
25.4 Anti-Overconfidence Invariant
Recent profit must not automatically increase risk. Any scaling rule must be based on validated allocation policy,
not on a single recent streak.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 45

---

## Page 46

PART 26 — REGIME ENGINE IMPLEMENTATION
The regime engine should begin with interpretable states and later allow more sophisticated probabilistic models.
A practical initial taxonomy can include TREND_UP, TREND_DOWN, RANGE, HIGH_VOLATILITY, LOW_VOLATILITY,
TRANSITION and UNKNOWN. The exact taxonomy should be empirically refined.
26.1 Regime State Object
RegimeState {
timestamp,
probabilities: {state: probability},
dominant_state,
transition_probability,
confidence,
out_of_distribution_score,
model_version
}
26.2 Transition Handling
A transition state is important because strategies often fail during changes between regimes. If transition
probability rises sharply, the strategy allocator should consider reducing strategies that rely on regime stability.
26.3 Regime-Specific Validation
Every strategy should report performance by regime. Aggregate performance can hide the fact that all returns
came from one environment.
PART 27 — HISTORICAL MEMORY IMPLEMENTATION
Historical memory should operate as a structured retrieval system. It is not a simple database of old trades and
not a replacement for statistical modeling.
27.1 Memory Record
MemoryRecord {
timestamp,
asset,
regime,
state_vector,
context_features,
thesis,
action,
outcome,
outcome_path,
strategy_id,
quality_label
}
27.2 Retrieval
73. Construct current query state.
74. Filter by data availability and valid historical period.
75. Filter by compatible asset/market class where appropriate.
76. Calculate similarity.
77. Return top candidates with similarity scores.
78. Apply regime and context weighting.
79. Summarize conditional outcomes.
80. Pass evidence to decision engine.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 46

---

## Page 47

27.3 Memory Safety
The system must never treat retrieval as certainty. A historical analog is evidence, not a forecast guarantee.
Similarity thresholds should be validated, and low-similarity retrieval should be labeled weak evidence.
PART 28 — MODEL TRAINING PIPELINE
Training must be reproducible. A model artifact should be generated from a specific dataset snapshot, feature
version, code version, training configuration and random seed where applicable.
28.1 Training Artifact
TrainingArtifact {
model_id,
version,
dataset_snapshot,
feature_versions[],
code_commit,
hyperparameters,
random_seed,
training_metrics,
validation_metrics,
test_metrics,
calibration_metrics,
robustness_results[],
limitations[]
}
28.2 Training Separation
 Training data cannot overlap protected final test data.
 Feature computation must obey historical availability.
 Hyperparameter selection must not repeatedly inspect the final test set.
 Research experiments must record data windows.
 Retraining must create a new version.
28.3 Calibration
Where a model produces probabilities, store both raw probability and calibrated probability. Monitor calibration
after deployment and by regime where sample sizes allow.
PART 29 — MODEL ENSEMBLE AND DISAGREEMENT
Model disagreement is information. If a trend model predicts continuation while a regime model reports
transition and an execution model reports abnormal liquidity, the system should not collapse those signals into
an unexplained average.
29.1 Ensemble Record
EnsembleOutput {
timestamp,
model_outputs[],
agreement_score,
disagreement_score,
calibrated_combination,
uncertainty,
version
}
29.2 Conflict Policy
 Minor disagreement: proceed if expected value and risk remain acceptable.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 47

---

## Page 48

 Material disagreement: reduce confidence or size.
 Severe disagreement plus abnormal state: abstain.
 Model failure: use only explicitly approved fallback.
 Risk engine retains final authority.
PART 30 — LLM RESEARCH AGENT IMPLEMENTATION
The LLM research agent should operate like a junior-to-senior research assistant under quantitative governance.
It can read documents, summarize evidence, propose hypotheses and organize findings, but it should not be
trusted to invent numerical facts or authorize capital.
30.1 Research Agent Tools
 read_dataset_metadata
 query_historical_states
 retrieve_trade_records
 run_backtest
 run_walk_forward
 run_robustness_test
 compare_strategies
 read_research_reports
 create_hypothesis
 write_research_report
30.2 Restricted Tools
 submit_live_order — not available to LLM research agent.
 modify_risk_limits — not available.
 promote_to_production — requires separate deployment gate.
 enable_trading — not available.
 change_exchange_credentials — not available.
30.3 Agent Output Policy
Every research conclusion should separate observed facts, computed statistics, assumptions and hypotheses. This
prevents a fluent narrative from being mistaken for measured evidence.
PART 31 — RESEARCH REPORT STANDARD
Every serious research result should use a consistent report structure.
81. Question and hypothesis.
82. Mechanism being tested.
83. Data sources and timestamps.
84. Data cleaning.
85. Feature definitions.
86. Baseline.
87. Candidate method.
88. Training period.
89. Validation period.
90. Protected test period.
91. Transaction-cost assumptions.
92. Execution assumptions.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 48

---

## Page 49

93. Metrics.
94. Regime breakdown.
95. Robustness tests.
96. Failure cases.
97. Limitations.
98. Conclusion.
99. Promotion recommendation.
The report should make it possible for another researcher to challenge the result without needing to ask the
original researcher what they meant.
PART 32 — STRATEGY DEVELOPMENT WORKFLOW
Every new strategy should move through the same workflow. This prevents research quality from depending on
which developer or AI agent happened to create it.
100. Write the hypothesis.
101. Define the market mechanism.
102. Define the setup.
103. Define the no-trade conditions.
104. Define entry.
105. Define invalidation.
106. Define position management.
107. Define exit.
108. Define expected holding period.
109. Define required data.
110. Implement transparent baseline.
111. Backtest with realistic costs.
112. Run leakage audit.
113. Run out-of-sample.
114. Run walk-forward.
115. Run robustness matrix.
116. Run paper simulation.
117. Create strategy card.
118. Submit promotion request.
PART 33 — INCIDENT RESPONSE
Financial software needs incident procedures before incidents happen. The system should classify incidents and
define automatic containment.
I1 — Data Integrity
Stop affected strategies; preserve data; diagnose source; replay after correction.
I2 — Unexpected Order
Freeze new orders; reconcile venue; determine cause; preserve logs.
I3 — Position Mismatch
Block new risk; reconcile internal and external positions.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 49

---

## Page 50

I4 — Excess Slippage
Reduce/stop affected execution policy; investigate liquidity and venue.
I5 — Model Failure
Disable affected model; use approved fallback or abstain.
I6 — Risk Service Failure
Fail closed; no new risk until risk authority returns.
I7 — Exchange Outage
Enter safe mode; reconcile after recovery.
I8 — Unexpected Drawdown
Move to configured defensive state; investigate strategy health.
I9 — Security Event
Disable compromised credentials; stop affected trading; preserve evidence.
33.1 Incident Record
Each incident should have incident ID, start/end time, affected services, financial impact, automatic actions,
human actions if any, root cause, corrective action and regression test added.
33.2 Post-Incident Rule
Every incident that reveals a missing guardrail should produce a permanent test or control. The system should
become harder to break as it experiences failures.
PART 34 — PERFORMANCE EVALUATION FRAMEWORK
The system should never be judged by a single return number. Performance evaluation should combine financial,
statistical, operational and behavioral measures.
 Net return after all known costs.
 Maximum drawdown.
 Expected shortfall/tail loss.
 Volatility.
 Sharpe/Sortino/Calmar where appropriate.
 Profit factor.
 Expectancy.
 Win rate and payoff ratio.
 Turnover.
 Fees/funding.
 Slippage.
 Capacity.
 Strategy correlation.
 Regime-specific performance.
 Calibration.
 Abstention quality.
 Rule-violation count.
 Operational incident count.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 50

---

## Page 51

34.1 Daily Return Policy
A daily return target must not be the system's primary optimization objective. The system may report daily return
and may research conditions associated with high-return days, but it must not force trades to achieve a daily
quota. The correct question is whether risk-adjusted expectancy remains positive and robust.
34.2 5% Day Handling
A 5% day should be recorded and investigated like any other unusually large outcome. The system should ask
whether it came from a validated edge, unusual volatility, concentrated risk, leverage, luck or an execution
anomaly. A large gain is evidence to analyze, not permission to increase risk automatically.
PART 35 — AUTONOMOUS LEARNING POLICY
Autonomous learning is allowed only inside a controlled hierarchy. The system may observe continuously,
generate hypotheses continuously and run research continuously. It may not continuously rewrite production
behavior without validation.
35.1 Allowed Autonomous Actions
 Collect and organize data.
 Compute features.
 Detect drift.
 Generate research questions.
 Run approved research experiments.
 Produce candidate strategies.
 Run validation.
 Update non-critical research memory.
 Recommend promotion.
 Reduce risk when predefined safety conditions trigger.
35.2 Restricted Actions
 Change hard risk limits.
 Increase leverage beyond approved configuration.
 Promote an unvalidated model.
 Create unrestricted production credentials.
 Disable safety checks.
 Trade to recover losses.
 Force a daily return target.
 Delete unfavorable research results.
35.3 Self-Modification Rule
Any change that can alter live financial behavior must create a new version, pass the appropriate validation gate,
and be deployed through the production process. The system should never modify its live trading code in place.
PART 36 — OUT-OF-DISTRIBUTION POLICY
One of the most important capabilities of an autonomous trader is recognizing when the present does not
resemble its experience.
36.1 OOD Signals
 Feature distribution distance.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 51

---

## Page 52

 Volatility outside validated range.
 Liquidity outside validated range.
 Regime classifier uncertainty.
 Model disagreement.
 Novel event type.
 Execution behavior outside historical assumptions.
 Historical analog similarity below threshold.
36.2 OOD Response
NORMAL → CAUTION → DEFENSIVE → SAFE_MODE
based on severity and validated thresholds
The exact thresholds must be calibrated through research. The important invariant is that uncertainty must be
allowed to reduce risk rather than forcing the model to produce a confident trade.
PART 37 — STRATEGY PORTFOLIO ALLOCATION
Once multiple validated strategies exist, the allocator should treat them as a portfolio of risk-taking processes.
Allocation should consider expected edge, uncertainty, correlation, regime fit, execution capacity and current
health.
37.1 Allocation Inputs
 Strategy expected value.
 Strategy uncertainty.
 Strategy drawdown.
 Strategy health.
 Strategy correlation.
 Current regime.
 Portfolio exposure.
 Capacity.
 Liquidity.
 Risk budget.
37.2 Allocation Constraints
No allocator output may exceed the risk constitution. The allocator may propose zero allocation. A strategy can
remain active but receive zero capital when conditions are unfavorable.
37.3 Strategy Competition
Strategies should compete for risk budget. This creates a portfolio-level opportunity-cost framework and
prevents every strategy from assuming it deserves capital simultaneously.
PART 38 — BACKTEST / LIVE PARITY
One of the highest-priority engineering requirements is parity between historical replay and live operation. If the
backtester uses different feature calculations, timing rules, cost models or strategy interfaces than production,
the backtest can become irrelevant.
38.1 Shared Components
 Feature definitions.
 Market-state construction.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 52

---

## Page 53

 Strategy interface.
 Decision interface.
 Risk calculations where possible.
 Position accounting.
 Reason codes.
 Trade journal schema.
38.2 Deliberate Differences
Only genuinely environment-specific behavior should differ, such as simulated order fills versus actual exchange
fills. Those differences must be explicit and testable.
38.3 Replay Acceptance
Given the same historical event stream and configuration, the replay engine should reproduce the same
candidate and decision sequence to the extent that stochastic models are controlled. Differences must be
attributable to explicit randomness or environment-specific execution.
PART 39 — RESEARCH-TO-PRODUCTION PROMOTION SYSTEM
Promotion is the bridge between discovery and capital. It should be formal enough that an AI agent cannot
accidentally deploy a promising experiment.
39.1 Promotion Package
 Artifact ID and version.
 Research report.
 Data snapshot.
 Validation results.
 Robustness results.
 Risk analysis.
 Execution analysis.
 Model card/strategy card.
 Known limitations.
 Canary plan.
 Rollback target.
39.2 Promotion States
DRAFT → REVIEW → APPROVED_FOR_PAPER → PAPER_VALIDATED
→ APPROVED_FOR_CANARY → CANARY_VALIDATED
→ APPROVED_FOR_PRODUCTION → PRODUCTION
↘ REJECTED / ARCHIVED
39.3 Promotion Principle
The more capital an artifact can influence, the stronger the evidence required. Research has broad freedom and
zero production authority. Production has narrow freedom and strong controls.
PART 40 — FINAL AGENT EXECUTION PLAN
The coding/building agent should now treat the project as a sequence of deliverables. It should not attempt to
satisfy the entire specification in one uncontrolled implementation pass.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 53

---

## Page 54

Sprint 1
Foundation: repository, environments, configuration, logging, schemas, tests.
Sprint 2
Data: historical ingestion, normalization, quality, storage.
Sprint 3
Replay: deterministic event replay and market-state generation.
Sprint 4
State: feature registry, multi-timeframe state, baseline regime.
Sprint 5
Research: experiment registry and backtest engine.
Sprint 6
Strategy: strategy interface and transparent baseline.
Sprint 7
Decision: candidate, thesis, scenarios, EV, abstention.
Sprint 8
Risk: constitution, sizing, portfolio exposure, drawdown, kill switch.
Sprint 9
Paper execution: order lifecycle, fills, reconciliation.
Sprint 10
Monitoring: dashboards, alerts, audit trail.
Sprint 11
Paper trading: complete end-to-end orchestration.
Sprint 12
Memory: trade memory, analog retrieval, diagnosis.
Sprint 13
AI: model registry and structured inference.
Sprint 14
LLM: research assistant with restricted permissions.
Sprint 15
Promotion: model/strategy gates and canary framework.
Sprint 16
Live canary: only after all required gates pass.
Sprint 17+
Research-driven iteration and measured scaling.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 54

---

## Page 55

40.1 Agent Output Format After Each Sprint
SPRINT REPORT
-------------
Completed:
Tests:
Test results:
Artifacts created:
Schema/config changes:
Permissions added:
Known issues:
Financial-risk implications:
Live trading status:
Next milestone:
The agent should not proceed silently from sprint to sprint. Each report is a checkpoint for reviewing whether the
implementation remains faithful to the architecture.
40.2 What the Agent Must Never Do
 Claim profitability without evidence.
 Claim a backtest is proof of future returns.
 Enable live trading merely because code compiles.
 Remove risk checks to make a test pass.
 Replace missing data with invented values.
 Use future information in a backtest.
 Allow a model to change its own risk limits.
 Let an LLM directly submit live orders.
 Delete failed experiments.
 Optimize for a forced 5% daily target.
 Hide errors because they are inconvenient.
 Continue trading after an unresolved position mismatch.
 Treat a large winning streak as proof that risk should be increased.
PART 41 — MASTER DEFINITION OF THE FINISHED SYSTEM
The finished system is not defined by the number of models, the size of the codebase, or the number of
indicators. It is defined by whether the entire lifecycle works as one governed system.
119. The system observes reliable information.
120. It knows what information was available at each decision time.
121. It constructs a coherent market state.
122. It recognizes regimes and uncertainty.
123. It can retrieve relevant historical experience.
124. It evaluates multiple validated strategies.
125. It forms explicit conditional theses.
126. It generates multiple scenarios.
127. It estimates probabilities and expected value.
128. It can abstain.
129. It sizes positions independently of directional confidence.
130. It passes every trade through an independent risk governor.
131. It executes through a controlled order service.
132. It reconciles actual positions.
133. It manages open positions according to thesis health.
134. It exits when strategy/risk conditions require.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 55

---

## Page 56

135. It records every important decision.
136. It diagnoses outcomes.
137. It monitors model and strategy health.
138. It researches improvements separately from production.
139. It validates improvements before deployment.
140. It can roll back.
141. It can enter safe mode.
142. It can stop.
143. It can recover.
144. It can scale only when evidence supports scaling.
That is the implementation target. Anything less can still be useful, but it should be described honestly as a
partial system rather than full autonomous trading intelligence.
AUTONOMOUS TRADER — TECHNICAL IMPLEMENTATION SPECIFICATION • 56
