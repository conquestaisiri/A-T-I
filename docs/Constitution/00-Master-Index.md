# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 00 — MASTER INDEX
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : Entire Trading-Intelligence Repository
Applies To      : Humans • AI Agents • Contributors • Reviewers • Architects • Maintainers • Future Systems
Version         : 1.0
Classification  : Living Document

=================================================================
PURPOSE
=================================================================

This index is the single entry point to the Trading Intelligence Engineering Constitution.

The mission of this repository is an Autonomous Trading Intelligence (ATI): an AI that observes markets, understands behaviour, reasons about opportunities, plans, executes disciplined trades, learns from outcomes, and improves continuously. The AI is the trader. Rules exist only as safety constraints.

Because the AI itself is a contributor to this repository, the Constitution exists so that every agent, engineer, reviewer, and contributor reads the same non-negotiable invariants before modifying it.

Every AI agent, engineer, reviewer, and contributor MUST read the full constitution before modifying this repository.

Reading order is mandatory.

Skimming is forbidden.

=================================================================
READING ORDER
=================================================================

• Document 00 — Master Index (this file)
• Document 01 — Chief Architect Charter
• Document 02 — Product Constitution
• Document 03 — Architecture Constitution
• Document 04 — Engineering Standards & Code Quality
• Document 05 — AI & Decision Systems Constitution
• Document 06 — Integration Constitution
• Document 07 — Repository Review Framework
• Document 08 — Implementation Strategy
• Document 09 — Long-Term Evolution Strategy
• Document 10 — Chief Architect Operating Manual

Order is not arbitrary.
Each document builds on the previous.
Do not skip documents.
Do not reorder documents.

=================================================================
RELATIONSHIP TO ADRs
=================================================================

• The existing ADRs (0001 Clean Architecture, 0002 FastAPI, 0003 Observation Layer, 0004 SQLite Persistence, 0005 Free-Tier AI Degradation, 0006 AI Entry Point, 0007 Execution and Risk Architecture, 0008 Deterministic Paper Simulator, 0009 Deterministic Rule-Based Reasoner, 0010 Bounded Episodic Memory and LLM Reasoning) are the legal foundations of the technical stack.
• The Engineering Constitution operationalizes the ADRs and the repository's identity documents (Vision, Brain, System_Architecture, Knowledge_Model, Market_Philosophy, Technical_Blueprint).
• ADRs state WHAT the system is.
• The Constitution states HOW to decide, build, review, and evolve it.
• ADRs win on any conflict of definition.
• The Constitution wins on any conflict of method.
• Both are living documents.
• Both must stay in agreement.
• Any amendment to one MUST be reflected in the other.

=================================================================
RELATIONSHIP TO OTHER DOCUMENTS
=================================================================

• AGENTS.md — session operating rules for AI agents and engineers. The Constitution outranks AGENTS.md.
• CLAUDE.md — removed. AGENTS.md is the single session entry point.
• docs/System_Architecture.md — current architectural baseline snapshot.
• docs/Vision.md, docs/Brain.md, docs/Knowledge_Model.md, docs/Market_Philosophy.md, docs/Technical_Blueprint.md — product and cognitive canon.
• docs/adr/*.md — individual ADRs for each accepted subsystem.
• ARCHITECTURE_REVIEW.md — standing principal review; a living application of Document 07.
• experiments/lessons/engineering_playbook.md — extracted, actionable lessons; candidates for promotion into ADRs.

Constitution outranks all of these.
When these contradict the Constitution, the Constitution wins.

=================================================================
CONSTITUTION INVARIANTS
=================================================================

• I-01: The AI is the trader. Deterministic software is the workhorse. Rules are safety constraints, never the strategy.
• I-02: The system must be runnable before it is ambitious. No sprint is "Complete" unless the suite runs and the commit is green.
• I-03: Everything inside the deterministic core is deterministic. Everything nondeterministic is an external capability.
• I-04: Risk is a decoupled service with veto authority over every order. Risk is deterministic and fully tested.
• I-05: The learning loop is sandboxed. It never alters risk parameters or production behaviour without human approval.
• I-06: Nothing is stored, nothing is learned. Observations and decisions are persisted before any learning, memory, or AI reasoning.
• I-07: Every external system — venue, model, provider, storage — is replaceable behind a stable interface.
• I-08: No provider, model, or vendor is ever permanent. Live trading never depends on free AI tiers.
• I-09: No subsystem enters the core without its own ADR.
• I-10: Knowledge is organized; execution is delegated. The AI is consulted; deterministic gates decide and enforce.
• I-11: Every subsystem justifies its existence or is removed. No dead weight, no prototype remnants, no empty placeholders.
• I-12: The user (operator) never touches internal machinery. Every decision is observable and explainable.

=================================================================
DEFINITION OF SUCCESS
=================================================================

The constitution is successful when:
• every new contributor reads it before writing code
• every AI agent reads it before modifying the repository
• engineering decisions trace back to a constitutional principle
• determinism, replaceability, and risk-veto never regress
• the system learns from outcomes, never from conversations
• complexity decreases as the system matures
• the constitution itself improves faster than it stagnates
• the repository is better understood because the constitution exists

=================================================================
HOW TO AMEND THE CONSTITUTION
=================================================================

Amendment Requirements
• Any engineer or AI agent may propose an amendment.
• Every amendment MUST state: What changed, Why it changed, What it affects, What it replaces.
• Every amendment MUST cite evidence (code, tests, backtests, paper-trading results, ecosystem research).
• No amendment may be silent.
• No amendment may weaken: determinism, risk-veto, learning sandbox, replaceability, runnable-before-ambitious.
• Amendments MUST be versioned.
• Amendments MUST be cross-referenced in this index.

Amendment Process
• Propose.
• Defend with evidence.
• Resolve conflicts with the ADRs.
• Update affected documents.
• Update this index.
• Record the change in version history.

=================================================================
VERSION HISTORY
=================================================================

• 1.0 — Initial publication of the Engineering Constitution.

# END OF DOCUMENT 00
