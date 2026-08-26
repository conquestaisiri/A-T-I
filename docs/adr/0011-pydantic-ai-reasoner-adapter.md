# ADR 0011: PydanticAI Reasoner Adapter

## Status
Proposed

## Context
ATI currently uses `OmniRouteReasoner` (ADR 0010) which calls a local OmniRoute gateway (`localhost:20128/v1`) via a raw OpenAI-compatible HTTP client. The reasoner has known failure modes: malformed JSON, provider timeout, missing fields, invalid actions, excessive tool calls. On any failure it degrades to `STAND_ASIDE`. We need a more robust, typed, and observable reasoner without changing the `AIReasoner` port or `DecisionProposal` schema.

## Decision
Introduce an optional `PydanticAIReasoner` implementing the existing `AIReasoner` interface. It will:
- Use `pydantic-ai-slim` (MIT, 19.2k★, Python 3.14 native)
- Map `DecisionProposal` ↔ Pydantic output model (`DecisionProposalOutput`)
- Preserve current conservative `STAND_ASIDE` fallback on any failure
- Add structured output validation, retry budgets, tool-call limits, usage tracking
- Sit behind the same `AIReasoner` port — no core changes

## Consequences
- **Positive**: Provider portability, schema reliability, telemetry, bounded execution
- **Negative**: New dependency (~50MB wheels), additional configuration surface
- **Neutral**: Does not alter decision pipeline, risk gate, or learning loop

## Integration Record
- Component: `PydanticAIReasoner`
- Purpose: External AI reasoning capability
- Category: Provider Gateway
- Version: `pydantic-ai-slim>=0.1.0`
- Source: https://github.com/pydantic/pydantic-ai
- License: MIT
- Status: Planned
- Priority: High
- Entrypoint: `backend/application/ai/pydantic_ai_reasoner.py`
- Dependencies: `pydantic-ai-slim`, OmniRoute endpoint config
- Capabilities: Structured output, validation retries, tool limits, usage tracking
- Configuration: `PydanticAIConfig(model, provider, max_retries, tool_limit, timeout)`
- Health: Liveness = provider reachable; Readiness = schema validation passes
- Upgrade Path: Version pin in requirements; swap provider via config
- Reason: Measured reliability gain over raw HTTP client on failure injection tests

## Validation Gate
Adopt only if spike demonstrates measurable improvement on:
- Malformed JSON recovery rate
- Provider timeout handling
- Missing field detection
- Invalid action rejection
- Excessive tool call bounding
- Token usage observability
- Latency overhead < 50ms p99

## References
- ADR 0005 (Free-Tier AI Degradation)
- ADR 0006 (AI Entry Point)
- ADR 0010 (Bounded Episodic Memory and LLM Reasoning)
- docs/Constitution/06-Integration-Constitution.md §25-36 (Decision Matrix)