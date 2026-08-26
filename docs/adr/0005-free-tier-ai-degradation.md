# 0005: Free-Tier AI Constraint and Degradation Policy

## Decision
The AI reasoning layer in V1 depends exclusively on free-tier access (OmniRoute gateway at `localhost:20128/v1`, dev/backtest only). **Live trading never depends on free AI tiers.** Because the free endpoint is a product constraint — not a budget detail — the system defines an explicit degradation policy for when the endpoint vanishes, throttles, or rate-limits.

Degradation policy:
1. **Observation and context production continue.** The deterministic pipeline (adapter → normalize → persist → context → persist) is fully independent of AI and never pauses because the AI endpoint is down.
2. **The AI step is out-of-band.** AI consumes the persisted `MarketContext` asynchronously; a missing/throttled endpoint delays reasoning but never blocks ingestion or persistence.
3. **No silent degradation.** Every AI-request failure is logged with reason and duration. An `ai_unavailable` condition is recorded so operators and future dashboards can see degraded reasoning, not guess at it.
4. **No fallback-to-live.** If the free tier fails mid-campaign, the system stays in dev/backtest and halts execution-side automation; it never silently substitutes a paid endpoint or a degraded model without human approval.

## Why
The review found that "silent degradation of the reasoning layer is the failure mode to design against." The deterministic core exists precisely so the market-data half of the system is not hostage to an external free API. Making the constraint an ADR turns a budget detail into a governed decision with an explicit failure mode.

## Alternatives Considered
- **Block the whole pipeline on AI availability:** simplest, but forfeits the resilience of the deterministic core. Rejected.
- **Free tier, silently best-effort:** hides failures and produces untrustworthy reasoning. Rejected.
- **Pay for AI:** not an option under the current hard budget constraint.

## Trade-offs
- Reasoning quality depends on an uncontrollable free endpoint; latency and model availability are external risks accepted for V1.
- Out-of-band AI adds a small orchestration seam now in exchange for never coupling ingestion to AI availability.

## Consequences
- All AI gateways sit behind a port and are reachable only out-of-band from the persisted context store.
- Every AI failure path is logged and observable; nothing in the ingest path imports an AI client.
- Future paid-tier access is an explicit, human-approved configuration change, not an automatic fallback.
