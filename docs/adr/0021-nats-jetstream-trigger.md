# ADR 0021: NATS JetStream Trigger

**Status:** Planned (Tier-4 deferred, T4-32)
**Date:** 2026-08-22
**Context:** `ObservationBus 1024` bounded, `EnhancedEventBus 10000` fan-out. Single-process bus limits not yet demonstrated.
**Decision:** Defer NATS JetStream (ADR 0014) until `queue_utilization>0.8` sustained or `publish p99>10ms` due to SQLite. When triggered, NATS will be `library-only` adapter behind `EventBus` port, not replacement. Doc `docs/adr/0014-nats-jetstream-event-backbone.md` already `Status: PLANNED, trigger: Only when single-process bus limits demonstrated` — add metric `queue_utilization>0.8`.
**Consequences:** No new infra until evidence, keep single bus for now.
