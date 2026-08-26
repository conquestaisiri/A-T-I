# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 08 — IMPLEMENTATION STRATEGY
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : How ATI Is Built and Evolved
Applies To      : Milestones • Priorities • Delivery Order • Refactoring • Drift Prevention • Release Discipline
Depends On      : 00-Master-Index, 01-Chief-Architect-Charter, 03-Architecture-Constitution, 04-Engineering-Standards
Version         : 1.0
Classification  : Living Document

=================================================================
PURPOSE
=================================================================

Define how ATI is implemented, in what order, and under what discipline.
Implementation is judged by decision quality and safety, not by internal architecture count.
Milestones are judged by observable capability, not by lines of code.

=================================================================
CORE RULE
=================================================================

• Never review and build at the same time.
• Understand first. Then plan. Then build.
• No implementation until the review is done.
• No feature until the architecture justifies it.
• No subsystem until its ADR exists.

=================================================================
CURRENT STATE (BASELINE, VERIFIED)
=================================================================

The standing review (ARCHITECTURE_REVIEW.md) establishes the true baseline:
• The repository does not import (3 confirmed bugs).
• The test suite cannot run.
• Two competing, disconnected ingestion pipelines exist.
• No persistence, no git history, no CI.
• The observation→context join is unimplemented.
• The Decision Proposal schema does not exist.

Phase 0 exists specifically to fix this baseline. Do not skip it.

=================================================================
IMPLEMENTATION ORDER
=================================================================

Phase 0 — Foundation Verification
• Verify the Constitution is read.
• Fix the import gate (missing exception classes + 2 relative-import fixes).
• Make the test suite green; fix fallout.
• Add observation-layer tests.
• Remove prototype remnants and empty placeholders.
• Delete the legacy market_data pipeline; unify to one pipeline.
• Delete duplicate/empty docs; dedupe AGENTS/CLAUDE.
• Make the first git commit; adopt commit discipline.
• Add ruff + mypy config; make them CI-clean.
• Establish CI: tests as merge gate.
• Document the interpreter environment (py -3; python-on-PATH shadowing).

Phase 1 — Deterministic Core & Persistence
• SQLite layer: repository port in application, sqlite3 impl in infrastructure.
• Persist ObservationEvent and MarketContext (at-least-once).
• Wire ObservationBus → ContextBuilder as an async service task.
• Fix timezone awareness (aware UTC) and bounded-queue/backpressure policy.
• Observability API: /context/latest, /context/history, structured event log.

Phase 2 — Decision Schema & Simulation
• Define the Decision Proposal schema (Document 05) — domain model + serialization.
• Define execution/risk interfaces + ADRs (IOrderGateway, RiskGate, OrderRequest, Position, ExecutionReport). No implementation.
• Paper-trading simulator (deterministic, replay-driven) consuming proposals and producing outcomes into the ledger.

Phase 3 — Cognitive Core
• Reasoning service: consumes MarketContext, produces Decision Proposals — deterministic/rule-based first, LLM hooks later via OmniRoute (free tier, dev/backtest only).
• Risk service per playbook (circuit breakers, sizing) with veto authority — deterministic, fully tested.
• Reflection job: proposals vs outcomes; reports + confidence-recalibration proposals.

Phase 4 — Learning & Graduation
• Trade outcome ledger analytics; learning loop with write-approval gates.
• Only after extended paper trading: risk-gated live execution on a real venue, with free-tier AI strictly excluded from the live path.

=================================================================
MILESTONE SHAPE
=================================================================

Every milestone is judged by product value
• better market understanding
• better decision proposals
• better risk discipline
• better explanation quality
• better outcome recording
• better learning

A milestone is not done when code lands.
A milestone is done when decision quality or operator safety measurably improves.

=================================================================
PRIORITY ORDER
=================================================================

1. Remove dead weight before adding anything.
2. Make the repository importable and tested before adding anything.
3. Deterministic core before AI features.
4. Persistence before learning.
5. Decision schema before AI.
6. Correctness before performance.
7. Reliability before scale.
8. Risk gating before execution.
9. Paper trading before live trading.

=================================================================
INCREMENTAL DELIVERY
=================================================================

• Ship small, coherent changes.
• Each change passes all quality gates.
• Each change is independently reviewable.
• Each change is independently testable.
• Each change is independently reversible.
• No mega-PRs.
• No silent mega-refactors.
• Refactors are separate from features.

=================================================================
REFACTORING PHILOSOPHY
=================================================================

• Refactor only when the architecture improves.
• Refactor only when the product improves.
• Refactor only when the long-term cost decreases.
• Never refactor for novelty.
• Never refactor to match a personal style.
• Refactor toward the Constitution, never away from it.
• Refactor in small, verifiable steps.
• Refactor with tests running after every step.
• Refactor removes duplication and dead weight.
• Refactor does not change behavior.

=================================================================
ARCHITECTURE EVOLUTION
=================================================================

• Architecture evolves through ADRs.
• No significant change without an ADR.
• ADRs record context, decision, and consequences.
• ADRs are living documents.
• ADR statuses: Proposed → Accepted → Deprecated → Superseded.
• A superseded ADR names its replacement.
• Old architecture is retired explicitly, never silently.

Required ADRs (from the review)
• SQLite-first persistence (resolves the "Redis now" note in recommended_integrations.md).
• Free-tier AI degradation policy.
• AI entry point (Decision Proposal stage; AI reasons out-of-band).
• Decision Proposal schema.
• Execution/risk domain interfaces.
• Learning sandbox rule.

=================================================================
AVOIDING ARCHITECTURE DRIFT
=================================================================

• Enforce dependency boundaries with tests.
• Enforce contract conformance with tests.
• Enforce naming with lint rules.
• Enforce docs accuracy with reviews.
• Review the Constitution before every major change.
• Record deviations immediately.
• Never let temporary work become permanent.
• Never let experimental code become default.
• Never skip a boundary test to ship faster.

=================================================================
DEBT DISCIPLINE
=================================================================

• Every piece of debt is recorded.
• Every piece of debt is scheduled.
• Every piece of debt has an owner.
• Debt is paid down before new features in the same area.
• New debt is introduced only with a plan.
• Skipped tests are re-enabled on schedule.
• Prototype remnants are removed on sight.
• The two-pipeline split is resolved before any new pipeline work.

=================================================================
RELEASE DISCIPLINE
=================================================================

• Releases are reproducible.
• Releases pass all gates.
• Releases carry changelogs.
• Releases preserve determinism.
• Releases preserve replaceability.
• Releases never require operator migration without notice.
• Config changes are additive by default.
• Secrets never enter the repository.
• A release that touches the live path requires a written risk review.

=================================================================
VERIFICATION BEFORE SHIPPING
=================================================================

Every change verified by
• Unit tests.
• Contract tests.
• Architecture tests.
• Integration tests (mocked externals).
• Determinism tests (same inputs, same outputs).
• Lint.
• Type check.
• Manual operator walkthrough where UX is affected.

=================================================================
DEFINITION OF SUCCESS
=================================================================

Implementation strategy succeeds when
• milestones are judged by decision quality, not architecture count
• the repository imports and the suite runs at all times
• deterministic core ships before AI features
• persistence ships before learning
• the decision schema ships before AI
• each change is small, tested, and reversible
• drift is caught by automated tests
• debt is visible and shrinking
• architecture evolves through ADRs
• risk gating precedes execution
• paper trading precedes live trading
• ATI gets safer and more explainable with every release

# END OF DOCUMENT 08
