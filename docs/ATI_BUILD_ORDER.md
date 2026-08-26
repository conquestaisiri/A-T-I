# ATI — WHAT TO BUILD NEXT, IN ORDER

## Current state in one sentence

All items in the P0-P4 task queue (docs/ATI_TASK_QUEUE.yaml, 43 tasks incl. P5-001..P5-004) are DONE and verified, and P5-001 (PBO/Deflated Sharpe false-discovery defense) is DONE: 1149 tests pass, mypy clean on 196 source files, ruff clean. The research → validation → promotion → canary → scaling ladder is implemented and tested, and the composed autonomy program (P4-006) runs one candidate through the whole ladder in a single deterministic step. Every STAGE 1-5 build-order item is now deliverable. The second external review (docs/ATI_Strategic_Review.md, 42/100) redirects the project toward "a small quantitative research institution in software" — the remaining P5 tasks (research firewall, strategy passport, live-vs-paper calibration) are the agreed next evidence work, mapped in docs/ATI_Strategic_Alignment.md.

## The order

### STAGE 1 — Make the foundation truthful
1. ✅ Dependency manifest (docs/ATI_DEPENDENCY_MANIFEST.md)
2. ✅ Regime price bug (domain/context/regime_detector.py; application/regime removed, G6)
3. ✅ Explicit feature configuration (config/context.yaml strict loader, P0-015)
4. ✅ Event enrichment wiring (application/pipeline/observation_enrichment.py routes order-book snapshots into micro-price state + OFI book, wired in context_pipeline_service, tested)
5. ✅ OFI correctness (domain/context/features/order_flow.py multi-level, tested)
6. ✅ Tick recorder correctness (validation/tick_recorder.py safe storage, tested)
7. ✅ Purged CV correctness (validation/purged_cv.py + combinatorial variant, tested)
8. ✅ Replay-time determinism (backtest_harness, verified)
9. ✅ PnL correctness (domain/execution/pnl.py, short PnL sign fixed, tested)
10. ✅ Fee accounting (paper fills maker/taker fees, tested)
11. ✅ Arrival-price capture (ccxt_gateway._capture_arrival_mid + paper_fill_engine arrival-price slippage attribution, tested)
12. ✅ Reconciliation (application/interfaces/venue_state.py + reconciliation service/repo, tested)
13. ✅ API protection (all /v1 routers behind verify_api_key, P0-013)

### STAGE 2 — Build the research factory
14. ✅ Dataset versioning (application/research/dataset_service.py + SqliteDatasetRepository, tested)
15. ✅ Label engine (application/research/label_engine.py, tested)
16. ✅ Baselines (application/research/baseline_evaluation.py: AlwaysFlat/BuyAndHold/Momentum/MAC, tested)
17. ✅ Feature ablations (application/research/ablation_runner.py, tested)
18. ✅ Experiment registry (application/interfaces/experiment_store.py + SqliteExperimentRepository, tested)
19. ✅ Historical sentiment storage (infrastructure/sqlite/alt_data_repository.py, publication-time reads, tested)
20. ✅ Historical proxy-event storage (same alt-data store: SEC/proxy items keyed by publication time, tested)
21. ✅ Regime-conditioned evaluation (application/research/regime_evaluation.py, tested)
22. ✅ Robustness/multiple testing (application/research/robustness.py, tested)
23. ✅ Out-of-sample decision-pipeline evaluation (application/research/decision_pipeline_evaluator.py: past-to-future walk-forward folds, fresh pipeline per fold, pipeline pays the same cost ruler as baselines, honest pooled evidence — win rate, profit factor, net expectancy, positive-fold and beats-buy-and-hold rates; tested)

### STAGE 3 — Make execution believable
24. ✅ Realistic paper fills (application/simulation/paper_fill_engine.py + sandbox_venue.py, tested)
25. ✅ Queue/latency/partial fills (sandbox venue FIFO queue_pos, partial fills, TTL expiry, tested)
26. ✅ execution attribution (ADRs 0017-0018, tested)
27. ✅ funding/fee model (ADRs 0017-0018, tested)
28. ✅ sandbox venue lifecycle (ADRs 0019, tested)
29. ✅ reconciliation (tested)

### STAGE 4 — Make intelligence useful
30. ✅ scenario engine (application/research/scenario_engine.py, tested)
31. ✅ expected net value (ScenarioEvaluation.gross/net_expected_value_pct, tested)
32. ✅ abstention (ScenarioEngine.js_abstains / ABSTAIN, tested)
33. ✅ historical analogs (application/research/analog_retrieval.py, tested)
34. ✅ strategy allocator (application/research/strategy_allocator.py, tested)
35. ✅ calibrated models (calibration in domain/research/promotion.py + application/research, tested)
36. ✅ drift detection (application/research/adwin.py + validation/adwin.py, tested)

### STAGE 5 — Controlled autonomy
37. ✅ model promotion (domain/research/promotion.py + application/research/promotion_engine.py controlled ladder, P4-001)
38. ✅ paper autonomy (domain/research/paper_campaign.py + application/research/paper_autonomy.py long unattended paper campaign, P4-004)
39. ✅ canary (domain/research/canary.py + application/research/canary_harness.py, P4-003)
40. ✅ live execution only with explicit authorization (infrastructure/execution/errors.py + ccxt_live_authorized guard, P0-014)
41. ✅ gradual scaling (domain/research/scaling.py + application/research/gradual_scaling.py post-canary capital ramp, P4-005)
42. ✅ autonomous research loop (domain/research/hypothesis.py + application/research/research_loop.py, P4-002)
43. ✅ composed autonomy program (domain/research/autonomy_program.py + application/research/autonomy_program.py runs one candidate through the whole ladder in one deterministic program, P4-006)

### STAGE 6 — Earn the intelligence (P5, per docs/ATI_Strategic_Review.md + docs/ATI_Strategic_Alignment.md)
44. ✅ PBO / Deflated Sharpe false-discovery defense (P5-001: backend/domain/research/pbo.py — compute_deflated_sharpe + compute_pbo, pure/seeded/strategy-free; evaluator n_trials + pooled.deflated_sharpe + evaluate_variants() PBO across reasoner factories; robustness.py imports the shared expected-max; 24 + 13 new tests)
45. ⏳ Research firewall — locked test is dead (P5-002)
46. ⏳ Strategy passport / evidence engine (P5-003)
47. ⏳ Live-vs-paper execution calibration harness (P5-004)
48. ⏳ Run the OOS evaluator (P1-009) on real historical data and quantify the AI reasoner's incremental contribution

## Do not reorder the stages just because a later feature sounds more exciting.
