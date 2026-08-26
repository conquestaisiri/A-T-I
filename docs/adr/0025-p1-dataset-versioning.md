# ADR 0025: P1 Dataset Versioning

**Status:** Accepted (P1-001, Tier-1 T1-1)
**Date:** 2026-08-22
**Context:** Research factory needs immutable, content-addressed snapshots to avoid leakage. `backend/domain/research/dataset.py:188` `compute_content_hash` now sorted + `milliseconds` (was unsorted + `microseconds` drift), `PRIMARY KEY(dataset_id,version)` immutability.
**Decision:** `DatasetKind RAW/NORMALIZED` + `DatasetPurpose TRAINING/TEST/AUDIT` + `TestPeriodLock` firewall at `load_records` + `available_at` point-in-time. `DatasetService` frozen `RAW` via `HistoricalDataIngestor`.
**Consequences:** Every `evidence run` is now causally correct, `PBO/DSR` honest.
