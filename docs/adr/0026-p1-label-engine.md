# ADR 0026: P1 Label Engine

**Status:** Accepted (P1-002)
**Date:** 2026-08-22
**Context:** Labels must be defined before model training, forward-looking windows explicit.
**Decision:** `backend/domain/research/label.py:84` `LabelDefinition` with `name` derived from `kind`, `horizon` explicit, `LabelSeries` timestamped.
**Consequences:** No look-ahead leakage, `PurgedCV` embargo explicit.
