# 0004: SQLite as the V1 Persistence Layer

## Decision
Persistence for V1 is a single-process, file-backed SQLite database (`data/trading_intelligence.db`) owned by one `Database` connection per process. SQLite is the durable store for normalized observation events (at-least-once) and produced market contexts. Redis is **not** integrated in V1.

## Why
The observation and context pipeline is single-consumer, single-process, and moderate in throughput. SQLite provides durable, ACID, zero-ops persistence with no external dependency. At-least-once delivery is implemented with a UNIQUE `event_key` over the `observation_events` table: replays insert no row and report `False`, so re-delivery never duplicates a market event. A schema, a connection, and two small repositories are all V1 requires.

This ADR also resolves a documentation contradiction: `recommended_integrations.md` carried a "Integrate Redis NOW" note that predates the SQLite decision. The note is now superseded; the docs no longer argue with each other.

## Alternatives Considered
- **Redis / in-memory store:** adds an operational dependency and does not provide durable history. Rejected for V1; revisit only when the pipeline becomes multi-process or when throughput outgrows a single writer.
- **Postgres:** correct long-term target for multi-process scale, but heavy operational overhead for a single-user dev/backtest system. Defer.

## Trade-offs
- One connection per process with `check_same_thread=False` supports web-server worker threads; SQLite itself serialises writes, and the pipeline never touches the connection from two threads at once.
- WAL journal mode is enabled for concurrent readers during a write. A single writer connection bounds throughput; this is the explicit V1 backpressure policy (bounded `ObservationBus`, block-on-full), not an accident.

## Consequences
- Repositories live behind ports in the application layer (`ObservationRepository`, `ContextRepository`); the SQLite implementation stays in `backend/infrastructure/sqlite/`.
- Future swap to Postgres/Redis replaces only the infrastructure implementation, never the application or domain layers.
- `docs/adr/` supersedes the Redis note in `recommended_integrations.md`.
