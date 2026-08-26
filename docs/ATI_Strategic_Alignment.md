# ATI Strategic Alignment — Mapping the 42/100 Review to Repository Reality

> **Standing instruction to any agent working in this repository: READ THIS
> FILE before doing any major architectural work.** It is the chief
> architect's analysis of how `ATI_Strategic_Review.md` (the second external
> review) maps onto the actual repository, what already exists, what is still
> missing, and the agreed next actions. It sits alongside
> `ATI_Architecture_Critique.md` (primary critique) and is subordinate to the
> Constitution (`docs/Constitution/00-Master-Index.md`).
>
> **Persisted 2026-08-13 by the chief-architect session.** Re-read
> `ATI_Strategic_Review.md` and this file together before architectural
> decisions. If this file drifts from the code, fix the file — the code is
> the truth.

---

## 1. The one-line verdict of the review

> The architecture is a surprisingly good skeleton (~75-80/100 engineering)
> whose most important part is unproven: **does the intelligence have a
> durable, live-market edge?** Overall maturity 42/100. The direction must
> change from "smarter trading bot" to "a small quantitative research
> institution in software".

This is fully consistent with `ATI_Architecture_Critique.md` ("show me the
evidence this machine can make money without fooling itself") and with the
AGENTS.md standing instruction: **never confuse architectural completeness
with trading intelligence.** The two reviews agree on priorities; the
Strategic Review adds a concrete 40-item roadmap and a mission amendment.

---

## 2. What the review asks the system to become

- **Research is the heart of ATI**, not the LLM, not the strategy, not the
  simulator. The hard problem: *repeatedly discover information not yet in the
  price, prove it is real, know when it stops working, and turn it into
  profitable execution*.
- **AI = researcher first, trader last.** The AI reasoner must never be
  assumed to have predictive value; its incremental contribution must be
  measured against non-AI baselines.
- **As difficult to fool as possible**, not as complex as possible.
  "Earned complexity" only after evidence.
- **Every strategy gets a passport + an evidence score**; promotion requires
  statistical, execution, stability, risk, regime, OOS and paper evidence.
- **Strategy death is a feature**: degrade → demote → retire quickly.
- **No autonomy wiring before the research pipeline can repeatedly produce
  strategies that survive strict out-of-sample evaluation.**

---

## 3. Mapping the review's roadmap to repository reality (refreshed 2026-08-17)

Legend: ✅ built and tested · ◐ partial / library-only / not wired · ❌ missing

### Tier 1 — Critical (the "truth layer")

| # | Review item | Repo status | Where / note |
| --- | --- | --- | --- |
| 1 | Immutable market data | ✅ | `dataset_store` / `dataset_repository` (P1-001): versioned, hash-checked (SHA-256), immutable PK, point-in-time reads; quality scan (T1-1-4, 2026-08-19): `dataset_quality_service.py` reports per-version gaps/duplicates/outliers via `datasets quality` (audit loads labelled `AUDIT`, never firewall-blocked) |
| 2 | Event replay | ✅ | `backtest_harness` + `tick_recorder` + `build_replay_steps` (P0-006/008): same pipeline consumes historical events; determinism guard (T1-2-1, 2026-08-19): identical event feed -> byte-identical equity curves across fresh pipelines (ADR 0007) |
| 3 | Research firewall | ✅ | `test_period_locks` + `load_records(purpose=TRAINING)` refusal at data access (P5-002, 2026-08-13): the exact load scope (kind + window + `available_at`) is joined against locks, so a training load can never serve a locked test period |
| 4 | Experiment registry | ✅ | `experiment_registry` + `experiment_store` (P1-005); lineage query (T1-4-1, 2026-08-19): `experiments lineage <experiment-id>` walks the parent/child DAG (`parent_experiment_id`, P1-008) — bounded, cycles/dangling reported, feeds passport lineage (T3-22-1) |
| 5 | Leakage detection | ✅ | Adversarial leakage tests (P0-007) + runtime leak-detector (T1-3-1/T1-5-1, 2026-08-19): `leak_detector_service.py` probes the firewall per frozen version via `datasets leaks <dataset-id>` — reports LEAK (TRAINING served despite overlapping locks, the bypass the firewall cannot see) and DEAD_LOCK (claim protecting nothing); counts protected records with labelled AUDIT loads |
| 6 | CPCV / purged validation | ✅ | `purged_cv.py`: PurgedKFold / WalkForwardCV / CombinatorialPurgedCV; settings spec (T1-6-1, 2026-08-19): every splitter serializes its exact gap/embargo via `as_dict()`, `evidence run --embargo N` surfaces the applied validation gap in the passport + archived report (locked-OOS harness is WalkForwardCV-only by design, P1-009; CPCV library-only) |
| 7 | PBO / Deflated Sharpe | ✅ | `backend/domain/research/pbo.py` (P5-001): `compute_pbo` + `compute_deflated_sharpe`, wired into `DecisionPipelineEvaluator` + `robustness.py`; **consumed** by `verdict_for_evidence` gates (PBO reject + DSR reject, T1-7-1, 2026-08-17) |
| 8 | Locked out-of-sample evaluation | ✅ | `DecisionPipelineEvaluator` (P1-009): strict past-to-future folds, fresh pipeline per fold, shared cost ruler, honest pooled evidence; operator runnable via CLI (`evidence run`, 6-6) — real-data run (T1-8-1) awaits BTCUSDT klines |
| 9 | Realistic transaction-cost model | ✅ | `PaperFeeConfig` fees+impact, `EvaluationCosts` for research, execution attribution, funding (P0-010/011, P1-003, P2-001/002, ADR 0017/0018) |
| 10 | Live-vs-paper execution calibration | ◐ | Reconciliation + attribution + impact calibrator + systematic comparison harness exist (T1-10-1); calibration feed into passports wired (`record_calibration`, P5-004) — awaits live-vs-paper records |

### Tier 2 — Intelligence

| # | Review item | Repo status | Where / note |
| --- | --- | --- | --- |
| 11 | Regime engine | ◐ | `RegimeDetector` + regime feature + `regime_evaluation.py` bucketing; regime-conditional routing lands regime evidence on the passport (`regime_oos_evidence.py`, T2-11-1) — advisory only, never verdict-gating (guardrail) |
| 12 | Strategy population | ◐ | Baselines + variants exist; persistent strategy population registry + gated competition ladder (T2-12-1, library, advisory — ranking never changes verdicts/statuses) |
| 13 | Strategy ensemble | ◐ | `strategy_allocator` + scenario engine exist; ensemble/competition not wired to allocation |
| 14 | Strategy allocator | ✅ | `strategy_allocator.py` (research tool) |
| 15 | Edge decay detector | ◐ | `adwin.py` drift detection + `edge_monitor.py` dedicated edge-monitoring (`environment_for_status` / `status_for_environment` bijection) — library, feeds death system |
| 16 | Feature attribution | ✅ | `feature_attribution.py` ablation runner (P1-004) |
| 17 | Feature ablation | ✅ | same, with regime bucketing + flip costing |
| 18 | Capacity estimation | ❌ | Not built |
| 19 | Signal confidence estimation | ◐ | Proposals carry confidence/uncertainty; no calibrated signal-quality scoring |
| 20 | Uncertainty estimation | ◐ | Calibration exists in promotion (`domain/research/promotion.py`); no general uncertainty layer |

### Tier 3 — Autonomy (NOT to be wired into the live path until evidence gates pass)

| # | Review item | Repo status | Note |
| --- | --- | --- | --- |
| 21 | Automated hypothesis generation | ✅ | `research_loop` / hypothesis domain (library) |
| 22 | Automated experiment generation | ✅ | `experiment_registry` + research loop (library) |
| 23 | Automated validation | ✅ | robustness + OOS evaluator (library) |
| 24 | Paper campaign management | ✅ | `paper_campaign_service` + `paper_autonomy` (library) |
| 25 | Canary deployment | ✅ | `canary.py` + `canary_harness` (library) |
| 26 | Automatic demotion | ◐ | **death system built** (T3-26-1, 2026-08-17): `DemotionAction` STAY/DEGRADE/DEMOTE/RETIRE with harshest-wins precedence, `DeathSystemService.evaluate` (edge trigger + retired campaign/canary verdicts) + `apply` (status transition + automatic rollback record) — library-only, not wired to live (guardrail) |
| 27 | Rollback | ✅ | rollback records appended to passport lifecycle (T3-27-1) |
| 28 | Strategy retirement | ✅ | death-system RETIRE (terminal status) + tombstone enforcement (T3-28-1, 2026-08-17): engine refuses transitions/re-evaluation/new campaigns on retired passports; rollback record closes the death audit; `evaluate` returns STAY on a corpse — dead strategies stay visible in the population views by design |
| 29 | Capital reallocation | ◐ | **portfolio-level optimizer built** (T3-29-1, 2026-08-17): `capital_allocator.py` — store-backed, evidence-gate guardrail enforced (only gate-passing, non-retired passports earn capital; every exclusion named), correlation-damped sizing on the measured matrix, `rebalance()` deltas — library-only, not wired to live |
| 30 | Research feedback loop | ◐ | `autonomy_program` composes the ladder deterministically; **measured loop built** (T3-30-1, 2026-08-17): `measured_loop.py` — iterations land passports on the ledger (misses recorded), loop quality = passport survival rates read at measurement time; not wired to live |

### Tier 4 — Production (deliberately later)

31-40 (HA, distributed events, DB migration, secrets, monitoring, alerting,
DR, order-state reconciliation, exchange failover, multi-venue) are mostly
NOT built and should stay that way. ⚠ The review and the critique both say:
**do not build production/complexity before evidence.** Exception: the
review's "better database architecture" point — SQLite is acknowledged as
excellent dev/paper infra and not automatically final production infra
(critique §8).

---

## 4. Agreed next priorities (in order)

These extend the critique's §18 list; both documents point the same way.

1. **Prove the decision pipeline out-of-sample** — ✅ DONE (P1-009,
   2026-08-13). Instrument exists; now feed it real historical data and run.
2. **Prove the simulator against realistic execution** — run the existing
   simulator on historical bars and compare simulated fills to realistic
   execution assumptions (impact calibrator + attribution data).
3. **Quantify the AI reasoner's incremental contribution** — quant-only vs
   AI-only vs quant+AI vs rules-only via the OOS evaluator's reasoner-factory
   seam. Keep only what measurably improves.
4. **PBO / Deflated Sharpe (review Tier-1 #7)** — ✅ DONE (2026-08-13, P5-001):
   `backend/domain/research/pbo.py` adds the full probability-of-backtest-overfitting
   and deflated Sharpe ratio on OOS fold returns (wired into the OOS evaluator,
   shared with robustness). ✅ CONSUMED (T1-7-1, 2026-08-17): `verdict_for_evidence`
   gates reject on PBO and DSR, and `EvidenceEngine` passes `pbo` on both the
   issue and re-record paths.
5. **Research firewall enforcement ("locked test is dead")** — ✅ DONE
   (2026-08-13, P5-002): dataset access refuses to serve a locked test period
   for training once claimed as a test set; the check is evaluated against the
   exact load scope at data-access time (Tier-1 #3).
6. **Evidence engine / strategy passport** — every evaluated strategy carries
   one auditable record: hypothesis, data, features, labels, train/val/test
   periods, trial count, costs, OOS/paper/live performance, promotion and
   rollback reasons. ✅ Extends through T3-27-1 (rollback records) and
   T3-28-1 (RETIRED tombstone — terminality enforced by the engine).
7. **Run real data** — wire the OOS evaluator to the P1-001 dataset store and
   run BTCUSDT history; produce the first honest evidence report. Operator
   step now executable via the CLI (6-6: `ingest` + `evidence run`);
   T1-8-1 awaits the real klines dataset.
8. **Only then** consider edge-decay monitoring, capacity estimation, and
   Tier-3 automation — and never wire the autonomy ladder into the live path
   before evidence gates pass (AGENTS.md rule 3).

---

## 5. What NOT to do (standing guardrails, from both reviews)

- Do NOT add indicators, LLMs, subsystems, or AI capacity before evidence.
- Do NOT wire the autonomy ladder (WS2+) into the live path.
- Do NOT promote strategies on backtest performance alone.
- Do NOT let learning alter risk parameters without operator approval.
- Do NOT chase latency/GPU/distributed infra ("sexy stuff later").
- Do NOT let the LLM become the assumed source of alpha; measure it.
- Do NOT add complexity to resemble institutional firms; earn it with evidence.

---

## 6. How the mission statement changes

Old (implicit): "build an autonomous trading bot".

New (per review, consistent with Constitution): **ATI is a quantitative
research and autonomous trading platform whose primary objective is to
discover, validate, deploy, monitor and retire genuinely profitable trading
strategies under realistic market conditions — and it must be as difficult to
fool as possible.** The system's honest self-description remains "an
autonomous trading intelligence framework with an operational
market-decision/simulation pipeline and a planned autonomy layer" until the
evidence gates pass.

---

## 7. Record of review ingestion

- 2026-08-13: second external review received from operator; persisted
  verbatim as `docs/ATI_Strategic_Review.md`; alignment analysis written here.
- AGENTS.md and this file updated in the same change so post-compaction
  sessions re-read the standing reviews before architectural work.
- Task queue: P1-009 (OOS evaluator) DONE; P5-001 (PBO/Deflated Sharpe,
  Tier-1 #7) DONE; P5-002 (research firewall, Tier-1 #3) DONE; P5-003
  (strategy passport / evidence engine) DONE; P5-004 (live-vs-paper
  calibration harness, Tier-1 #10) DONE; P5-005 (real-data evidence run,
  Tier-1 #8 + data-quality gate 6-4) DONE; P5-006 (simulator-vs-realistic-
  execution proof, Tier-1 #9 + T1-9-1 + T2-18 prerequisites: cost-model
  sensitivity sweep + simulator-vs-square-root-law validation) DONE — all
  2026-08-13. P5-007 (AI reasoner incremental-contribution quantification:
  `reasoner_ablation.py` + `quant_momentum_scorer.py`; 26 tests, suite
  1304 green) DONE 2026-08-14. P5 evidence queue complete.
  T2-11-1 (regime-conditional strategy routing: `RegimeOosEvidenceBuilder`
  + regime evidence embedded in the passport, advisory only; 10 tests,
  suite 1315 green) DONE 2026-08-17.
  T2-12-1 (strategy population registry: `strategy_population.py` service
  projecting the passport store into member rows + gated competition
  ladder, 3+ real candidates, advisory only; 16 tests, suite 1331 green)
  DONE 2026-08-17.
  T2-13-1 (strategy ensemble wired to allocation: `ensemble_allocator.py`
  feeds evidence-gated candidates into the risk-parity allocator — pooled
  mean excess expected return, operator-supplied volatility, regime-fit =
  T2-11-1 score, risk gate decides; + passport-repository commit-durability
  fix; 19 tests, suite 1350 green) DONE 2026-08-17.
  T2-15-1 (dedicated strategy edge-monitoring system: `edge_monitor.py`
  ADWIN-on-rolling-returns per passport, honest verdict ladder
  INSUFFICIENT→HEALTHY/WATCHING/DECAYED — drift is not decay, decay is a
  state that persists until the window pays again; advisory library-only
  demotion trigger via the promotion chain, nothing wired into the live
  path; 30 tests, suite 1380 green) DONE 2026-08-17.
  T2-13-2 (correlation-aware allocation input: `portfolio_correlations.py`
  Pearson surface measured from aligned shared OOS return series, neutral
  0.0 with recorded state for unmeasurable pairs, wired into
  `EnsembleAllocator.allocate(returns_by_id=...)` — never guessed,
  candidates without series excluded; 24 tests, suite 1404 green) DONE
  2026-08-17.
  T2-16-1 (attribution summary folded into passport evidence:
  `EvidenceEngine` embeds the attribution report verbatim on issue/
  rerecord, same reproducible pattern as regime evidence; 4 tests, suite
  1408 green) DONE 2026-08-17.
  T2-18-1 (capacity model: `capacity_model.py` inverts the calibrated
  square-root impact law into a per-symbol capacity bound — no calibration
  means no capacity, zero edge means zero size, negative eta is bounded at
  the participation cap, never infinity; 19 tests, suite 1427 green) DONE
  2026-08-17.
  T3-30-1 (measured research feedback loop — each iteration lands a
  passport on the ledger, misses recorded; loop quality = passport
  survival rates read from the ledger at measurement time; 12 tests,
  suite 1584 green) DONE 2026-08-17. **Tier 3 (autonomy) fully
  complete, library-only.**
  T1-1-4 (dataset-quality report — frozen versions scanned for gaps/
  duplicates/outliers, audit loads labelled `AUDIT`; 18 tests, suite
  1602 green) DONE 2026-08-19.
  T1-2-1 (replay determinism guard — byte-identical equity curves across
  fresh pipelines; 1 test, suite 1603 green) DONE 2026-08-19.
  T1-3-1/T1-5-1 (runtime leak-detector — probes the firewall per frozen
  version via `datasets leaks`, LEAK/DEAD_LOCK findings, adversarial
  bypass test; 10 tests, suite 1618 green) DONE 2026-08-19.
  T1-4-1 (experiment lineage query — `experiments lineage`, bounded DAG
  walk, cycles/dangling reported, feeds passport lineage; 13 tests, suite
  1631 green) DONE 2026-08-19.
  T1-6-1 (CV gap/embargo surfaced — splitter `as_dict()` specs, `evidence
  run --embargo`, proven in passport + archived report; 5 tests, suite
  1636 green; `.gitignore` `research/` anchored to `/research/`) DONE
  2026-08-19.
  Next queue entry: Tier-1 reconciliation (T1-8-1 real-data run, operator
  step).
  Master backlog: `docs/ATI_BACKLOG.md`.
