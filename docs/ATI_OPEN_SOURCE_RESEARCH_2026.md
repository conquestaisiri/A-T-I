# ATI Open-Source Research Dossier 2026 — External Integration Candidates

> Source: operator-directed external research (ChatGPT session, 2026-08-23,
> `chat gpt ATI SYSTEM.docx`). Persisted here as the canonical research
> reference per the constitution's integration rules: every external project
> must EARN its way into ATI through the pipeline
> DISCOVER → INSPECT → UNDERSTAND → COMPARE → LICENSE-CHECK → PROTOTYPE →
> BENCHMARK → ABLATION → OOS → WALK-FORWARD → COST → REGIME → ROBUSTNESS →
> PROMOTE OR REJECT.
>
> Core principle (verbatim from the dossier): do not tell the agent
> "install X" — tell it "investigate whether X exposes weaknesses in ATI".

## Part 1 — Already covered by ATI-native code (no integration needed)

| Dossier item | Score | ATI already has | Verdict |
|---|---|---|---|
| ruptures (change-point) | 86% | `regime_detector._cusum_changepoints` + HMM | Study their algorithms for accuracy comparison later; no dependency |
| River (drift detection) | 91% | `adwin.py` ADWIN drift detector + edge_monitor verdict ladder | Same algorithm family (ADWIN). Compare implementations, don't import |
| MAPIE (uncertainty) | 90% | `signal_calibration.py` (Brier/ECE) + `prediction_uncertainty.py` (confidence ± gap bands) | Same conformal spirit. Abstention-on-wide-band is the natural next wire-in |
| skfolio (portfolio) | 92% | `portfolio_risk.py` HRP/CVaR (riskfolio) + capital_allocator | Consider swapping engine later behind same port; not urgent |
| ML4T backtest | 82% | `simulator_validation.py` vs square-root law (CONSISTENT, r=0.97) | Independent-simulator challenge is the same idea at larger scale |
| VectorBT sweeps | 68% | Purged walk-forward evaluator | Research accelerator concept valid; defer |

## Part 2 — Genuinely new value (ranked integration queue)

### Q1. NautilusTrader parity laboratory (score 96, LGPL-3.0)
**Question to investigate:** can an independent Rust-native event-driven engine,
fed identical bars/orders/fees, expose weaknesses in ATI's replay semantics?
**Why it matters:** we validated fills against a math model (square-root law);
an independent ENGINE is a stronger adversary.
**License handling:** LGPL — isolated tool/venv, never linked into `backend/`;
parity reports are data, not code.
**First task:** feasibility spike — `pip install nautilus_trader` in an
isolated venv, replay btcusdt-1h v1 through both engines, diff equity curves.

### Q2. Hummingbot execution intelligence (score 94, Apache-2.0)
**Question:** which executor-lifecycle concepts (sliced limit entry, cancel-on-
signal-weaken, aggress-if-alpha > cost) reduce ATI's execution cost?
**ATI gap:** PaperFillEngine is market-order-only; no passive execution policy.
**First task:** design doc — `ExecutionPolicy` port (passive/adaptive slicing)
behind OrderGateway; simulate cost delta on real bars before any venue use.

### Q3. Forecast ensemble disagreement (Chronos-2 / TimesFM, Apache-2.0)
**Question:** does multi-model disagreement improve calibration/abstention?
**ATI fit:** plugs into prediction_uncertainty — disagreement widens the band;
wide band + costs > edge ⇒ abstain. Never a crystal ball.
**First task:** offline study on btcusdt-1d: does disagreement correlate with
fold losses?

### Q4. DSPy reasoner optimization (score 89, MIT)
**Question:** does metric-driven prompt optimization improve structured
reasoning without overfitting?
**Constraint:** SYSTEM_PROMPT is pinned (v1) by prompt-determinism tests; any
optimized prompt ships as PROMPT_VERSION v2 behind the same determinism suite,
validated on locked OOS folds, never the test set.

### Q5. tsfresh feature discovery (score 79)
Research-sidecar only: generate → filter → cluster → purged CV → keep stable.
Never feed raw generated features into the live feature registry.

### Q6. TradingAgents organizational pattern (score 84)
Steal the shape, not the code: analyst roles produce EVIDENCE items into the
proposal's supporting_evidence — the reasoner already consumes evidence lists.

## Part 3 — Explicit rejections (dossier agrees)

- Freqtrade/FreqAI: GPLv3 — study concepts only, zero code/dep.
- OpenBB: AGPLv3 — at most an offline research sidecar, never core data path.
- FinRL/Backtrader/QSTrader: below bar or superseded.

## Sequencing (operator-approved order)

Finish current evidence work first; then:
Nautilus parity lab → Hummingbot execution-policy design → forecast-ensemble
disagreement study → DSPy v2 experiment → tsfresh research sweep.

Each item lands as: spec doc → isolated prototype → benchmark vs ATI-native →
ablation/OOS/cost/regime gates → promote as ADR or reject with findings logged.
