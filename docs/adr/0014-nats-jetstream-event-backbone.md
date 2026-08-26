# ADR 0014: NATS JetStream Event Backbone

## Status
Proposed

## Context
ATI's current `ObservationBus` uses `asyncio.Queue` (unbounded, no durability, no replay, single-process). The Architecture Review (AR 2026-08-05) identified this as a latent correctness bug. Research confirms NATS JetStream (Apache 2.0, single binary, embedded mode, hierarchical subjects, replay, consumer groups) is the best fit for ATI's V1→V2 migration.

## Decision
Replace `ObservationBus` with `JetStreamObservationBus` in two phases:
1. **Phase 1 (V1.5)**: Embedded NATS JetStream in-process — zero publisher/consumer API changes; enables persistence, replay, backpressure visibility
2. **Phase 2 (V2)**: Multi-process deployment — separate ingest/strategy/execution processes connected via NATS cluster

Subject hierarchy: `ati.obs.{symbol}`, `ati.sig.{symbol}`, `ati.exec.{symbol}`, `ati.risk.{symbol}`, `ati.health`

## Consequences
- **Positive**: Durability, replay, consumer groups, exactly-once (with deduplication), Kafka bridge, Apache 2.0
- **Negative**: New runtime dependency (NATS server); subject schema governance; embedded mode limited to single-process
- **Neutral**: Does not change domain logic; `ObservationEvent` serialization unchanged

## Integration Record
- Component: `JetStreamObservationBus`, `NatsEventBus`
- Purpose: Event backbone for observation, signal, execution, risk, health
- Category: Message Bus
- Version: `nats-py>=2.10.0`, `nats-server>=2.10.0`
- Source: https://github.com/nats-io/nats.py, https://github.com/nats-io/nats-server
- License: Apache 2.0
- Status: Planned
- Priority: High (Architecture Review blocker)
- Entrypoint: `backend/infrastructure/event_bus/nats_bus.py`
- Dependencies: `nats-py`, `nats-server` binary (embedded or standalone)
- Capabilities: Pub/sub, streams, consumer groups, replay, JetStream KV/object store, Kafka bridge
- Configuration: `NatsConfig(servers, streams, subjects, retention, max_bytes, max_age)`
- Health: Cluster reachable, stream storage < 80%, consumer lag < 1000
- Upgrade Path: NATS server minor versions rolling; subject schema versioned in config
- Reason: Only embedded-mode, hierarchical-subject, replay-capable, Apache-2.0 message bus with Python native client

## Validation Gate
- Embedded NATS starts in < 2s in test environment
- Current `ObservationBus` tests pass with `JetStreamObservationBus` (drop-in)
- Replay from stream reproduces identical `MarketContext` sequence
- Consumer group distributes load across strategy workers
- Backpressure: publisher blocks when stream storage > 90%

## References
- ARCHITECTURE_REVIEW.md §38-39, §328-330, §398-403
- docs/Constitution/06-Integration-Constitution.md §66, §82-83
- research/repositories/polybot-main (Redpanda+ClickHouse+Grafana stack)