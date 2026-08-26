# ML Infrastructure Landscape for ATI

**Date:** 2026-08-09
**Scope:** Survey of production ML infrastructure for alpha generation and execution optimisation, evaluated against ATI's actual current state.
**Status:** Research only. No code changed. No dependency added.
**Method:** GitHub repos, vendor docs, JMLR/arXiv/ACM papers, published benchmarks. Every latency number is cited. Every Sharpe number is an estimate with stated reasoning and stated uncertainty.

---

## 0. Reader's warning — calibrate against ATI's real state

Before the tables, the honest framing this repository's constitution demands.

`requirements.txt` today is: fastapi, uvicorn, pydantic, pydantic-settings, python-dotenv, websockets, pyyaml, pytest, pytest-asyncio.

**There is no numpy. No pandas. No scikit-learn. No model of any kind.** There is one SQLite persistence layer, a deterministic 5-feature context builder, a decision path, a risk gate, a deterministic backtest replay, and a PydanticAI reasoner adapter (ADR 0011).

That means:

1. **ATI has no ML to serve.** Nine of the ten categories below solve problems ATI does not have yet. A Triton Inference Server in front of zero models is pure cost.
2. **The binding constraint on ATI's Sharpe is not inference latency.** It is the absence of (a) a labelled dataset, (b) an honest validation protocol, (c) a live data history long enough to fit anything. Latency optimisation before those exist is optimising the denominator of a fraction whose numerator is zero.
3. **Therefore the ranking below is deliberately inverted relative to the question's ordering.** Validation methodology and labelling rank above serving infrastructure, because at ATI's stage a wrong validation protocol produces a *negative* realised Sharpe (deploying a phantom edge), while a slow model produces merely a smaller positive one.
4. **"Expected Sharpe contribution" is decomposed into two kinds** and I keep them separate because conflating them is how quant teams lie to themselves:
   - **Δ-additive:** the tool plausibly raises realised risk-adjusted return.
   - **Δ-protective:** the tool prevents realised Sharpe from being far *lower* than backtested Sharpe. Protective value is usually larger and is almost always underrated.

All Sharpe figures assume a base strategy in the **0.5–1.2 annualised** band, which is the honest range for a single-operator crypto/multi-asset system after costs. Anything claiming >2.0 from a library integration is selling something.

---

## 1. Summary table — top 8 by expected Sharpe contribution

| # | Candidate | Category | License | Prod (1-10) | Inference latency | Python quality | Verdict | Expected Sharpe Δ |
|---|---|---|---|---|---|---|---|---|
| 1 | **purgedcv** | Time-series CV | MIT | 7 | N/A (offline) | Excellent (typed, mypy, 98% cov) | **INTEGRATE** | **+0.6 to +1.5 protective** |
| 2 | **Triple-barrier + meta-labelling** (own impl, mlfinpy reference) | Labelling / stacking | MIT (mlfinpy) | 5 | ~50–200 µs/label | Good | **BUILD** (reference mlfinpy) | **+0.10 to +0.25 additive** |
| 3 | **LightGBM + lleaves** | Model + serving | MIT / MIT | 9 / 7 | **9.6 µs** @ batch=1 | Excellent / Good | **INTEGRATE** (LightGBM), **WRAP** (lleaves) | **+0.15 to +0.40 additive** |
| 4 | **hmmlearn + ruptures** | Regime detection | BSD-3 / BSD-2 | 7 / 7 | ~0.1–1 ms Viterbi | Good (sklearn API) | **INTEGRATE** | **+0.10 to +0.30 additive** |
| 5 | **River** (incl. `river.drift.ADWIN`) | Online learning + drift | BSD-3 | 7 | ~5–50 µs `predict_one` | Excellent | **INTEGRATE** (drift first, models later) | **+0.05 to +0.20 protective** |
| 6 | **ONNX Runtime** | Model serving | MIT | 9 | **11.0 µs** @ batch=1 (GBDT) | Very good | **INTEGRATE** (as fallback runtime) | **+0.02 to +0.05 additive** |
| 7 | **MLflow** (registry + tracking only) | Pipeline / registry | Apache-2.0 | 8 | N/A | Good (heavy) | **WRAP** (thin port) | **0 direct; +0.05 protective** |
| 8 | **Custom point-in-time feature store** (on existing SQLite) | Feature store | N/A (ours) | — | <1 ms local read | — | **BUILD** | **+0.3 to +0.8 protective** |

**Total realistic envelope:** roughly **+0.4 to +1.0 additive** Sharpe on top of a working base strategy, plus **+1.0 to +2.3 protective** (i.e. avoided phantom Sharpe). The protective number dominates and that is the whole point.

**Nothing outside this list should be added to ATI in the next 12 months.** Justification per item below.

---

## 2. Feature stores for market data

### 2.1 Feast — https://github.com/feast-dev/feast
- **License:** Apache-2.0 (Linux Foundation).
- **Production readiness:** 7/10. Widely deployed, but no native streaming transformation engine; you supply the bridge from stream → online store, and that bridge is exactly where training/serving skew reappears.
- **Latency:** With Redis online store, published figures conflict: ~5 ms p99 in one benchmark, 15 ms p50 / 45 ms p99 in another. Treat **5–45 ms p99** as the honest band. Compare to lleaves at 9.6 µs — the feature lookup would be **1000× the model inference cost**.
- **Python quality:** Good. Declarative `FeatureView` API, clean registry, decent typing.
- **Recommendation: IGNORE (for ATI).**
- **Sharpe:** ~0. Feast solves *organisational* skew — many teams, many models, one definition. ATI has one process and one definition already enforced by `FeatureRegistry` in the domain layer. Adding Feast imports Redis + a registry service + materialisation jobs to solve a problem the Clean Architecture boundary already solves. It would add ~5–45 ms to the hot path and negative Sharpe.

### 2.2 Tecton — https://www.tecton.ai
- **License:** Proprietary. **Acquired by Databricks, August 2025**; the standalone product is being absorbed into Mosaic AI. Buying into it now is buying into a migration.
- **Production readiness:** 9/10 as engineering; 4/10 as a *strategic* dependency given the acquisition.
- **Latency:** sub-10 ms p99 managed SLA; ~8 ms p50 / 25 ms p99 in third-party benchmarks.
- **Python quality:** Very good declarative SDK.
- **Recommendation: IGNORE.** Cost starts in the thousands per month, live trading must never depend on a vendor SLA you cannot inspect, and the product is mid-absorption.
- **Sharpe:** 0. Cost is a direct drag on net returns.

### 2.3 Hopsworks — https://github.com/logicalclocks/hopsworks
- **License:** AGPL-3.0 community edition + commercial enterprise. **AGPL is a hard blocker** for a proprietary trading system if you ever link rather than merely call it over a network — treat as a legal review item, not a casual pip install.
- **Production readiness:** 8/10. RonDB online store is genuinely fast; SIGMOD 2024 benchmarks claim ~10× lower online latency than SageMaker/Vertex feature stores. Hopsworks 5.0 (early 2026) added agent-driven pipeline building.
- **Latency:** claimed sub-millisecond to ~12 ms p50 / 35 ms p99 depending on whose benchmark you read.
- **Python quality:** Good.
- **Recommendation: IGNORE** for ATI; **revisit only** if ATI ever becomes multi-model, multi-operator, multi-venue with a real ops team.
- **Sharpe:** 0 at current scale.

### 2.4 FeatureBase
- **License:** Apache-2.0. **Project momentum has effectively stopped**; the bitmap-index-for-features thesis lost to the Redis/RonDB/DynamoDB consensus. Verify current status before any consideration.
- **Recommendation: IGNORE.**

### 2.5 HopsFS
- Distributed filesystem underneath Hopsworks. Irrelevant to a single-node system. **IGNORE.**

### 2.6 ArcticDB — https://github.com/man-group/ArcticDB
- **License:** **Business Source License 1.1.** Free to read the source, but *"use of ArcticDB in production (including business or commercial environments) requires a paid licence from ArcticDB Limited."* **Running ATI live on the free tier would be a licence violation.** This is the single most commonly missed licence trap in the quant Python ecosystem.
- **Production readiness:** 9/10. Built at Man Group, integrated into Bloomberg BQuant. Billions of rows, versioned, time-travel, S3/LMDB backed.
- **Latency:** Not an online store. Research-time reads of large frames in seconds; not a µs-scale serving path.
- **Python quality:** Excellent — Pandas in, Pandas out, C++ engine.
- **Recommendation: IGNORE now; FORK-equivalent later** — i.e. if ATI ever needs versioned tick storage, either buy the licence or build the narrow subset (append-only Parquet + a version manifest) yourself. The subset is maybe 400 lines.
- **Sharpe:** 0 direct. Would be **+0.1 protective** at multi-TB scale via reproducible research datasets. ATI is nowhere near that.

### 2.7 ⭐ Custom point-in-time feature store on existing SQLite — **BUILD** (rank #8)
- **License:** Ours.
- **What it is:** Extend the existing `backend/infrastructure/sqlite/` layer with two columns on every persisted feature row: `event_time` (when the market fact occurred) and `ingest_time` (when ATI learned it). Every training query becomes `WHERE ingest_time <= as_of`. That single discipline is ~90% of what a feature store buys you.
- **Latency:** <1 ms for a local indexed SQLite read of a single feature vector. Zero network hops.
- **Recommendation: BUILD.** This is the correct answer for ATI and it is not close.
- **Sharpe: +0.3 to +0.8 protective.** Reasoning: look-ahead bias via "latest value" joins is the single most common source of backtest inflation in retail-scale quant systems, routinely worth 0.5–1.5 phantom Sharpe. The `purgedcv` documentation demonstrates the mechanism starkly: on a UCI air-quality dataset, adding one innocuous cumulative-hour counter drove naive R² from 0.07 to **0.99** while purged CV correctly stayed at −1.52. Bi-temporal storage prevents the class of bug that produces that. Cost: roughly one sprint.

---

## 3. Online / incremental learning

### 3.1 River — https://github.com/online-ml/river · https://riverml.xyz
- **License:** BSD-3-Clause. **Requires Python ≥3.11** (v0.25.0). ATI runs CPython 3.14 — verify wheel availability on 3.14 before committing; this is a real risk, River historically lags new CPython releases.
- **Production readiness:** 7/10. Merger of `creme` + `scikit-multiflow`, JMLR-published (Montiel et al., JMLR 22, 2021). Actively maintained. The maintainers themselves state it "focuses on clarity and user experience, more so than performance" and openly ask *"Should I be using River? The answer is likely no."* That honesty is worth respecting.
- **Latency:** Very fast per single sample. JMLR benchmarks on Elec2 (45,312 samples, 8 features) show River at or faster than sklearn/creme/scikit-multiflow for GNB, LogReg, Hoeffding Tree. Practical band: **~5–50 µs** for `predict_one` on linear/Hoeffding models; **~100 µs–1 ms** for ensembles (Adaptive Random Forest). Dict-based I/O, not arrays — allocation-heavy but predictable.
- **Python quality:** Excellent. `learn_one` / `predict_one`, pipeline `|` operator, mypy-checked.
- **Recommendation: INTEGRATE — but drift detection first, models second.**
  - Phase 1: `river.drift.ADWIN` only. Zero model risk, immediate value.
  - Phase 2 (later): online logistic regression as a *challenger*, never as champion.
- **Sharpe: +0.05 to +0.20 protective.** Reasoning: online learning does not find alpha; it slows alpha decay. If a batch model's edge decays with a ~6-month half-life and continuous adaptation extends that to ~9 months, you keep roughly 15–25% more of the edge per year, which on a 0.8 base is ~+0.12–0.20. **Counter-argument I take seriously:** online learning on financial data also adapts *to noise*, and an ADWIN-triggered reset during a transient shock can destroy a genuinely stationary edge. Net expected value is positive but modest and depends entirely on gating updates behind drift confirmation rather than updating every tick.

### 3.2 scikit-multiflow
- **License:** BSD-3. **Superseded — merged into River.** The JMLR River paper explicitly states River "supersedes said packages."
- **Recommendation: IGNORE.** Using it in 2026 is choosing an archived library.

### 3.3 Vowpal Wabbit — https://vowpalwabbit.org · https://github.com/VowpalWabbit/vowpal_wabbit
- **License:** BSD-3.
- **Production readiness:** 8/10 as a C++ engine (Microsoft Research backing, powers Azure Personalizer); 5/10 as a *Python* dependency — the Python bindings are a thin wrapper over a text-format CLI mindset, and the project's centre of gravity has shifted to Microsoft's internal/hosted uses.
- **Latency:** Sub-10 µs per example for hashed linear models is realistic; VW is genuinely one of the fastest online learners in existence.
- **Python quality:** Mediocre. Non-Pythonic input format, awkward error surfaces, build friction.
- **Recommendation: IGNORE for prediction; WRAP later for contextual bandits.** VW's genuine differentiator is `cb_explore_adf` — contextual bandits with exploration. That is the *correct* formalism for "which of N strategy variants do I allocate to right now," and it is a far better fit for ATI's operator-supervised allocation problem than deep RL.
- **Sharpe: +0.05 to +0.15 additive** *if and only if* ATI ever runs ≥3 concurrent strategy variants and needs principled online allocation. Zero before that.

### 3.4 Incremental XGBoost / LightGBM
- **License:** Apache-2.0 / MIT.
- **Production readiness:** 9/10 as batch learners. As *incremental* learners: 4/10. Both support continued training (`init_model` / `xgb_model`), but this is warm-starting, not true incremental learning — tree structure from the old regime persists and the model drifts toward an incoherent mixture of regimes.
- **Latency (inference, batch=1, NYC-taxi model, dedicated i7-4770, min over 20,000 runs — siboehm/lleaves benchmark):**

  | Runtime | batch=1 | batch=10 | batch=100 |
  |---|---|---|---|
  | LightGBM (native) | 52.31 µs | 84.46 µs | 441.15 µs |
  | ONNX Runtime | 11.00 µs | 36.74 µs | 190.87 µs |
  | Treelite | 28.03 µs | 40.81 µs | 94.14 µs |
  | **lleaves** | **9.61 µs** | **14.06 µs** | **31.88 µs** |

- **Python quality:** Excellent (both).
- **Recommendation: INTEGRATE LightGBM** as the default model class. **Do not use incremental mode** — use scheduled full retrains gated by drift detection instead. Full retrain of a 200-tree model on 10⁵ rows is seconds; there is no operational reason to accept the incoherence of warm-starting.
- **Sharpe: +0.15 to +0.40 additive** (combined with lleaves, see §5.7). Reasoning: this is the delta from *no model* to *a well-regularised GBDT on properly-labelled features*. It is the single largest additive item, and also the one most likely to be squandered by bad validation — which is why purgedcv ranks above it.

### 3.5 nevergrad — https://github.com/facebookresearch/nevergrad
- **License:** MIT.
- **Production readiness:** 7/10. Solid gradient-free optimiser zoo from Meta.
- **Recommendation: IGNORE.** ATI's hyperparameter space will be small. Wrong tool at this stage; Optuna is the more idiomatic choice if/when tuning is needed, and even that is premature. Worse: powerful hyperparameter search on financial data is an **overfitting accelerator** — it increases the number of effective trials, which directly deflates the Deflated Sharpe Ratio.
- **Sharpe: −0.1 to +0.05.** Genuinely plausibly negative. Named for honesty.

---

## 4. Reinforcement learning for trading

Blunt summary before details: **RL for directional trading is the highest-hype, lowest-realised-Sharpe category in this document.** RL for *execution* is a different and far more defensible proposition.

### 4.1 FinRL / FinRL-X (FinRL-Trading) — https://github.com/AI4Finance-Foundation/FinRL · https://github.com/AI4Finance-Foundation/FinRL-Trading
- **License:** MIT (FinRL) / Apache-2.0 (FinRL-Trading).
- **Production readiness:** FinRL 3/10 — the maintainers now explicitly label it *"the original FinRL library for education, benchmarking, and research prototyping."* FinRL-X 5/10 — better architecture (weight-vector interface contract, `bt` backtest engine with explicit transaction costs, SQLite caching, Alpaca live path), still young.
- **Latency:** Policy forward pass ~0.5–5 ms (PyTorch MLP). Fine for anything slower than tick-level.
- **Python quality:** FinRL: poor — 14 hand-wired data processors, coupled three-layer monolith, notebook-derived code. FinRL-X: substantially better, decoupled layers.
- **Recommendation: IGNORE the library; STEAL two ideas.** (a) FinRL-X's weight-vector-as-sole-interface-contract between strategy and execution is genuinely good architecture and maps cleanly onto ATI's existing decision→risk→execution boundary. (b) Their own published result is the honest data point: agent **+$239 at 0% commission, −$650 at 0.1% commission**, versus buy-and-hold −$355. The edge exists and is entirely consumed by costs.
- **Sharpe: −0.2 to +0.1.** Negative-skewed. Reasoning: the published evidence, from the library's own authors, is that the strategy is cost-negative. Adding a heavy dependency to reproduce a documented loss is not engineering.

### 4.2 Gymnasium / gym-anytrading
- **License:** MIT.
- **Production readiness:** Gymnasium 9/10 as an *interface standard*; the trading envs built on it are 3/10.
- **Recommendation: WRAP the interface, IGNORE the envs.** If ATI ever does RL, implement `Env` against ATI's own `BacktestEngine` (which already does deterministic replay of the live decision path — a genuinely strong foundation). Do not import someone else's simulator with someone else's cost assumptions.
- **Sharpe:** 0 direct; **+0.05 protective** by keeping research-to-live parity through ATI's own replay path.

### 4.3 TensorTrade — https://github.com/tensortrade-org/tensortrade
- **License:** Apache-2.0. 6.6k stars.
- **Production readiness:** 4/10. Requires Python 3.11/3.12 — **will not run on ATI's 3.14 interpreter.** That alone is disqualifying. NumPy pinned `<2.0`. Their own EXPERIMENTS.md documents the same commission-eats-the-edge result as FinRL.
- **Recommendation: IGNORE.**
- **Sharpe: −0.2 to 0.**

### 4.4 Stable-Baselines3 — https://github.com/DLR-RM/stable-baselines3
- **License:** MIT. JMLR-published (Raffin et al., JMLR 22, 2021), ~5,000 citations.
- **Production readiness:** 8/10 — as an RL *algorithm library* it is the best-tested in Python. It is not a trading library and does not pretend to be.
- **Latency:** PPO/SAC policy inference ~0.2–2 ms CPU for small MLPs.
- **Python quality:** Excellent. Typed, documented, benchmarked.
- **Recommendation: WRAP — but only for execution, and only much later.**
- **Sharpe:** 0 standalone; see §4.5.

### 4.5 RL for optimal execution — the one defensible RL use case
Published evidence, ranked by credibility:

| Study | Setting | Result |
|---|---|---|
| Nevmyvaka, Feng & Kearns (2006), UPenn — the seminal work | NASDAQ order books, millions of events | Double-digit % reduction in implementation shortfall vs static submit-and-leave |
| A3C-LSTM, ICDSIC 2025 (ACM DL 10.1145/3788910.3788936) | US equities | IS **−8.32 bps** vs TWAP **−15.47 bps**, VWAP **−12.83**, Almgren-Chriss **−10.91**; Sharpe 1.87 vs TWAP 0.94; completion 98.7% |
| RL-Exec (arXiv:2511.07434) | BTC-USD LOB replay with transient impact, partial fills, fees, latency | PPO beats TWAP and book-liquidity VWAP on per-day protocol |

- **Recommendation: BUILD later, on ATI's own replay engine, using SB3 as the algorithm layer.** Not now.
- **Sharpe: +0.10 to +0.25 additive** *conditional on ATI trading enough size that execution cost is material*. Reasoning: the A3C-LSTM paper's **7.15 bps** improvement over TWAP, applied at 100% annual turnover, is ~14 bps/yr of saved cost. On a 12% vol strategy that is ~+0.012 Sharpe — **negligible**. The number only becomes the +0.10–0.25 figure at 500–1000% annual turnover (i.e. intraday), where saved cost reaches 70–140 bps/yr. **If ATI trades daily or slower, RL execution is worth approximately nothing and should be dropped from the roadmap entirely.** Note also that the Sharpe figures in those papers are *execution-episode* Sharpe, not strategy Sharpe; they are not directly comparable and should not be quoted as such.

---

## 5. Low-latency model serving

Framing: ATI is a **single Python process** with an in-process decision path. Every option in this section except the compilers introduces a **network hop**. Localhost gRPC costs ~50–200 µs round trip — which is **5–20× the entire lleaves inference time**. For ATI, the correct serving architecture is *no server*.

### 5.1 BentoML — https://github.com/bentoml/BentoML
- **License:** Apache-2.0. **Prod:** 8/10. **Latency:** framework overhead ~1–5 ms + model time + network.
- **Python quality:** Very good, clean decorator API.
- **Recommendation: IGNORE.** Solves packaging/deployment for teams shipping models to other teams. ATI has one consumer of its model: itself, in-process.
- **Sharpe:** 0, slightly negative via added latency and ops surface.

### 5.2 Ray Serve — https://docs.ray.io/en/latest/serve/
- **License:** Apache-2.0. **Prod:** 8/10. **Latency:** ~1–10 ms overhead; Ray actor scheduling adds tail variance.
- **Recommendation: IGNORE.** Ray's value is distributed scaling. ATI is not distributed and should not become distributed to serve a 48 KB tree ensemble.

### 5.3 NVIDIA Triton Inference Server — https://github.com/triton-inference-server/server
- **License:** BSD-3. **Prod:** 10/10 — genuinely the best model server that exists.
- **Latency:** Documented per-request breakdown from an ONNX Runtime backend run: *"Avg request latency 1718 µs (overhead 19 µs + queue 184 µs + compute input 72 µs + compute infer 1431 µs + compute output 10 µs)."* Note the **184 µs queue** and **19 µs overhead** — before the network. There are open issues (`server#7677`, `onnxruntime_backend#34`) documenting Triton CPU inference being *slower* than calling ONNX Runtime directly from Python for small models.
- **Python quality:** Client is fine; the server is C++ and configured via `config.pbtxt` protobufs.
- **Recommendation: IGNORE.** For a small tabular model, Triton is between 20× and 200× slower than in-process compiled inference, and adds a container, a model repository, and a protobuf config format. It is the right answer for GPU-batched deep learning at scale and the wrong answer for everything ATI will do this decade.

### 5.4 ONNX Runtime — https://github.com/microsoft/onnxruntime
- **License:** MIT. **Prod:** 9/10.
- **Latency:** **11.0 µs** batch=1 on a GBDT (lleaves benchmark). ~1.86 ms for SqueezeNet-INT8 on Azure Cobalt 100 Arm64 — irrelevant to ATI but useful as an upper bound for anything neural.
- **Python quality:** Very good. In-process `InferenceSession`, no server needed.
- **Recommendation: INTEGRATE — as the portable fallback runtime.** Rationale: lleaves is Linux/macOS-only via conda-forge/pip and LightGBM-only. ONNX Runtime runs everywhere including the Windows dev box this repo lives on, and covers non-tree models. Ship ONNX as the default, lleaves as the Linux production optimisation.
- **Sharpe: +0.02 to +0.05 additive.** Small and honest: the gain is purely reduced decision-to-order latency (~40 µs saved vs native LightGBM per inference). At ATI's decision cadence this is rounding error; the real value is deterministic, dependency-free inference artefacts.

### 5.5 TensorFlow Serving / 5.6 TorchServe
- **License:** Apache-2.0 both. **Prod:** 8/10 and 6/10 (TorchServe's maintenance cadence has visibly slowed).
- **Latency:** Published comparison at batch=8: TorchServe ~15 ms, Triton+TensorRT ~8 ms, Triton+PyTorch ~14 ms — i.e. **three orders of magnitude** above the compiled-tree path.
- **Recommendation: IGNORE both.** ATI has no TensorFlow and no PyTorch models, and should not acquire them for tabular market data.

### 5.7 ⭐ lleaves — https://github.com/siboehm/lleaves
- **License:** MIT. **Prod:** 7/10 — small maintainer base is the main risk; the artefact it produces, however, is a static compiled function with no runtime service to fail.
- **Latency:** **9.61 µs** batch=1 — the fastest option in the table above. Dependencies: `llvmlite` + `numpy` only, LLVM statically linked. **Linux/macOS only.**
- **Python quality:** Good. `lleaves.Model` is a drop-in subset of `LightGBM.Booster`.
- **Recommendation: WRAP.** Put it behind an `IModelRuntime` port in `backend/application/interfaces/` with an ONNX Runtime adapter as the portable sibling. Never let `lleaves` types cross into the domain layer — this is exactly the port/adapter discipline the architecture already enforces for exchanges.
- **Sharpe: included in the +0.15–0.40 of §3.4.** Isolated contribution ~+0.02.

### 5.8 FastAPI for model APIs
- **License:** MIT. Already a dependency. **Prod:** 9/10.
- **Recommendation: IGNORE as a serving layer.** ATI's model is called from inside the same process as the decision engine. Wrapping it in an HTTP endpoint that ATI then calls over localhost would add ~200 µs–1 ms to gain nothing. Keep FastAPI for the operator dashboard and API only, which is what it is already doing.

### 5.9 Timber (AOT C99 compiler) — https://github.com/kossisoroyce/timber
- **License:** verify. Claims **~2 µs** single-sample, ~48 KB artefact, zero runtime deps, MISRA-C output.
- **Recommendation: IGNORE (watch).** Impressive claims, immature project, benchmark methodology is vendor-run. The 7.6 µs it would save over lleaves is worth nothing at ATI's cadence. Revisit only if ATI ever goes sub-millisecond.

---

## 6. ML pipeline orchestration

Framing: ATI has **one pipeline**, run by **one operator**, on **one machine**. Every tool in this category is designed for the opposite.

### 6.1 MLflow — https://github.com/mlflow/mlflow
- **License:** Apache-2.0. **Prod:** 8/10.
- **Known constraints (v3.x, verify against current release before adopting):** model **Stages are deprecated** — migrate to aliases/tags (`@champion`, `@challenger`); **file-based tracking store raises an error by default** in 3.x (needs `MLFLOW_ALLOW_FILE_STORE=true` or a real backend); benchmarks report **SQLite backend locking under ~10+ parallel runs** with 5s+ retries, and REST logging overhead of **~200–400 ms per log call** (~190 ms per scalar in one test). Search over 10,000 runs on SQLite has been reported to time out at 30s.
- **Python quality:** Good, but the client is heavy and pulls a large dependency tree.
- **Recommendation: WRAP — registry and run-metadata only, never the tracking REST server in a hot loop.** Define an `IModelRegistry` port; implement it first as ~150 lines against ATI's existing SQLite (model hash, feature schema hash, train window, purge/embargo params, CPCV path stats, DSR, promotion decision, operator approval). Swap in MLflow behind that port only if the hand-rolled version proves inadequate. Given the 200–400 ms/call overhead and SQLite locking, the hand-rolled version will probably win.
- **Sharpe: 0 direct; +0.05 protective** — via the ability to answer "which exact model made this trade, trained on what, validated how" during an incident. That capability is mandatory; MLflow is merely one way to get it.

### 6.2 ZenML — https://github.com/zenml-io/zenml
- **License:** Apache-2.0. **Prod:** 7/10. Good decorator-based pipeline API, strong integration story.
- **Recommendation: IGNORE.** ZenML's own docs concede adoption "is a bigger commitment than using a lightweight tool like DVC. You have to structure your code as ZenML pipelines/steps." Restructuring ATI's Clean Architecture around a third-party pipeline abstraction would invert the dependency rule — the framework would own the application layer. That is an architectural regression regardless of the tool's quality.

### 6.3 Kubeflow
- **License:** Apache-2.0. **Prod:** 8/10 on Kubernetes; **1/10 for ATI**, which has no Kubernetes and must not acquire one.
- **Recommendation: IGNORE.**

### 6.4 DVC — https://github.com/iterative/dvc
- **License:** Apache-2.0. **Prod:** 8/10. Git-native, no server required, content-addressed `dvc repro`.
- **Recommendation: IGNORE now; the *lightest* plausible later addition in this category.** It has no approval state machine, so it complements rather than replaces a registry. ATI's datasets currently fit in a SQLite file that git can track directly.
- **Sharpe:** 0 direct; +0.03 protective at multi-GB dataset scale.

### 6.5 Airflow / Prefect / Dagster
- **License:** Apache-2.0 (all three).
- **Recommendation: IGNORE all three.** ATI needs a scheduled retrain job. That is `asyncio` + a cron entry, or at most APScheduler. Airflow is a scheduler with a database, a webserver, and an executor; installing it to run one nightly job is the definition of unnecessary architectural layering.
- **Sharpe:** 0, negative on operational complexity.

---

## 7. Time-series cross-validation — **the highest-value category in this document**

### 7.1 ⭐ purgedcv — https://github.com/eslazarev/purged-cross-validation — **RANK #1**
- **License:** MIT. Docs: https://eslazarev.github.io/purged-cross-validation/. PyPI `purgedcv`, also conda-forge. JOSS paper in `paper/paper.md`. Listed in Awesome Quant.
- **Production readiness:** 7/10. Young (created 2026-05) and small (~22 stars) — that is the honest risk. Mitigating: **354 tests, 98% line coverage, mypy-checked, ruff-linted, CI + codecov**, and the surface area is small enough to vendor if the project stalls. Implements de Prado (2018) and Bailey & López de Prado (2012, 2014) checked against the original papers.
- **API:** `purge`, `apply_embargo`, `WalkForwardSplit` (expanding/sliding), `PurgedKFold`, `PurgedGroupKFold`, `CombinatorialPurgedCV` (C(N,K) with backtest-path reconstruction), plus **Probabilistic, Deflated, and Minimum-Track-Record Sharpe**. Standard sklearn splitter protocol — drops into `cross_val_score`, `GridSearchCV`, `Pipeline`.
- **Latency:** N/A — offline. CPCV(6,2) = 15 folds ⇒ 15× training cost. Budget for it.
- **Python quality:** **Excellent.** The best-engineered library in this entire document.
- **Recommendation: INTEGRATE. First. Before any model exists.**
- **Sharpe: +0.6 to +1.5 protective — the largest single number here.** Reasoning, with the library's own evidence:
  - On a pure-noise target, `KFold(shuffle=True)` reports **R² = +0.92** with 100% train/test label overlap; `PurgedKFold` correctly returns no skill.
  - On earthquake magnitudes (unpredictable by Gutenberg–Richter, empirical autocorrelation +0.02), naive shuffled k-fold still prints **R² = +0.65**; purged (−0.75), blocked (−1.13) and walk-forward (−1.24) all correctly report no skill.
  - On BTC data with six candidate models, once the **Deflated Sharpe Ratio** corrects for the number of trials, **no model clears DSR ≥ 0.95** — and the library's docs state plainly that *"Reporting no edge is the correct outcome."*
  - The protective value is therefore not "0.6–1.5 better returns." It is: **the difference between deploying capital on a phantom edge and not deploying it.** A strategy with a true Sharpe of 0 that backtests at 1.2 and gets deployed does not earn 0 — it earns *negative*, after costs and slippage. Preventing one such deployment is worth more than every other item in this document combined.
- **Constitutional note:** this belongs in `docs/Constitution` territory. "No model reaches the decision path without a CPCV path distribution and a DSR ≥ threshold, approved by the operator" is a **risk rule**, not a tooling preference.

### 7.2 timeseriescv — https://pypi.org/project/timeseriescv · https://github.com/sam31415/timeseriescv
- **License:** MIT (verify). **Prod:** 5/10 — v0.2, essentially unmaintained since ~2019.
- **API:** `PurgedWalkForwardCV`, `CombPurgedKFoldCV`. Correct algorithms, minimal packaging.
- **Recommendation: IGNORE** in favour of purgedcv. Keep as a **cross-check implementation**: run both on the same split spec and assert identical index sets. That is a cheap, high-value test for the one component ATI cannot afford to get wrong.

### 7.3 mlfinlab — https://github.com/hudson-and-thames/mlfinlab
- **License:** ⚠️ **NOT OPEN SOURCE. All rights reserved, Hudson & Thames.** The student licence states *"under no circumstances may the codebase be used for any commercial purposes"* and the codebase *"may not be reverse engineered or used to create a competitor product."* Commercial tier: **£100 +VAT per user per month.** The licence also discloses **webhooks that fire on installation and during specific function calls**, tracking installations, daily usage, function calls and source-code modifications.
- **Additional signal:** the public GitHub mirror has method bodies replaced with `pass` — the visible repo is a stub.
- **Production readiness:** 7/10 as code; **0/10 as an ATI dependency.**
- **Recommendation: IGNORE — hard prohibition.** Using it commercially without a licence is infringement; using it *with* a licence puts phone-home telemetry inside a trading system. Both violate this repo's security standards. Read the book, implement the algorithms.

### 7.4 skfolio — https://skfolio.org · https://pypi.org/project/skfolio
- **License:** BSD-3.
- **Production readiness:** 8/10. Actively maintained, sklearn-native.
- **Relevant surface:** Walk Forward, **Combinatorial Purged Cross-Validation**, Multiple Randomized CV, online predict/score, online grid search, plus a full portfolio-optimisation stack (HRP, Black-Litterman, entropy pooling, vine copulas).
- **Recommendation: WRAP (later, portfolio layer only).** Its CPCV overlaps purgedcv; its distinctive value is portfolio construction, which ATI does not need until it holds >5 concurrent positions.
- **Sharpe: +0.05 to +0.15 additive** *at the portfolio layer only*, via HRP/denoised covariance beating naive equal-weight. Zero for a single-position system.

---

## 8. Regime detection & non-stationarity

### 8.1 ⭐ hmmlearn — https://github.com/hmmlearn/hmmlearn — **RANK #4 (joint)**
- **License:** BSD-3 (commercially friendly, explicitly).
- **Production readiness:** 7/10. v0.3.3, vectorised NumPy/SciPy core, sklearn-style API. Documented use in real-time finance pipelines; supported as a first-class library in QuantConnect's research environment.
- **Latency:** Fit of a 5-state Gaussian HMM on tens of thousands of observations: **under a second** on a laptop. Online Viterbi/forward step for a new observation: **~0.1–1 ms**. Guidance for production: `covariance_type='diag'` for high-dimensional features, disable verbose logging.
- **Python quality:** Good. Clean `fit`/`predict`/`predict_proba`/`score`.
- **Recommendation: INTEGRATE.** Concretely: a 3–4 state Gaussian HMM over (return, realised vol, volume z-score) producing a **posterior probability vector**, not a hard label. Feed the posterior into position sizing.
- **Sharpe: +0.10 to +0.30 additive.** Reasoning: regime-conditional sizing (full size in the identified favourable state, reduced or flat in the adverse state) is the cheapest known drawdown-reduction technique. It typically cuts max drawdown by 20–35% while cutting return by 10–20% — a net Sharpe gain because the vol reduction outpaces the return reduction. **Caveat I will not hide:** HMM regime labels are only reliable in-sample; the Viterbi path *revises history* as new data arrives, so any backtest that uses smoothed (full-sample) states is catastrophically look-ahead-biased. **Only filtered (causal, forward-algorithm) posteriors may enter the decision path.** Getting this wrong turns +0.3 into a phantom +1.5.
- **Reference implementation to study (not import):** `wisdomgu/regim` — 4-state Gaussian HMM with BIC covariance selection, 40 random seeds, min 3% state occupancy, PELT changepoints vs HMM Viterbi lead/lag, regime-conditional GARCH(1,1), SHAP on HMM posteriors. Good methodology, tiny project.

### 8.2 ⭐ ruptures — https://github.com/deepcharles/ruptures — **RANK #4 (joint)**
- **License:** BSD-2 (verify).
- **Production readiness:** 7/10. Mature, well-documented offline changepoint detection (PELT, BinSeg, Window, Dynp).
- **Latency:** PELT is roughly linear-to-`O(n log n)` in practice; seconds on 10⁴–10⁵ points. **Offline only** — this is a research/validation tool, not a live signal.
- **Python quality:** Good, clean, well-documented.
- **Recommendation: INTEGRATE — for research validation, not the live path.** Its real job: independently verify that the HMM's state boundaries correspond to actual structural breaks, and segment history for regime-stratified backtesting.
- **Sharpe:** counted jointly with hmmlearn above. Standalone additive contribution ~0; protective contribution meaningful (prevents fitting one model across a structural break).

### 8.3 Bayesian Online Changepoint Detection (BOCD, Adams & MacKay 2007)
- **Availability:** in `frouros`; also standalone MIT implementations.
- **Recommendation: INTEGRATE via frouros** if a *causal* changepoint signal is wanted. BOCD is genuinely online, unlike ruptures.
- **Sharpe:** folded into the drift/regime allocation above.

### 8.4 ADWIN (Bifet & Gavaldà 2007) — https://riverml.xyz/dev/api/drift/ADWIN/
- **License:** BSD-3 (via River).
- **Production readiness:** 8/10. Mathematically grounded (adaptive windowing with guarantees), tunable via `delta` (default 0.002), `clock` (default 32), `grace_period`.
- **Latency:** Negligible — amortised bucket merging, **single-digit µs** per update.
- **Recommendation: INTEGRATE.** This is the **highest value-per-line-of-code item in the entire document.** Roughly 10 lines: feed the model's rolling prediction error into ADWIN; on `drift_detected`, do not silently retrain — **raise an operator alert and optionally de-risk.** That last clause matters: per the Constitution, learning must never alter risk parameters without human approval.
- **Sharpe: +0.05 to +0.15 protective.** Reasoning: the value is in *time-to-detection* of model decay. A model that decays and keeps trading for three months before anyone notices can give back a year of gains. ADWIN reduces that detection lag from months to days.

### 8.5 frouros — https://github.com/IFCA-Advanced-Computing/frouros
- **License:** BSD-3. Published in SoftwareX 26 (2024), DOI 10.1016/j.softx.2024.101733; arXiv:2208.06868.
- **Production readiness:** 7/10. >90% coverage, flake8/pylint/black/mypy. **28 drift methods** — the most comprehensive in Python (vs River 7, MOA 14, Menelaus 13, Alibi Detect 9, TorchDrift 4). Covers both concept drift (ADWIN, BOCD, CUSUM, DDM, EDDM, HDDM-A/W, KSWIN, Page-Hinkley, RDDM, STEPD…) and data drift (MMD, PSI, KS, χ², PCA-CD…).
- **Latency:** Streaming detectors update one sample at a time; µs-scale. The authors note performance optimisation (Cython) is future work.
- **Python quality:** Very good; framework-agnostic with a callbacks system.
- **Recommendation: REJECTED (correction 2026-08-12).** Requires **Python < 3.13**, a hard blocker on ATI's CPython 3.14 interpreter. The 2026-08-12 edge-library review (`experiments/decisions/ml_edge_library_validation_2026.md` §6) supersedes this entry: `river.drift.ADWIN` is the drift-detection choice, with ATI's own `adwin.py` (stdlib-only ADWIN0) as the zero-dependency fallback. Frouros stays out on a Python-version incompatibility, not a quality judgment.
- **Sharpe:** same envelope as ADWIN; do not double-count.

### 8.6 Alibi Detect — https://github.com/SeldonIO/alibi-detect
- **License:** ⚠️ **"source-available"**, not OSI open source (verify current terms; Seldon relicensed). Requires TensorFlow *or* PyTorch backends for drift detection — a multi-hundred-MB dependency for a statistical test.
- **Recommendation: IGNORE.** Licence ambiguity plus deep-learning dependency weight, for capability frouros provides under BSD-3 with numpy/scipy.

---

## 9. Feature importance for non-stationary data

### 9.1 SHAP — https://github.com/shap/shap
- **License:** MIT. **Prod:** 8/10.
- **Latency:** TreeSHAP computes **exact** Shapley values for tree ensembles in polynomial time — `O(TLD²)` in trees × leaves × depth. Practically **~0.1–10 ms per row** for a 200-tree ensemble. KernelSHAP is orders of magnitude slower and model-agnostic. **Offline use only** — never in the decision path.
- **Python quality:** Good API, heavy dependency tree, some maintenance lag.
- **Recommendation: INTEGRATE — offline diagnostics only, with a hard caveat.**
- **The caveat, which is the substance of this category:** SHAP's additivity guarantees assume **feature independence**. Financial time series violate this comprehensively via autocorrelation and cross-correlation. J.P. Morgan AI Research documented exactly this (arXiv:2210.02176, ICAIF'22 XAI workshop): standard KernelSHAP is inappropriate for time series, and they propose **VARSHAP** (fitting a Vector Autoregressive surrogate instead of a linear one, proven to converge to SHAP) and **Time Consistent Shapley values**, noting the two "yield dramatically different results." Their own conclusion is that model-agnosticism "may lead to unrefined explanations that fail to capture important aspects of the model behaviour."
- **Practical rule for ATI:** use TreeSHAP for *ranking* and *sanity-checking sign* ("does this feature push the way economics says it should?"), never for *feature selection* on correlated market features. For selection, use clustered/grouped importance so that a family of correlated features is evaluated jointly rather than having their importance split and each member individually discarded.
- **Sharpe: 0 direct; +0.05 to +0.10 protective** — catches the class of bug where a leaked or degenerate feature (a timestamp counter, a forward-filled label proxy) dominates the model.

### 9.2 Permutation importance with purging
- **Availability:** `sklearn.inspection.permutation_importance` + purgedcv splits.
- **Recommendation: BUILD** — ~40 lines. Run permutation importance *inside each purged CV fold* (de Prado's MDA), not on a single holdout. Naive permutation importance on overlapping labels is as leaky as naive k-fold.
- **Sharpe:** folded into purgedcv's protective figure.

### 9.3 Feature selection for financial time series
- **Recommendation: BUILD, minimal.** Three rules, no library:
  1. **Fractional differentiation** before selection — make features stationary while preserving memory.
  2. **Cluster correlated features** and select at cluster level.
  3. **Cap the feature count hard** (≤20 for the first model). Every additional feature deflates the DSR.
- **Sharpe: +0.05 to +0.15 additive**, purely via variance reduction from not overfitting a wide feature matrix on a short history.

---

## 10. Signal combination / stacking / meta-labelling

### 10.1 ⭐ Triple-barrier + meta-labelling — **RANK #2 — BUILD**
- **What it is:** Primary model emits side ∈ {−1, 0, +1}. Labels come from the **triple-barrier method**: profit-take barrier, stop-loss barrier, and a vertical (time) barrier, with horizontal barriers **scaled by point-in-time volatility**. A **secondary** classifier then predicts P(the primary signal succeeds), and that probability drives **size**, not direction.
- **Reference implementations:** `mlfinpy` (https://github.com/baobach/mlfinpy, MIT, docs at mlfinpy.readthedocs.io) — a clean-room reimplementation built explicitly because *"MlFinLab ... is closed-source and I believe in the power of open source projects."* Alpha-stage, one maintainer, requires Python <4.0 ≥3.11.
- **Production readiness:** mlfinpy 5/10 (alpha, single maintainer). **A first-principles implementation in ATI is ~300 lines** and is the recommendation.
- **Latency:** Labelling is offline. Inference is one extra GBDT call: **+10 µs**.
- **Recommendation: BUILD, referencing mlfinpy and the book. Do not depend on mlfinlab (§7.3).**
- **Sharpe: +0.10 to +0.25 additive.** Evidence, ranked by how much I trust it:
  - **Most credible:** Hudson & Thames / WorldQuant University MSFE capstone on S&P 500 E-mini futures — event-based sampling + triple-barrier + meta-labelling improved trend-following and mean-reverting strategy performance. Peer-reviewed-adjacent, real data, real methodology.
  - **Moderately credible:** a widely-cited SPY momentum study (2010–2024, 0.05% costs) reports Sharpe **0.51 → 0.61 (+20%)** and max drawdown **−24.1% → −18.2% (+25%)**, with win rate *falling* 52.3% → 48.1%. That last figure is the tell that the mechanism is real: meta-labelling improves risk-adjusted return by **sizing**, not by predicting direction better.
  - **Treat with suspicion:** claims of accuracy going "20% → 77%" or "17% → 63%". These are classification accuracy on a re-defined, filtered, heavily imbalanced label set — they do not translate to Sharpe and should never be quoted as if they do.
  - **Consolidated estimate:** the "+15–25% Sharpe improvement" figure recurs across independent sources and is consistent with the mechanism. On a 0.8 base that is **+0.12 to +0.20**. I am using +0.10 to +0.25 to reflect implementation risk.
- **Why this ranks #2:** it is the single technique with the best evidence-to-effort ratio in quantitative finance, it requires no new runtime dependency, and it maps *perfectly* onto ATI's existing architecture — the primary signal is the AI reasoner's decision, the meta-model is a confidence gate that sits naturally in front of the existing risk gate. It makes the AI-as-trader design *more* defensible, not less.

### 10.2 Ensemble methods / model stacking
- **Availability:** `sklearn.ensemble.StackingClassifier`; `river` has online bagging/boosting/stacking.
- **Recommendation: BUILD minimal (simple averaging), IGNORE complex stacking.** Rationale: stacking requires a *second* level of cross-validation to generate out-of-fold predictions, and doing that correctly under purging and embargo is subtle and easy to get wrong. Rank-average or inverse-variance-weight 2–3 diverse models instead.
- **Sharpe: +0.05 to +0.15 additive** from decorrelated model averaging — the classic `1/√n` variance reduction, capped in practice by how correlated the models actually are (usually very).

### 10.3 Signal blending
- **Recommendation: BUILD.** Volatility-target the blended signal. This is arithmetic, not a library.

---

## 11. Backtesting ML strategies

### 11.1 ATI's existing `BacktestEngine`
- **Already implemented:** deterministic replay of the live decision path (commit `483e836`). This is the correct architecture and is worth more than any external engine, because **research-to-live parity is a property of using the same code path**, not of using a good framework.
- **Recommendation: KEEP and extend** with (a) purged/CPCV split generation, (b) explicit transaction cost + slippage model, (c) CPCV path reconstruction so the output is a *distribution* of equity curves rather than one line.
- **Sharpe: +0.2 to +0.5 protective** for the cost model alone. FinRL's own numbers make the case: agent **+$239 at 0% commission → −$650 at 0.1%**. A backtest without explicit costs is not a backtest.

### 11.2 NautilusTrader — https://github.com/nautechsystems/nautilus_trader
- **License:** LGPL-3.0 (verify — LGPL linking obligations matter for a proprietary system). v1.228.0 as of June 2026, very actively maintained.
- **Production readiness:** 9/10. Rust core, nanosecond resolution, event-sourced replay, configurable fill/fee/latency/order-book models, SLSA Build Level 3 provenance, research-to-live parity by design.
- **Recommendation: IGNORE now — but this is the one candidate that could justify replacing ATI's own engine later.** Adopting it means adopting its domain model, which would conflict with ATI's existing immutable domain entities. That is a rewrite, not an integration. Revisit only if ATI goes multi-venue intraday.
- **Sharpe: 0 now; +0.1 to +0.3 protective** at intraday multi-venue scale via realistic partial-fill and queue-position modelling.

### 11.3 vectorbt — https://github.com/polakowo/vectorbt
- **License:** Apache-2.0 (OSS) — **but the OSS version is in maintenance mode; active development moved to vectorbt PRO (~$25/mo commercial).**
- **Production readiness:** 6/10 (OSS). Extremely fast parameter sweeps via NumPy/Numba/Rust.
- **Recommendation: IGNORE — and note the danger.** vectorbt's core strength, running thousands of parameter combinations in seconds, is an **overfitting machine** in a system without a multiple-testing correction. Every extra trial deflates the DSR. Adopting it before purgedcv's DSR is in place would be actively harmful.
- **Sharpe: −0.3 to +0.1.** Genuinely negative-skewed for an undisciplined user.

### 11.4 PyBroker — https://github.com/edtechre/pybroker
- **License:** Apache-2.0. Actively maintained.
- **Production readiness:** 7/10. Notably: **walk-forward analysis is the default path, not an add-on**, plus bootstrapped confidence intervals instead of point estimates, NumPy/Numba accelerated.
- **Recommendation: IGNORE (study).** It is the closest external framework to what ATI needs, and its API is worth reading before extending ATI's own engine. But importing it duplicates the engine ATI already has.

### 11.5 backtesting.py — https://github.com/kernc/backtesting.py
- **License:** ⚠️ **AGPL-3.0.** For a networked trading service, AGPL's §13 network-use clause is a serious consideration. **IGNORE on licence grounds alone.**

### 11.6 backtrader / Zipline-Reloaded
- backtrader: **effectively frozen since ~2023.** IGNORE.
- Zipline-Reloaded: active community fork, US-equity-factor shaped. IGNORE (wrong asset class shape for ATI).

### 11.7 Microsoft Qlib — https://github.com/microsoft/qlib
- **License:** MIT. ~44–47k stars, 2,065 commits, Microsoft Research.
- **Production readiness:** 7/10. Full pipeline (data → features → model zoo → backtest → online serving), **point-in-time data as a first-class structure**, Alpha158/Alpha360 feature sets, DDG-DA meta-learning for regime adaptation, RD-Agent for LLM-driven factor mining, nested decision framework for hierarchical execution.
- **Latency:** Research platform. Backtesting 10 years of CSI300 on a 32-core server ≈ 15 min.
- **Python quality:** Good but opinionated — Qlib wants to own your data layout, your feature expressions, and your workflow.
- **Recommendation: IGNORE as a dependency; STUDY as the best available reference architecture.** Specifically worth stealing: (a) the point-in-time data structure design, (b) the Alpha158 feature taxonomy as a menu of candidate features, (c) the online-serving/model-rolling design. Adopting Qlib wholesale would mean ATI becomes a Qlib deployment, and Qlib's architecture is China-A-share-and-equities shaped.
- **Sharpe:** 0 as dependency; the *ideas* are worth a meaningful fraction of the +0.15–0.40 model figure.

---

## 12. Recommended sequence for ATI

Ordered by dependency, not by excitement. Each phase gated on the previous.

**Phase 0 — Prerequisite (no ML at all)**
Add `numpy`, `pandas`, `scikit-learn` to `requirements.txt`. Verify all three build on CPython 3.14 with `py -3`. Extend SQLite persistence with `event_time`/`ingest_time` bi-temporal columns. Accumulate ≥6 months of live observation history. **No model may be fitted before this exists.**

**Phase 1 — Validation before modelling (purgedcv)**
Integrate `purgedcv`. Write an ADR making purged walk-forward + CPCV + DSR ≥ threshold a *risk rule* under the Constitution. Add a cross-check test against `timeseriescv`. Build the cost/slippage model into the existing `BacktestEngine`.

**Phase 2 — Labelling (triple-barrier + meta-labelling)**
Build volatility-scaled triple-barrier labelling and the meta-labelling secondary model. Reference mlfinpy and the book; depend on neither. The meta-model's output feeds sizing, in front of the existing risk gate.

**Phase 3 — Model + serving (LightGBM → ONNX / lleaves)**
LightGBM primary and secondary models, ≤20 features, full retrains only. Define an `IModelRuntime` port; ONNX Runtime adapter (portable, 11 µs) plus an lleaves adapter (Linux, 9.6 µs). Hand-rolled SQLite model registry behind an `IModelRegistry` port.

**Phase 4 — Regime + drift (hmmlearn, ruptures, ADWIN)**
Causal (filtered, never smoothed) HMM posteriors into position sizing. `ruptures` for offline validation of regime boundaries. ADWIN on rolling prediction error → **operator alert**, never an autonomous risk-parameter change.

**Phase 5 — Reassess. Do not plan it now.**
Everything else — RL execution, feature stores, orchestrators, model servers, portfolio optimisation — is deferred to a decision made with data ATI does not yet have.

---

## 13. Explicit rejections and why

| Rejected | Primary reason |
|---|---|
| mlfinlab | Proprietary, £100/user/mo, no-commercial-use student tier, **phone-home webhooks in a trading system** |
| ArcticDB | **BSL 1.1 — production use requires a paid licence**; commonly missed |
| Alibi Detect | Source-available (not OSI), TF/PyTorch dependency weight |
| backtesting.py | AGPL-3.0 network-use clause |
| Hopsworks | AGPL-3.0 community edition; legal review required |
| TensorTrade | **Python 3.11/3.12 only — cannot run on ATI's 3.14** |
| FinRL | Authors label it educational; own results are cost-negative |
| Tecton | Proprietary, mid-acquisition by Databricks, live path must not depend on vendor SLA |
| Triton / BentoML / Ray Serve / TF Serving / TorchServe | Network hop costs 5–200× the entire inference time for a small tabular model |
| Airflow / Prefect / Dagster / Kubeflow / ZenML | Solve multi-team, multi-pipeline, multi-node problems ATI does not have; ZenML would invert the dependency rule |
| vectorbt | Parameter-sweep speed is an overfitting accelerator absent a DSR gate |
| nevergrad | Same, plus the wrong tool for a small hyperparameter space |
| Feast / FeatureBase / HopsFS | Solve organisational skew; ATI's `FeatureRegistry` already enforces single-definition |
| scikit-multiflow | Superseded by River, by its own authors |
| backtrader | Frozen since ~2023 |

---

## 14. Open questions for the operator

1. **What is ATI's intended holding period?** This single answer determines whether §4.5 (RL execution) is worth +0.25 Sharpe or ~+0.01. Everything intraday-flavoured in this document is contingent on it.
2. **Will ATI run on Linux in production?** If yes, lleaves is viable and ONNX is a dev-box fallback. If Windows, ONNX Runtime is the only option and the lleaves adapter should not be built.
3. **Does River publish CPython 3.14 wheels?** If not, `river.drift.ADWIN` still wins; the fallback is ATI's own `adwin.py` (stdlib-only). Frouros is **not** the fallback — it requires Python <3.13 and is hard-blocked on ATI's 3.14 interpreter (corrected 2026-08-12, see `experiments/decisions/ml_edge_library_validation_2026.md` §6–7).
4. **Is there an appetite for a Constitution amendment** making purged CV + DSR a mandatory risk gate rather than an engineering preference? I believe there should be, and that it is the highest-value change available to this repository today.

---

## 15. Sources

Repositories: purgedcv `eslazarev/purged-cross-validation` · River `online-ml/river` · frouros `IFCA-Advanced-Computing/frouros` · lleaves `siboehm/lleaves` · hmmlearn `hmmlearn/hmmlearn` · ruptures `deepcharles/ruptures` · Qlib `microsoft/qlib` · NautilusTrader `nautechsystems/nautilus_trader` · FinRL `AI4Finance-Foundation/FinRL` · FinRL-Trading `AI4Finance-Foundation/FinRL-Trading` · TensorTrade `tensortrade-org/tensortrade` · Stable-Baselines3 `DLR-RM/stable-baselines3` · mlfinpy `baobach/mlfinpy` · mlfinlab `hudson-and-thames/mlfinlab` · skfolio `skfolio.org` · ArcticDB `man-group/ArcticDB` · vectorbt `polakowo/vectorbt` · timeseriescv `sam31415/timeseriescv` · regim `wisdomgu/regim`

Papers: Montiel et al., *River: machine learning for streaming data in Python*, JMLR 22 (2021) · Raffin et al., *Stable-Baselines3*, JMLR 22 (2021) · Céspedes Sisniega & López García, *Frouros*, SoftwareX 26 (2024), doi:10.1016/j.softx.2024.101733 · Villani, Lockhart & Magazzeni (J.P. Morgan AI Research), *Feature Importance for Time Series Data: Improving KernelSHAP*, arXiv:2210.02176, ICAIF'22 · Nevmyvaka, Feng & Kearns, *Reinforcement Learning for Optimized Trade Execution* (2006) · *Algorithmic Trading Execution Optimization Using A3C-LSTM*, ICDSIC 2025, doi:10.1145/3788910.3788936 · Duflot & Robineau, *RL-Exec*, arXiv:2511.07434 · López de Prado, *Advances in Financial Machine Learning* (Wiley, 2018) · Bailey & López de Prado (2012, 2014), PSR/DSR

Benchmarks: siboehm/lleaves NYC-taxi & MTPL2 (i7-4770, min over 20,000 runs) · NVIDIA Triton optimization docs · Arm/Azure `onnxruntime_perf_test` on Cobalt 100 · Hudson & Thames / WorldQuant University MSFE meta-labelling capstone
