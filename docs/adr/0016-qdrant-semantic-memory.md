# ADR 0016: Qdrant Semantic Memory Index

## Status
Proposed

## Context
ATI's episodic memory (`MemoryStore` + SQLite `memory_episodes`) uses brute-force NumPy dot-product for recall (O(N)). At millions of episodes this becomes a latency and throughput bottleneck. ADR 0010 mandates SQLite as authoritative; vector search must be a secondary projection. Research confirms Qdrant embedded (Apache 2.0, Rust, filterable HNSW, binary quantization, pre-filter before ANN) uniquely satisfies ATI's constraints: idempotent upsert by `episode_id`, symbol filtering before ANN, recency weighting, sub-10ms p99 at 100M vectors.

## Decision
Add Qdrant embedded as a shadow semantic index behind a new `SemanticMemoryStore` port:
- SQLite remains source of truth (ADR 0004, ADR 0010)
- `ReflectionService` dual-writes: SQLite (authoritative) → Qdrant (projection)
- `AiOmniRouteReasoner` queries Qdrant for `relevant(context, limit)` semantic recall
- Fallback to SQLite `recall(symbol, limit)` on Qdrant unavailability
- Never allow vector-index failure to block reflection or trading

## Consequences
- **Positive**: Semantic recall for relevant past episodes; bounded latency; graceful degradation
- **Negative**: New dependency (~20MB); dual-write consistency; embedding model versioning
- **Neutral**: Does not change `MemoryEpisode` schema or `MemoryStore` port

## Integration Record
- Component: `QdrantSemanticMemoryStore`, `SemanticMemoryStore` port
- Purpose: Semantic retrieval for episodic memory
- Category: Memory Backend
- Version: `qdrant-client>=1.19.0`
- Source: https://github.com/qdrant/qdrant
- License: Apache 2.0
- Status: Planned
- Priority: Medium (after memory proves valuable)
- Entrypoint: `backend/infrastructure/memory/qdrant_store.py`
- Dependencies: `qdrant-client`, embedding model (sentence-transformers or provider), Qdrant embedded binary
- Capabilities: Vector search, payload filtering (symbol, timestamp), hybrid search (sparse+dense), binary quantization, recency scoring
- Configuration: `QdrantConfig(path, vector_size, distance, quantization, hnsw_m, hnsw_ef_construct)`
- Health: Qdrant reachable, collection exists, disk < 80%, indexing lag < 1000
- Upgrade Path: Qdrant minor versions backward-compatible; embedding model version in collection name
- Reason: Only embedded vector DB with filterable HNSW (pre-filter before ANN), binary quantization, Apache 2.0, Python async SDK

## Validation Gate
- Dual-write: SQLite + Qdrant idempotent on repeated `ReflectionService` calls
- Symbol filter: Qdrant payload index on `symbol` reduces candidates before ANN
- Recency: `timestamp` payload enables recency-weighted scoring
- Fallback: Qdrant crash → `AiOmniRouteReasoner` falls back to SQLite `recall()` without error
- Latency: `relevant()` p99 < 10ms at 1M vectors, 768-dim, binary quantization
- Embedding versioning: collection name includes model hash; reindex on model change

## References
- ADR 0004 (SQLite Persistence)
- ADR 0010 (Bounded Episodic Memory and LLM Reasoning)
- ADR 0014 (NATS JetStream — enables async projection)
- docs/Constitution/06-Integration-Constitution.md §94-96 (Hermes memory framework)