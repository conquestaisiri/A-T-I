# ADR 0020: HA Single-Writer WAL

**Status:** Accepted (Tier-4 deferred, library-only)
**Date:** 2026-08-22
**Context:** T4-31 High Availability requires no single-point-of-failure for persistence. SQLite single-writer is sufficient for paper/dev (ADR 0004) but needs explicit discipline.
**Decision:** Keep `EnhancedEventBus` single-writer `50ms` `executemany` batch as in `event_bus.py:138` (`_batch_writer`), `PRAGMA journal_mode=WAL busy_timeout=5000 synchronous=NORMAL`, `Database.lock` serialises `ledger/memory/reconciliation/dataset` writes. No distributed DB until `queue_utilization>0.8` sustained (trigger metric).
**Consequences:** Paper path never blocks on SQLite fsync (God-mode), `await_flush()` test helper, no NATS/ClickHouse until evidence gates pass. HA is `library-only` until T4 guardrail lifted.
