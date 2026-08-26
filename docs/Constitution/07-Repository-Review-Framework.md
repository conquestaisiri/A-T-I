# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 07 — REPOSITORY REVIEW FRAMEWORK
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : Reviews of ATI Itself
Applies To      : Code Audit • Architecture Audit • Product Audit • UX Audit • Dependency Audit • Due Diligence
Depends On      : 00-Master-Index, 01-Chief-Architect-Charter, 03-Architecture-Constitution
Version         : 1.0
Classification  : Living Document

=================================================================
PURPOSE
=================================================================

Define how ATI is reviewed.
This is the operating procedure for any full review of the repository.
Use it before major milestones, before large refactors, and when new architects take ownership.
The review exists to find the truth about the repository, not to agree with past decisions.
ARCHITECTURE_REVIEW.md at the repository root is a standing application of this framework.

=================================================================
REVIEW MINDSET
=================================================================

• Stop implementation during the review.
• Do not fix bugs during the review.
• Do not refactor during the review.
• Understand, evaluate, and report only.
• Challenge every assumption.
• Defend every conclusion with evidence.
• Time is not a constraint; depth is.
• Read everything: code, docs, ADRs, tests, config, scripts, CI, roadmap.
• Verify docs against implementation.
• Verify implementation against behavior.
• Verify behavior against architecture.
• The reviewer is the lead architect for the next five years; no rubber-stamping, no protecting past decisions.

=================================================================
REVIEW LAYERS
=================================================================

1. Repository Understanding
• What is the product?
• What has been built?
• What is incomplete?
• What has drifted?
• What belongs?
• What no longer belongs?
• Where is the dead weight?
• What would be built differently from scratch?

2. Architecture Review
For every subsystem
• Purpose, responsibility, ownership.
• Complexity, coupling, cohesion.
• Maintainability, testability, replaceability.
• Does it earn its place?
• Does it keep the core deterministic?
• Is it vendor-agnostic?
• Can risk be bypassed?

3. Code Review
• Dead code.
• Duplicate code.
• Prototype remnants.
• Abandoned experiments.
• Overengineering.
• Underengineering.
• Circular dependencies.
• Leaky abstractions.
• Conflicting responsibilities.
• Naming drift.
• Framework leakage.
• Import breakage (repository must import; suite must run).

4. Product Review
• Walk the product as an operator.
• Where is friction?
• Where is confusion?
• Where does infrastructure leak into the product?
• Would a disciplined trader trust this?

5. UX Review
• Onboarding.
• Context visibility.
• Proposal visibility.
• Risk visibility.
• Approval flows.
• Explanation quality.
• Recovery and interruption.
• Return-to-work experience.

6. Engineering Standards Review
• Quality gates.
• Testing coverage.
• Logging and observability.
• Documentation accuracy.
• Security posture.
• Performance.

7. AI Review
• Is AI used only where reasoning is required?
• Is the core deterministic?
• Is the system model-agnostic?
• Are prompts versioned and reviewed?
• Is context relevant, not maximal?
• Are Decision Proposals validated and risk-gated?
• Is the learning loop sandboxed?

8. Integration Review
• Is every external system replaceable?
• Does every integration justify its existence?
• Is anything being rebuilt that the ecosystem already solves?
• Are health checks truthful?

9. Dependency Review
• Every dependency's cost.
• Every dependency's maintenance burden.
• Every dependency's replaceability.
• Unnecessary dependencies.
• Vendor lock-in.

10. Ecosystem Review
• Compare against prediction-market bots, LLM tools, and data infrastructure researched under research/repositories/.
• What better options exist?
• What should be integrated?
• What should be removed?
• What should never have been built?

=================================================================
REVIEW CHECKLIST: SUBSYSTEM ACCEPTANCE
=================================================================

Every subsystem reviewed against
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
• ✓ Own ADR

=================================================================
REVIEW CHECKLIST: DEBT SEARCH
=================================================================

Search for
• Dead code
• Duplicate code
• Prototype remnants
• Abandoned ideas
• Empty placeholder subsystems
• Empty docs directories
• Skipped tests
• Scaffold placeholders
• Hard-coded venue names
• Hard-coded provider names
• Hard-coded model names
• Naming drift
• Documentation drift
• Configuration drift
• UX drift
• Architecture drift
• Hidden global state
• Circular imports
• Silent failures
• Multiple pipelines for the same responsibility
• Dead config (files nothing loads)

=================================================================
REVIEW CHECKLIST: RISK & SAFETY
=================================================================

Evaluate
• Is risk a decoupled service with veto authority?
• Can the AI bypass any risk gate?
• Can learning alter risk parameters without approval?
• Are circuit breakers deterministic and tested?
• Is the live path independent of free AI tiers?
• Is every decision recorded with evidence?
• Are failures loud and structured?

=================================================================
REVIEW CHECKLIST: QUALITY
=================================================================

Evaluate
• Purpose
• Responsibility
• Complexity
• Coupling
• Cohesion
• Maintainability
• Extensibility
• Testability
• Scalability
• Reliability
• Observability
• Replaceability

For each: strong / acceptable / weak / missing.

=================================================================
REVIEW CHECKLIST: SECURITY
=================================================================

Evaluate
• Secret handling
• Input validation
• Command injection
• Prompt injection
• Path traversal
• External dependency risk
• Log exposure
• State exposure

=================================================================
REVIEW CHECKLIST: SCALABILITY
=================================================================

Evaluate
• Window insert cost
• Queue backpressure
• Context growth
• Repository size growth
• Memory growth
• Provider load
• Cache invalidation
• Long-running work

=================================================================
REVIEW CHECKLIST: FUTURE
=================================================================

Ask
• Can this survive three years?
• Five years?
• Ten years?
• Will new venues require a rewrite?
• Will new models require a rewrite?
• Will new providers require a rewrite?
• Will new capabilities integrate cleanly?
• Where are the future bottlenecks?
• What will maintenance look like in a year?

=================================================================
REVIEW OUTPUT
=================================================================

Required sections
• Executive Summary
• Repository Understanding
• Product Understanding
• Architecture Review
• Codebase Review
• User Experience Review
• Engineering Principles Review
• Product Principles Review
• Dependency Review
• External Ecosystem Review
• Outsourcing Opportunities
• Internal Systems Worth Keeping
• Internal Systems Worth Removing
• Internal Systems Worth Replacing
• Technical Debt
• Architectural Debt
• Product Debt
• UX Debt
• Testing Review
• Security Review
• Performance Review
• Scalability Review
• Reliability Review
• Maintainability Review
• Modularity Review
• AI Architecture Review
• Data Flow Review
• Execution Flow Review
• Learning System Review
• Memory System Review
• Risk Assessment
• Blind Spots
• Missed Opportunities
• Better Open-Source Alternatives
• Three/Five/Ten-Year Vision
• Recommended Architecture
• Prioritized Roadmap
• Final Verdict
• Final Rule

Every claim cites evidence.
Every recommendation names the affected subsystem.
Every finding is classified: keep / remove / replace / simplify.

=================================================================
REVIEW RULES
=================================================================

• No rubber-stamping.
• No protecting past decisions.
• No vague conclusions without evidence.
• No recommendations without impact analysis.
• No report until the reviewer genuinely understands the repository.
• Do not stop early because you think you have "seen enough."
• The report reads like a week-long architecture review, not an AI summary.

=================================================================
DEFINITION OF SUCCESS
=================================================================

The review succeeds when
• the truth about the repository is documented with evidence
• every subsystem is classified and justified
• all dead weight is identified
• all debt is quantified and prioritized
• outsourcing opportunities are concrete
• recommendations are actionable and ordered
• the report makes the repository significantly better by thinking more deeply than anyone has before

# END OF DOCUMENT 07
