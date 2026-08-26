# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 05 — AI & DECISION SYSTEMS CONSTITUTION
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : All AI-Related Subsystems
Applies To      : Reasoning • Planning • Memory • Skills • Learning • Context • LLMs • Routing • Evaluation • Decision Proposals
Depends On      : 00-Master-Index, 01-Chief-Architect-Charter, 03-Architecture-Constitution
Version         : 1.0
Classification  : Living Document

=================================================================
PURPOSE
=================================================================

Define how AI is used inside ATI.
The AI is the trader — but the AI is also a contributor and a component.
The AI reasons about markets. Deterministic software computes facts, enforces risk, and executes. The AI is consulted for judgment; deterministic gates decide and enforce.

AI is not the whole product.
AI is the reasoning capability within the product.

=================================================================
CORE AI PRINCIPLE
=================================================================

> AI only where reasoning is required.

AI exists to provide
• reasoning
• interpretation
• synthesis
• judgment
• explanation
• planning
• adaptation

AI must NOT perform deterministic work that software can perform reliably.
AI calls are small and rare. The deterministic core does the heavy lifting.

Every AI decision must be questioned
• Is AI actually necessary here?
• Could deterministic software do this better?
• Can the result be verified?
• Can the reasoning be explained?
• Can the operator trust the outcome?
• Does this bypass any risk gate?

=================================================================
AI BOUNDARY
=================================================================

AI MAY
• Interpret market context.
• Generate decision proposals.
• Choose between approaches.
• Summarize context.
• Explain decisions.
• Suggest learning hypotheses.
• Draft reports and lessons.

AI MUST NOT
• Execute orders directly.
• Persist state directly.
• Enforce risk policy.
• Bypass or weaken risk gates.
• Become the source of truth for execution.
• Alter risk parameters without human approval.
• Block the observation path (AI runs out-of-band).

AI is consulted.
Deterministic gates decide and enforce.
Risk has veto authority over everything the AI proposes.

=================================================================
THE DECISION PROPOSAL SCHEMA (ARTIFACT OF RECORD)
=================================================================

The single most important missing artifact in the architecture. It MUST be defined before any LLM integration. Everything downstream (planning, decision, risk, execution, ledger, learning) models around it.

A Decision Proposal is a structured, serializable contract describing one candidate decision. It contains:
• proposal_id, correlation_id, created_at (aware UTC)
• symbol / market
• hypothesis — what the AI believes and why
• evidence — the observations/features that support it
• confidence — calibrated estimate, not a vibe
• uncertainty — explicit acknowledgment of what is unknown
• action set — ordered candidate actions (e.g., enter/exit/size/stand aside)
• risk context — current risk state at time of proposal
• alternatives considered and why rejected
• rationale — human-readable explanation

Properties
• Immutable once created.
• Deterministic gateway: proposals are validated, not trusted.
• Risk service evaluates every proposal; approval is required for any action.
• The AI never emits orders; it emits proposals.

=================================================================
REASONING ARCHITECTURE
=================================================================

Reasoning is
• prompted
• structured
• bounded
• observable
• verifiable

Reasoning inputs
• MarketContext (immutable, serialized)
• Relevant history
• Knowledge store (when it exists)
• Risk constraints
• Prior proposals and outcomes

Reasoning outputs
• Decision Proposals
• Explanations
• Learning hypotheses

Reasoning must be
• reproducible under same inputs (as much as models allow)
• recorded for the operator to review
• bounded in scope and cost
• sandboxed from core determinism
• called out-of-band, never blocking the observation path

=================================================================
MODEL-AGNOSTICISM
=================================================================

• ATI is model-agnostic.
• No architecture depends on a specific model.
• No prompt assumes a specific model's behavior.
• No feature requires a specific model.
• No capability is coupled to a specific provider.
• Model selection is policy and preference driven.
• Model quality is measured, not assumed.
• Model swap must never require a core change.

=================================================================
FREE-TIER POLICY (PRODUCT CONSTRAINT)
=================================================================

The hard constraint is: no money for AI usage — free access only (OmniRoute `localhost:20128/v1` as OpenAI-compatible gateway; free tiers for dev/backtest only).

Constitutional rules:
• Free-tier AI is used ONLY for development, backtesting, and paper trading.
• Live trading NEVER depends on free AI tiers.
• Free-tier endpoints may vanish, throttle, or rate-limit. A degradation policy is REQUIRED: when the reasoning layer is unavailable, the system continues to observe, compute, and enforce risk; it simply does not generate new proposals.
• The reasoning path must degrade loudly, not silently.
• No production behaviour may silently depend on a free endpoint.

=================================================================
MEMORY CONSTITUTION
=================================================================

Memory purpose
• Reduce repetition.
• Improve continuity across sessions.
• Preserve understanding.
• Preserve decisions and their reasons.
• Preserve lessons from outcomes.

Memory scopes
• Market memory — durable market knowledge (structured, with confidence and provenance).
• Episodic memory — trades/decisions/outcomes.
• Reflective memory — lessons.
• Session memory — working context.

Memory policy
• ATI governs what is remembered.
• ATI governs how memory is used.
• ATI does NOT own the storage backend (SQLite first, replaceable behind a MemoryStore contract).

Memory rules
• Never remember secrets.
• Never remember raw prompts by default.
• Always remember decisions and reasons.
• Always remember failures and lessons.
• Prune what is no longer relevant.
• Memory must be explainable to the operator.

Adopted framework: Hermes-style bounded memory, procedural skills, and cross-session SQLite recall — but the CONTENT model is market outcomes, not conversations. ATI learns from noisy, delayed, non-stationary market outcomes.

=================================================================
KNOWLEDGE CONSTITUTION
=================================================================

Knowledge is organized; execution is delegated.

The Knowledge_Model hierarchy (Reality → Observations → Knowledge → Experience → Wisdom) is the conceptual canon. It must be mapped to durable, queryable storage before it is used.

Knowledge rules
• ATI owns the organization of knowledge.
• ATI does not own the storage engine.
• Knowledge is retrieved by relevance.
• Only relevant knowledge enters context.
• Knowledge must be verifiable against data.
• Documentation drift is a knowledge bug.
• Knowledge must improve over time, not accumulate blindly.

=================================================================
CONTEXT CONSTITUTION
=================================================================

Context principles
• Dynamic, not static.
• Relevant, not maximal.
• Assembled per decision.
• Compressed when large.
• Delivered deterministically.

Context rules
• More context is not better.
• Relevant context is better.
• Context assembly is deterministic.
• Context never includes secrets.
• Context never includes provider internals.

=================================================================
ROUTING & SELECTION
=================================================================

Provider gateway
• Prefer OmniRoute when configured.
• Fall back through healthy providers deterministically.
• Retry with bounded count.
• Record usage.
• Fail loudly with structured error.

Routing rules
• Routing is an external capability.
• Routing is replaceable.
• No routing logic in the deterministic core.
• Provider health is checked, not assumed.
• Model choice is a preference, never an architecture constant.

=================================================================
EVALUATION CONSTITUTION
=================================================================

Every AI output should be evaluated
• Does it satisfy the goal?
• Is it correct?
• Is it testable?
• Does it respect risk policy?
• Can it be explained?
• Did it improve the outcome?

Evaluation loop
• Proposal → Execute (or simulate) → Verify → Pass / Retry / Escalate.
• Verification is deterministic where possible.
• Retry is bounded.
• Escalation surfaces to the operator.
• Every verification result is recorded in the ledger.

=================================================================
LEARNING CONSTITUTION
=================================================================

• ATI learns from market outcomes, never from conversations.
• The first learning artifact is the Trade Outcome Ledger (decisions → outcomes → metrics), stored durably.
• Learning produces reports and recalibration proposals FIRST; it never directly rewrites production behaviour.
• Confidence recalibration is proposed, not applied.
• Anything that alters risk parameters requires human approval.
• The learning loop is sandboxed from the deterministic core.

=================================================================
COST CONSTITUTION
=================================================================

• AI usage costs money (or rate limits, on free tiers).
• AI usage adds latency.
• Minimize AI where deterministic software suffices.
• Bound reasoning per proposal.
• Bound retries per proposal.
• Bound context per call.
• Prefer cheap models for cheap work.
• Prefer expensive models only where judgment matters.
• Track cost and usage; surface it.

=================================================================
PROMPT ENGINEERING STANDARDS
=================================================================

Prompts are
• deterministic templates
• versioned
• reviewed
• tested

Prompt rules
• Never embed secrets.
• Never assume model behavior.
• Always state the task.
• Always state constraints.
• Always request structure.
• Always request evidence.
• Always bound output length.
• Always include relevant context only.
• Always specify format for parsing.
• Always request confidence and uncertainty.

=================================================================
AI ANTI-PATTERNS
=================================================================

Reject
• AI everywhere
• AI as default for deterministic work
• Prompt-as-codebase
• Model-specific prompts
• Model-specific features
• Hidden model calls
• Unbounded reasoning
• Unbounded retries
• Prompt injection through user files
• Storing raw prompts as truth
• Leaking AI internals to the operator
• Silently swapping models under the operator
• Letting AI bypass risk gates
• Letting learning touch production behaviour without approval

=================================================================
DEFINITION OF SUCCESS
=================================================================

AI systems succeed when
• AI is used only where reasoning is required
• the deterministic core remains deterministic and model-agnostic
• swapping models and providers requires no core change
• every proposal is validated, risk-gated, and explainable
• memory reduces repetition measurably
• context is relevant, not maximal
• the ledger records every decision and outcome
• learning improves outcomes over time, demonstrated, not claimed
• cost is bounded, tracked, and surfaced
• AI makes ATI smarter without making it fragile

# END OF DOCUMENT 05
