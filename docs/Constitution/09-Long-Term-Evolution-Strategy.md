# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 09 — LONG-TERM EVOLUTION STRATEGY
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : Future of ATI
Applies To      : 3-Year Vision • 5-Year Vision • 10-Year Vision • Venue Futures • Model Futures • Provider Futures • Memory Futures
Depends On      : 00-Master-Index, 01-Chief-Architect-Charter, 03-Architecture-Constitution
Version         : 1.0
Classification  : Living Document

=================================================================
PURPOSE
=================================================================

Define how ATI evolves without rewriting itself.
The architecture must survive new venues, new models, new providers, and new market regimes.
Evolution is continuous improvement, never revolution.

=================================================================
EVOLUTION PRINCIPLES
=================================================================

• Evolve by composition, not replacement of core.
• Evolve by adding replaceable capabilities.
• Evolve by improving the decision loop, not by growing complexity.
• Evolve by adopting ecosystem advances.
• Evolve by deleting what no longer earns its place.
• Every new venue, model, and provider must slot in without core changes.
• Every new capability must be replaceable.
• The product surface must stay clean while capability grows.

=================================================================
3-YEAR VISION
=================================================================

ATI should have
• a single, wired observation→context pipeline
• SQLite persistence with deterministic replay
• a Decision Proposal schema and a reasoning service
• a risk service with veto authority, fully tested
• a paper-trading simulator and an outcome ledger
• a reflection job producing reports and recalibration proposals
• an observability API and structured logging
• venue-agnostic adapters for at least one read venue

3-Year Principles
• Core remains deterministic.
• Every external system remains replaceable.
• No venue, provider, or model is permanent.
• Complexity decreases as capability increases.

=================================================================
5-YEAR VISION
=================================================================

ATI should have
• risk-gated paper execution with confidence calibration
• a learning loop that demonstrates improvement from outcomes
• multiple venues behind one port (no core change to add one)
• long-term knowledge and memory under policy
• a demonstrable track record of decisions → outcomes → lessons
• live trading only if paper performance justified it, always behind risk gates, never on free AI tiers

5-Year Principles
• ATI's decisions are auditable years later.
• ATI remembers outcomes better than any single human.
• ATI composes the world's best systems transparently.
• ATI adapts to new AI without rewrites.
• ATI is trusted with risk-gated autonomous work.

=================================================================
10-YEAR VISION
=================================================================

ATI should become
• a persistent trading intelligence with a decade-long outcome ledger
• a system whose learning is a continuous, human-gated improvement process
• an explainable, auditable partner in capital allocation
• a demonstration that disciplined, outcome-driven learning compounds

10-Year Principles
• Architecture survives venue churn.
• Architecture survives model generation changes.
• Architecture survives provider churn.
• Architecture survives ecosystem disruption.
• The product experience remains calm and disciplined.
• ATI feels like one intelligent partner, not twenty systems.

=================================================================
VENUE FUTURES
=================================================================

When venues appear
• Add an adapter behind the existing port.
• No core change.
• Paper-trade the new venue before any live capital.

When venues change APIs
• Adapter update only.
• No core change.
• Health checks catch drift before it matters.

When venues disappear
• ATI loses nothing.
• Adapters are replaceable.
• Venue-agnosticism means the decision loop never imports a venue.

=================================================================
MODEL FUTURES
=================================================================

When models improve
• ATI must only improve.
• Better reasoning improves proposal quality.
• No architectural change required.

When context windows become effectively infinite
• Context assembly still applies by relevance.
• Infinite context is not a reason to be sloppy.
• Relevance beats volume forever.

When local models become dominant
• Provider selection adapts by policy.
• Local-first becomes a preference, not a rewrite.
• Routing is external; swap the gateway config.

When agents become autonomous
• ATI stays the control plane.
• Human approval stays where risk demands.
• Determinism stays in core.
• Autonomy is a policy decision, not an architecture constant.

=================================================================
PROVIDER FUTURES
=================================================================

When providers disappear
• ATI loses nothing.
• Routing is external.
• No core coupling.
• Fallback list is configuration.
• Free-tier loss degrades to no-proposals mode loudly, never silently.

When new providers appear
• Add a provider entry.
• Add an adapter or use the gateway.
• No core change.

When pricing changes
• Selection factors adapt by policy.
• Cost is tracked and surfaced.
• No core change.

=================================================================
MEMORY & KNOWLEDGE FUTURES
=================================================================

When memory backends mature
• Swap the MemoryStore implementation.
• No core change.

When procedural learning matures
• Adopt skill formats behind ATI skill adapters.
• Adopt learning ideas from Hermes and peers.
• Keep memory governed by policy.
• ATI's memory is about market outcomes, not conversations.

When organizational knowledge grows
• Knowledge is organized, not hoarded.
• Retrieval is by relevance.
• Knowledge is verifiable against data.

=================================================================
DATA & SCALE FUTURES
=================================================================

When tick volume grows
• Move OLAP to ClickHouse or DuckDB.
• Repository port unchanged.
• No core change.

When multi-process is required
• Move pub/sub to Redis or NATS.
• ObservationBus/EventBus roles documented, implementations swapped.
• No core change.

When backtesting needs grow
• Replay from the durable store; determinism makes it possible.
• No "re-optimize until it fits" culture. Ever.

=================================================================
ECOSYSTEM FUTURES
=================================================================

Continuous research targets
• New prediction-market venues and SDKs.
• New agent frameworks.
• New memory systems.
• New routing solutions.
• New standard protocols.
• New model architectures.
• New open-source trading/analysis tools.

Rule
• When a better system exists, adopt it.
• When a better standard exists, adopt it.
• When ATI can stop owning something, stop owning it.
• Never inherit debt from the ecosystem blindly.

=================================================================
EVOLUTION ANTI-PATTERNS
=================================================================

Reject
• Rewrites driven by novelty.
• Coupling to model generations.
• Coupling to provider churn.
• Accumulating context without relevance.
• Hoarding memory without policy.
• Growing complexity to look sophisticated.
• Preserving subsystems out of sentiment.
• Letting prototype code harden into production.
• Waiting for "the right time" to remove debt.
• Over-optimizing backtests to fit history.

=================================================================
EVOLUTION REVIEW QUESTIONS
=================================================================

At every major milestone
• Can new venues be adopted without rewrites?
• Can new models be adopted without rewrites?
• Can new providers be adopted without rewrites?
• Can new tools integrate cleanly?
• Is maintenance getting easier or harder?
• Is complexity shrinking or growing?
• Is the product surface staying clean?
• Is every system still replaceable?
• Is the core still deterministic?
• Is risk still in charge?
• Is ATI easier to understand than a year ago?
• Would we still choose this architecture today?

=================================================================
DEFINITION OF SUCCESS
=================================================================

Long-term evolution succeeds when
• ATI survives three, five, and ten years without a rewrite
• new venues, models, and providers are configuration changes, not architecture changes
• new capabilities integrate cleanly behind stable interfaces
• memory and knowledge improve continuously under policy
• the product experience stays calm and disciplined
• maintenance becomes easier each year, not harder
• the decision loop demonstrably improves from outcomes over time
• the architecture is easier to understand over time, not more complex

# END OF DOCUMENT 09
