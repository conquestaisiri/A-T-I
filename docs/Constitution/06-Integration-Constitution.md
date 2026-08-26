# ============================================================
# TRADING INTELLIGENCE ENGINEERING CONSTITUTION
# DOCUMENT 06 — INTEGRATION CONSTITUTION
# ============================================================

Status          : Constitutional
Priority        : Absolute
Authority       : Highest
Scope           : All External Systems
Applies To      : Venues • Exchanges • LLMs • Provider Gateways • Storage • Buses • Research Repos • All External Capabilities
Depends On      : 00-Master-Index, 01-Chief-Architect-Charter, 03-Architecture-Constitution
Version         : 1.0
Classification  : Living Document

=================================================================
PURPOSE
=================================================================

Define how ATI relates to every external system.
ATI does not compete with mature infrastructure.
ATI composes mature infrastructure.
ATI never rebuilds what the ecosystem already does exceptionally well.

=================================================================
DECISION MATRIX: BUILD / WRAP / INTEGRATE / FORK / DELEGATE / IGNORE
=================================================================

IF
Existing mature OSS solves problem completely AND fits ATI's boundaries
THEN
INTEGRATE via adapter. Do not build. Do not wrap unnecessarily.

ELSE IF
Existing OSS solves ~80% AND is replaceable AND fits boundaries
THEN
WRAP behind a stable interface. Add only the missing 20%.

ELSE IF
Problem is core to ATI identity AND no ecosystem answer exists
THEN
BUILD. Require its own ADR. Justify with evidence.

ELSE IF
OSS is promising but immature or diverges from boundaries
THEN
FORK only the ideas. Adopt concepts behind ATI abstractions.

ELSE IF
Mature system handles an entire problem domain we should not own
THEN
DELEGATE entirely. Become a thin client.

ELSE
IGNORE. Do not own what does not serve ATI.

=================================================================
OUTSOURCING PHILOSOPHY
=================================================================

ATI Should Never Own
• Venue connectivity → venue SDKs (wrapped behind a port).
• AI model inference → providers.
• AI provider routing → OmniRoute / gateway.
• Settlement and calendaring → venue data feeds + thin reconcile.
• OLAP / tick storage → ClickHouse, DuckDB, or similar.
• Multi-process pub/sub → Redis, NATS (beyond V1 in-process).
• LLM orchestration → thin OpenAI-compatible client; NOT LangChain-style frameworks.
• Storage engines → SQLite (V1), then Postgres/ClickHouse.
• Vector stores → external capability when needed.

Never build infrastructure for the sake of building infrastructure.

=================================================================
REFERENCE SYSTEMS
=================================================================

The following are references for world-class engineering. Not mandates.

Venue Layer
• Binance — V1 venue; wrap its SDK/WS behind the ObservationAdapter port.
• Polymarket — prediction-market venue; the research repos (polybot, polymarket_lp_tool, Prediction-Markets-Trading-Bot-Toolkits) demonstrate settlement, order-replace, and risk patterns. Adopt ideas, not code.
• Kalshi — future venue; the Rust toolkits prove that "venue-agnostic" is easy to claim and hard to ship. ATI must have one adapter per venue behind one port before claiming it.

Provider Layer
• OmniRoute (`localhost:20128/v1`) — free-tier AI gateway for dev/backtest. Replaceable. Live trading never depends on it.
• Free model aggregation — reference for model access economics.

Data & Storage Layer
• SQLite — V1 persistence. File-backed, zero ops, replayable.
• ClickHouse — later OLAP for ticks; right call at scale.
• DuckDB — local backtest analytics, no server.
• Redis / NATS — later multi-process pub/sub; not now.

Learning & Memory Layer
• Hermes — procedural skill creation, bounded memory, background review loop. Adopt the FRAMEWORK (bounded memory files, write-approval gates, cross-session SQLite recall), not the conversation content model.
• Honcho / Letta — references for stateful memory, if boundaries stay clean.

Research Layer
• research/repositories/* — reference clones. Extract lessons into experiments/lessons/; keep clones OUT of version control.

=================================================================
VENUE-AGNOSTICISM RULE
=================================================================

• "Venue-agnostic" is an implementation property, never a README claim.
• One adapter per venue, all behind one port (ObservationAdapter for reads; IOrderGateway for writes).
• Adding a venue MUST NOT touch the core.
• The research evidence is explicit: mature projects ship the claim without the code. ATI must not repeat that.

=================================================================
ADAPTER RULES
=================================================================

• Every external system sits behind a stable interface.
• Adapters are replaceable drop-in implementations.
• Adapters never import core internals.
• Adapters never import other adapters.
• Adapters never share state.
• Adapters report availability honestly.
• Adapter failures return a structured result, never a crash.
• Adapters log their selection and result.
• Adapters carry timing metadata.
• Adapter names are visible for diagnosis only, never as product vocabulary.
• Venue streams are schema-validated at the adapter boundary.

=================================================================
INTEGRATION RECORD REQUIREMENTS
=================================================================

Every integration must carry
• Component name.
• Purpose.
• Category.
• Version.
• Source.
• License.
• Status (planned / available / degraded / unavailable / external).
• Priority.
• Enabled / required flags.
• Entrypoint.
• Dependencies.
• Capabilities exposed.
• Configuration.
• Tests (smoke tests).
• Health.
• Upgrade path.
• Reason selected.

These fields answer:
• What does it solve?
• Does it solve it better than ATI should?
• Is it mature?
• Is it maintainable?
• Is it replaceable?
• Can it disappear behind ATI abstractions?
• Does it improve the product?
• Does it reduce maintenance?
• Does it reduce engineering effort?
• Does it increase long-term quality?

=================================================================
MANAGED INTEGRATION LIFECYCLE
=================================================================

States
• Planned → Available → Degraded → Unavailable.
• External (reference only, not installed).

Operations
• Install
• Configure
• Detect
• Health check
• Diagnostics
• Capability listing
• Update
• Restart
• Shutdown
• Uninstall

Rules
• Health checks are deterministic and local.
• Diagnostics are human-readable.
• Status is synced to the registry.
• Every operation returns a structured result.

=================================================================
OMNIROUTE CONSTITUTION
=================================================================

• OmniRoute is the preferred provider gateway when configured.
• OmniRoute is replaceable.
• ATI does not depend on OmniRoute at the architecture level.
• ATI consumes OmniRoute through a ProviderGateway port.
• If OmniRoute is unavailable, ATI falls back through configured providers deterministically — or degrades loudly to no-proposals mode on the live path.
• OmniRoute configuration is environment-driven, never hard-coded.
• OmniRoute is never required for the core to load.

=================================================================
REPLACEABILITY CONSTITUTION
=================================================================

• Swapping a venue → swap one adapter.
• Swapping a provider → touch only gateway config.
• Swapping a model → touch only provider/preference config.
• Swapping storage → swap one repository implementation.
• Swapping an execution backend → swap one adapter.
• No swap may touch the core.
• Every integration has an upgrade path.
• Every integration has a reason for existing.

=================================================================
INTEGRATION ANTI-PATTERNS
=================================================================

Reject
• Vendor lock-in
• Importing external systems into the core
• Duplicating mature infrastructure
• Building wrappers that add no value
• Abstracting abstractions
• Integrating to inflate the feature list
• Integrating systems that weaken product identity
• Keeping dead integrations in the registry
• Silent health failures
• Unbounded dependency chains
• Claiming venue-agnosticism without adapters
• Vendoring research clones into version control

=================================================================
DEFINITION OF SUCCESS
=================================================================

Integration strategy succeeds when
• every external system is replaceable behind a stable interface
• no core change is needed to swap a venue, model, provider, or tool
• ATI composes the world's best systems instead of rebuilding them
• integrations disappear behind one cohesive product
• health and diagnostics are observable
• the registry is truthful about what exists and why
• the dependency chain never becomes irreversible
• ATI's unique value is the decision loop and risk discipline, not reimplementation

# END OF DOCUMENT 06
