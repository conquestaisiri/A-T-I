# ADR 0027: P1 Baselines & Attribution

**Status:** Accepted (P1-003/004)
**Date:** 2026-08-22
**Context:** Research needs simple baselines with realistic costs and feature ablation.
**Decision:** `backend/application/research/baseline_evaluation.py` simple baselines (`buy_hold`, `sma`, `momentum`) with `EvaluationCosts` realistic, `backend/domain/research/attribution.py` ablation `AttributionReport` via `EvidenceEngine.attribution_evidence`.
**Consequences:** Baselines comparable, `feature_attribution` folded into passport `metrics`.
