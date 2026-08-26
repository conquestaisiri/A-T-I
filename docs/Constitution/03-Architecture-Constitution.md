# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 03 — ARCHITECTURE CONSTITUTION
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : Entire ATI Architecture
Applies To      : Subsystems • Boundaries • Contracts • Layering • Dependencies • Data Flow • Execution Flow
Depends On      : 00-Master-Index, 01-Chief-Architect-Charter, ADR-0001, ADR-0002, ADR-0003
Version         : 1.0
Classification  : Living Document

=================================================================
ARCHITECTURAL AXIOM
=================================================================

> Everything inside the deterministic core is deterministic. Everything nondeterministic is an external capability.

• This rule takes precedence over implementation convenience.
• This rule takes precedence over any subsystem's wishes.
• If an existing ecosystem component already solves a problem well, ATI integrates it through an adapter rather than reimplementing it.
• No subsystem may violate this rule to ship faster.

Secondary axiom, specific to this product:

> The AI is the trader; deterministic software is the workhorse; rules are safety constraints.

The deterministic core computes facts (observations, context, features, risk decisions, execution state). The AI reasons over those facts out-of-band and proposes decisions. The deterministic core gates, enforces, and executes. This is the ATI equivalent of Forge's control-plane model: ATI decides within risk bounds; venues and providers execute.

=================================================================
ARCHITECTURAL BOUNDARY
=================================================================

ATI owns
• Market observation and context assembly
• Feature computation
• Decision proposal handling
• Risk policy and enforcement
• Execution orchestration within risk bounds
• Outcome recording
• Learning policy
• Knowledge organization
• Memory strategy
• Operator experience

ATI does not own
• Venue execution runtimes
• AI routing
• Storage engines
• Workflow automation engines
• Tool implementations
• Model inference
• Vector databases
• Settlement and calendaring
• Exchange SDKs (wrapped, not written)

ATI is the control plane.
External systems are the execution planes.
The boundary between them is a stable interface.

=================================================================
CORE SUBSYSTEMS
=================================================================

The architecture of record (per System_Architecture.md and the review) is:

1. Observation Layer
• Purpose: turn venue streams into normalized, typed ObservationEvents.
• Owns: adapters, normalization, validation, backpressure.
• Does NOT own: strategy, decisions, risk.

2. Context Builder
• Purpose: transform observations into a rolling ContextSnapshot and deterministic MarketContext.
• Owns: windowing, feature computation, context publication.
• Does NOT own: reasoning about what to do.

3. Decision Engine (future)
• Purpose: consume MarketContext and produce Decision Proposals.
• Owns: hypothesis generation, confidence estimation, action selection.
• Does NOT own: execution, risk enforcement. Proposals only.

4. Risk Service
• Purpose: enforce constraints on every decision.
• Owns: circuit breakers, exposure limits, sizing, veto authority.
• Output: RiskDecision (approved / rejected / reduced / requires_confirmation).
• Does NOT own: execution. Is never bypassable by the AI.

5. Execution Service (future)
• Purpose: execute approved decisions on venues via a venue-agnostic IOrderGateway.
• Owns: order lifecycle, fill handling, position tracking.
• Does NOT own: decisions, risk policy.

6. Outcome Ledger
• Purpose: durably record decisions → outcomes → metrics.
• Owns: the record of truth for learning.
• Does NOT own: learning logic.

7. Reflection & Learning
• Purpose: produce reports and recalibration proposals from the ledger.
• Owns: lessons, confidence recalibration, skill updates.
• Does NOT own: production behaviour. Learning is sandboxed; changes require human approval.

8. Knowledge & Memory
• Purpose: organize what ATI knows (semantic/episodic/reflective).
• Owns: knowledge policy, memory strategy.
• Does NOT own: the storage implementation.

=================================================================
SUBSYSTEM ACCEPTANCE TEST
=================================================================

Every subsystem must satisfy
• ✓ Single Responsibility
• ✓ Replaceable
• ✓ Testable
• ✓ Observable
• ✓ Independently Deployable
• ✓ Independently Versionable
• ✓ Independently Documented
• ✓ Measurable Value
• ✓ Clear Owner
• ✓ Clear Exit Strategy
• ✓ Deterministic in Core
• ✓ Own ADR before entering Core

If any check fails, the subsystem must be reconsidered.

=================================================================
LAYERING
=================================================================

Domain Layer (entities, value objects, ports)
• Pure. Imports nothing from application/infrastructure.
• Immutable value objects (frozen dataclasses).
• Ports as abstract interfaces / Protocols.

Application Layer (use cases, orchestration)
• Wires domain to infrastructure.
• Thin. No business logic beyond composition and coordination.
• Holds no global state.

Infrastructure Layer (adapters, persistence, buses)
• Implements ports.
• Knows about external systems.
• Replaceable behind interfaces.

Presentation Layer (API, CLI, dashboard)
• Exposes product experience.
• Never contains decision logic.
• Never leaks internal machinery.

Services Layer (workers, long-running processes)
• Long-lived processes (e.g., market data worker).
• Thin composition of application services.

The dependency direction always points inward.
No circular dependencies.
Architecture boundary tests enforce these rules and MUST NOT be skipped.

=================================================================
DEPENDENCY RULES
=================================================================

• Domain MUST NOT import application, infrastructure, or presentation.
• Application MUST NOT import infrastructure implementation details (only ports).
• Infrastructure MAY import application ports.
• Presentation MAY import application, never domain internals.
• Adapters MUST NOT be imported by the core.
• No circular dependencies.
• Boundary tests MUST NOT be skipped.

=================================================================
CONTRACTS
=================================================================

Contract Style
• Protocols for behavior.
• Frozen dataclasses for value objects.
• Typed enums for fixed sets.
• Pydantic models for serializable boundaries.
• No inheritance chains deeper than one level.
• No hidden mutation.

Core Value Objects
• ObservationEvent — immutable, timezone-aware (UTC), typed.
• ContextSnapshot — immutable window state.
• MarketContext — immutable, deterministic, serializable (as_dict).
• Decision Proposal — schema of record for AI output (Document 05).
• RiskDecision — outcome of risk evaluation (approved/rejected/reduced/confirm).
• OrderRequest / OrderStatus / Position / ExecutionReport — execution domain.
• TradeRecord — durable outcome record.

=================================================================
DATA FLOW
=================================================================

Observe
• Adapter → normalized ObservationEvent (aware UTC) → persist (at-least-once) → ContextBuilder.

Understand
• ContextBuilder: WindowManager → FeatureEngine → MarketContext → persist → publish.

Reason (out-of-band)
• AI consumes MarketContext → Decision Proposal (evidence + confidence + uncertainty + action set).

Decide & Enforce
• Proposal → Risk Service (veto authority, deterministic) → approved/rejected.

Execute
• Approved order → Execution Service (IOrderGateway) → fills → position → TradeRecord.

Learn
• TradeRecord → Reflection → reports + recalibration proposals → human approval → knowledge/memory update.

Every step in this flow must be observable.
Every hop must be logged.
Every hop must be explainable.
Every hop must be resumable.

=================================================================
DETERMINISM RULES
=================================================================

• Same inputs MUST produce same results in the deterministic core.
• No random choice in core.
• No model calls in core.
• No clock-dependent decisions in core (timestamps derive from data, not wall clock, where replay matters).
• No network calls in core.
• No hidden global state in core.
• All I/O happens at the edges.
• Deterministic portions are tested with identical-input tests.
• Nondeterministic portions are isolated behind adapters and mocked in tests.
• The AI is never called from the deterministic core; the core only serializes context for out-of-band reasoning.

=================================================================
REPLACEABILITY RULES
=================================================================

• Every external system sits behind a stable interface.
• Swapping a venue MUST NOT touch the core.
• Swapping a model or provider MUST NOT touch the core.
• Swapping storage MUST NOT touch the core.
• Adapter replacement is a drop-in change.
• No capability may be coupled to a specific vendor.
• "Venue-agnostic" is an implementation property (one adapter per venue behind one port), never a README claim.

=================================================================
EVENTS
=================================================================

• State transitions emit DomainEvents.
• Every event carries source, name, and correlation_id.
• Events are immutable.
• Events are the audit trail.
• Events are NOT the primary persistence mechanism.
• Logging and telemetry consume events.
• The operator should never see raw events.

=================================================================
STATE MANAGEMENT
=================================================================

• State is deterministic.
• State is serializable.
• State is resumable.
• State transitions are event-driven.
• State NEVER stores secrets.
• State NEVER stores model outputs as truth.
• State NEVER leaks into core contracts.

State must answer at all times:
• What is the current market context?
• What is the current proposal?
• What is the current risk status?
• What is the current position?
• What is the operator-visible status?

=================================================================
PERSISTENCE
=================================================================

• SQLite is the V1 persistence choice: single process, zero ops, file-backed, replayable.
• A repository port lives in application; the sqlite3 implementation lives in infrastructure.
• Storage choice must remain replaceable behind the repository contract (SQLite → Postgres/ClickHouse later as scale demands).
• At-least-once ingestion to a durable store is the reliability foundation.
• No learning, memory, or AI reasoning until observations and decisions are durably persisted.

=================================================================
RELIABILITY PRINCIPLES
=================================================================

• Resumability is mandatory.
• Checkpoint after every step.
• Fail loudly with a structured report.
• Never silently continue.
• Never silently stop.
• Never lose state.
• Provider failures fall back deterministically.
• Capability failures produce a structured result, never a crash.
• Recovery is always explainable.
• Backpressure is explicit: bounded queues with a declared drop/block policy; no unbounded memory growth; no silent oldest-drop by default.

=================================================================
SCALABILITY PRINCIPLES
=================================================================

• Scale by composition, not by monolith growth.
• Scale by replacing capability backends, not by rewriting the core.
• Context scales by relevance, not by accumulation.
• Memory scales by policy, not by hoarding.
• Execution scales by delegation, not by internal parallelism.
• Observability scales by event streaming, not by log dumping.
• V1 scale (single symbol, in-process) is correct; known limits (O(n log n) window insert, in-memory bus) are documented, not silently ignored.

=================================================================
ARCHITECTURE ANTI-PATTERNS
=================================================================

Reject
• God classes
• Hidden global state
• Implicit dependencies
• Circular imports
• Deep inheritance
• Leaky abstractions
• Duplicated abstractions (three buses, two event models)
• Unnecessary wrappers
• Speculative generality
• Architecture astronautics
• Vendor-locked contracts
• Prototype remnants
• Empty placeholder subsystems
• Skipped boundary tests
• Multiple divergent pipelines for the same responsibility

=================================================================
PROTOTYPE REMNANT RULE
=================================================================

• Any directory, module, or capability that is an abandoned experiment MUST be removed or justified.
• Empty packages (only `__init__.py`) are dead weight.
• Empty docs directories are dead weight.
• Scaffold placeholders are dead weight unless scheduled.
• Architecture boundary tests that are skipped MUST be enabled or removed.
• The repository must never contain "future ideas as empty folders."
• The repository must never contain two pipelines for the same job.

=================================================================
ARCHITECTURE REVIEW QUESTIONS
=================================================================

For every subsystem
• What is its purpose?
• What is its responsibility?
• Who owns it?
• How complex is it?
• How coupled is it?
• How cohesive is it?
• How maintainable is it?
• How testable is it?
• How scalable is it?
• How observable is it?
• How replaceable is it?
• Does it deserve to exist?
• Is it deterministic in core?
• Is it vendor-agnostic?

=================================================================
DEFINITION OF SUCCESS
=================================================================

The architecture succeeds when
• the deterministic core stays deterministic and vendor-agnostic
• the AI reasons out-of-band and never bypasses risk
• every external system is replaceable behind a stable interface
• adding a venue, model, or provider requires no core change
• new subsystems require an ADR and pass the acceptance test
• dependency rules are enforced by automated tests
• the repository contains no dead weight
• the product surface never leaks architecture
• the system can survive three, five, and ten years without a rewrite

# END OF DOCUMENT 03
