# ATI — WHAT TO BUILD NEXT, IN ORDER

## Current state in one sentence

ATI has a serious foundation and many advanced components, but the immediate problem is integration correctness: several newly added features exist in code but are not yet correctly wired, timestamp-safe, economically measured, or validated.

## The order

### STAGE 1 — Make the foundation truthful
1. Dependency manifest
2. Regime price bug
3. Explicit feature configuration
4. Event enrichment wiring
5. OFI correctness
6. Tick recorder correctness
7. Purged CV correctness
8. Replay-time determinism
9. PnL correctness
10. Fee accounting
11. Arrival-price capture
12. Reconciliation
13. API protection

### STAGE 2 — Build the research factory
14. Dataset versioning
15. Label engine
16. Baselines
17. Feature ablations
18. Experiment registry
19. Historical sentiment storage
20. Historical proxy-event storage
21. Regime-conditioned evaluation
22. Robustness/multiple testing

### STAGE 3 — Make execution believable
23. Realistic paper fills
24. Queue/latency/partial fills
25. ✅ execution attribution
26. ✅ funding/fee model
27. ✅ sandbox venue lifecycle
28. ✅ reconciliation

### STAGE 4 — Make intelligence useful
29. scenario engine
30. expected net value
31. abstention
32. historical analogs
33. strategy allocator
34. calibrated models
35. drift detection

### STAGE 5 — Controlled autonomy
36. model promotion
37. paper autonomy
38. canary
39. live execution only with explicit authorization
40. gradual scaling
41. autonomous research loop

## Do not reorder the stages just because a later feature sounds more exciting.
