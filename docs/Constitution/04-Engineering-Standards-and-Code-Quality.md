# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 04 — ENGINEERING STANDARDS & CODE QUALITY
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : All ATI Code
Applies To      : Naming • Structure • Testing • Logging • Docs • Performance • Security • Reviews • Quality Gates
Depends On      : 00-Master-Index, 01-Chief-Architect-Charter, 03-Architecture-Constitution
Version         : 1.0
Classification  : Living Document

=================================================================
PURPOSE
=================================================================

Define how ATI code is written, structured, tested, reviewed, and maintained.
Quality is not optional.
Quality is the product.
Every line of code either strengthens the system or becomes debt.
For a trading system, a silent bug is a financial loss. Quality gates exist to make silent failure structurally difficult.

=================================================================
QUALITY GATES
=================================================================

Every change must pass
• ✓ Ruff (lint, import order, complexity)
• ✓ Mypy strict
• ✓ Pytest (all tests, no skips without justification)
• ✓ Architecture boundary tests
• ✓ Contract tests
• ✓ Pre-commit hooks
• ✓ Review against this Constitution

A change is not done until the quality gates pass.
Skipped gates are technical debt.
Skipped tests must carry a reason and a schedule.

The minimum viable gate, in effect now: the repository MUST import and the test suite MUST run. A change that breaks imports or the suite is never acceptable, even temporarily.

=================================================================
CODING STANDARDS
=================================================================

Style
• Python 3.14+ (the suite's verified target environment).
• Type annotations everywhere.
• Strict typing (mypy strict).
• 120 character line limit.
• Prefer dataclasses over classes for data.
• Prefer frozen dataclasses for value objects.
• Prefer Protocols over inheritance for behavior.
• Prefer enums over string literals for fixed sets.
• Prefer immutability.
• No bare except.
• No unused imports.
• No unused variables.
• No dead code.
• No commented-out code.
• No print() in library code.
• No global mutable state.

Naming
• Modules: snake_case.
• Classes: PascalCase.
• Functions/methods: snake_case.
• Constants: UPPER_SNAKE.
• Private helpers: single underscore prefix.
• Prefer explicit names over abbreviations.
• Name by responsibility, not by location.
• Normalize to ONE interface naming convention (no mixing ContextBuilder/FeatureEngine with IExchangeClient/IMarketDataPublisher).

Structure
• One responsibility per module.
• One concept per file.
• Keep modules small enough to be read in one pass.
• Keep functions small enough to be understood in one glance.
• Prefer flat structures over deep nesting.
• No god objects.
• No circular imports.

=================================================================
TYPING RULES
=================================================================

• Every function has a return annotation.
• Every parameter has a type annotation.
• Every public contract has a docstring.
• No `Any` in core.
• No `# type: ignore` without justification.
• NewType for primitive identities (SymbolId, TradeId, CorrelationId).
• Protocols for runtime-checkable contracts.
• No dynamic typing at boundaries.

=================================================================
TESTING STANDARDS
=================================================================

Test Layers
• Unit tests — one subsystem, mocked dependencies.
• Contract tests — verify each subsystem honors its interface.
• Architecture tests — verify dependency boundaries.
• Integration tests — verify real wiring, mocked external systems.
• Determinism tests — same inputs, same outputs (replay).
• Simulation tests — paper-trading, replay-driven.

Coverage Requirements
• Domain core contracts: full.
• Feature engine: full (every feature, failure isolation, health recording).
• Window manager: full (insert, snapshot, determinism).
• Risk service (when built): full — every breaker, every veto path.
• Execution gateway (when built): full (order lifecycle, fill, failure, resume).
• Outcome ledger: full (round-trip, missing file, corrupt data).
• Observation layer: full (adapter normalize, bus, normalizer, backpressure) — currently missing, must be added.
• Config validation: full.
• API/presentation: full once it exists.

Test Rules
• Tests are deterministic.
• Tests never require network.
• Tests never require live providers or live venues.
• Tests mock external systems.
• Tests run in isolation.
• Tests are fast.
• Every bug fix ships with a regression test.
• Every new capability ships with tests.
• No test skips without a reason and a schedule.
• Architecture boundary tests are NEVER skipped.

=================================================================
LOGGING & OBSERVABILITY
=================================================================

Logging Principles
• Every decision is logged.
• Every observation is logged at appropriate verbosity.
• Every state transition is logged.
• Every failure is logged with reason.
• Every risk decision is logged (approved/rejected and why).
• Every provider/venue call is logged with timing.
• Logs are structured.
• Logs carry correlation_id.
• Logs never contain secrets.
• Logs never contain full prompts by default.
• Logs are human-readable at the surface.

Observability
• Every subsystem exposes health.
• Every integration exposes diagnostics.
• Pipeline lag and bus depth are observable.
• Execution is resumable and observable at every step.
• The operator sees status, never raw internals.
• Every important decision is explainable after the fact (audit).

=================================================================
DOCUMENTATION STANDARDS
=================================================================

Every module
• Has a one-line purpose docstring.

Every public contract
• Has a docstring describing behavior.

Every subsystem
• Has an ADR.

Every decision
• Is recorded in ADR form or docs/decisions.

Documentation Rules
• Docs must match code.
• Docs drift is a bug.
• Update docs in the same change as code.
• No placeholder documents.
• No empty docs directories.
• No "future ideas" as empty files.
• Delete duplicates (the repo has 5 identical System_Architecture copies and 3 empty docs).
• Keep CLAUDE.md and AGENTS.md in sync or remove the duplicate.

=================================================================
SECURITY STANDARDS
=================================================================

Never
• Store API keys in code.
• Store API keys in state.
• Store API keys in logs.
• Print secrets.
• Commit secrets.
• Hard-code credentials.
• Trust external input without validation.
• Allow arbitrary shell injection.
• Expose internal state to the operator.
• Expose free-tier AI endpoints on the live path.

Always
• Read secrets from environment or secure store.
• Validate every external input (payload schema validation on every venue stream).
• Scrub secrets from errors.
• Review dependencies for risk.
• Prefer least privilege.
• Keep the live trading path independent of free AI tiers.

=================================================================
PERFORMANCE STANDARDS
=================================================================

• Optimize the hot path only.
• Measure before optimizing.
• Bound queues and loops; no unbounded memory growth.
• Timeout external calls.
• Backpressure is explicit, never accidental.
• Avoid full-file reads in hot paths.
• Cap context by relevance.
• The known O(n log n) window insert is documented; fix with sorted insertion (bisect) when it matters, not before.

=================================================================
CODE REVIEW STANDARDS
=================================================================

Every review must ask
• Does this respect the architecture?
• Does this stay deterministic in core?
• Does this leak internals to the product?
• Does this couple to a venue/provider/model?
• Is this replaceable?
• Is this tested?
• Is this observable?
• Is this documented?
• Is this the simplest correct solution?
• Does this duplicate existing work?
• Does this deserve to exist?
• Would this survive five years?
• Can risk bypass this?

Review outputs
• Approve.
• Request changes with specific evidence.
• Never rubber-stamp.
• Never approve skipped tests.

=================================================================
TECHNICAL DEBT POLICY
=================================================================

• Debt must be recorded.
• Debt must be scheduled.
• Debt must be visible.
• Debt must have an owner.
• Debt must never be silently introduced.
• Prototype remnants must be removed.
• Empty subsystems must be removed or justified.
• Skipped tests must be scheduled or deleted.
• Every hack must have a follow-up issue.
• The two-pipeline split (market_data vs observation) must be resolved to one pipeline, with the loser deleted.

=================================================================
QUALITY BAR
=================================================================

Accept Nothing That Is
• Temporary by accident
• Difficult to understand
• Difficult to maintain
• Difficult to replace
• Difficult to explain
• Difficult to test
• Difficult to evolve
• Coupled to a vendor
• Nondeterministic in core
• Silent in failure

Accept Only Systems That Are
• Intentional
• Cohesive
• Observable
• Predictable
• Composable
• Replaceable
• Maintainable
• Elegant
• Valuable
• Deterministic in core

=================================================================
DEFINITION OF SUCCESS
=================================================================

Engineering standards succeed when
• every change passes all quality gates
• the core remains deterministic and vendor-agnostic
• architecture boundary tests are always green and never skipped
• tests run in seconds, not minutes
• new contributors can read any module and understand it
• code review catches drift before it lands
• documentation matches code
• no dead code, no empty subsystems, no prototype remnants
• technical debt is visible, scheduled, and shrinking
• the repository is easier to maintain each quarter, not harder

# END OF DOCUMENT 04
