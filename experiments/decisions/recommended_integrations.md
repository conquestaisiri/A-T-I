# Recommended Integrations

## 1. Redis
- **Why**: We need a fast, decoupled internal event bus. The current in-memory `asyncio.Queue` only works within a single process. Redis Pub/Sub allows the Observation Layer to broadcast to multiple isolated Python processes (Consumers, AI engines, Data loggers).
- **Benefits**: Instant horizontal scaling; microservice decoupling; native support for channels.
- **Drawbacks**: Adds a crucial infrastructure dependency.
- **Complexity**: Low.
- **Recommendation**: **Integrate LATER, superseded by ADR 0004.** SQLite-first was decided for V1 (single process, zero ops, file-backed persistence with a UNIQUE `event_key` for at-least-once dedup). Revisit Redis only when the pipeline becomes multi-process and the in-memory `ObservationBus` (bounded, block-on-full backpressure) is no longer sufficient.

## 2. ClickHouse
- **Why**: Trading systems generate massive amounts of tick data, order book updates, and execution logs. Traditional DBs choke on this. ClickHouse is explicitly designed for high-throughput time-series and analytical queries.
- **Benefits**: Lightning-fast backtesting; highly compressed storage for market data; used by `polybot` for quantitative analysis.
- **Drawbacks**: High memory footprint; requires managing another database.
- **Complexity**: Medium.
- **Recommendation**: **Integrate LATER.** Wait until our observation layer is running for 24/7 and we need to start persisting the events for AI training.

## 3. CloddsBot / Vercel AI SDK
- **Why**: For connecting LLMs (like Claude/OpenAI) to our internal trading APIs, we need robust tool-calling and agent orchestration.
- **Benefits**: Avoids reinventing prompt engineering, context window management, and retry logic.
- **Drawbacks**: May be heavily tied to TypeScript/Node.js ecosystems, whereas our backend is Python.
- **Complexity**: Medium.
- **Recommendation**: **Integrate LATER.** Wait until we reach the AI reasoning phase, and look for a Python-native equivalent (like LangChain or pure API abstractions).

## 4. Rust-based Execution Engine (Prediction-Market Toolkits)
- **Why**: Contains highly optimized, venue-agnostic execution logic and pre-built adapters for Kalshi, Polymarket, etc.
- **Benefits**: Sub-50ms execution. We wouldn't need to write the WebSocket and REST adapters ourselves.
- **Drawbacks**: Requires polyglot architecture (Python AI + Rust Execution). Drastically increases deployment complexity.
- **Complexity**: High.
- **Recommendation**: **NEVER (or only if latency becomes a critical bottleneck).** We are building an *Autonomous Trading Intelligence* platform, not a pure HFT arb bot. We should maintain Python for now to keep the architecture clean and unified, taking only their *ideas* (venue-agnostic core), not their exact code.
