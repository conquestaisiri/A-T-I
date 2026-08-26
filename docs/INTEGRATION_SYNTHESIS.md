# Integration Synthesis & Profit Roadmap

**Date:** 2026-08-09
**Purpose:** Consolidate 7 parallel research streams into one profit-ranked integration roadmap.
**Method:** 7 research agents covered — ultra-low-latency data infra, free/cheap data sources, alternative data, execution & order routing, risk/portfolio engineering, ML for alpha, market microstructure. Each candidate was evaluated against the Integration Constitution decision matrix (INTEGRATE / WRAP / BUILD / FORK / IGNORE) with profitability as the primary ranking signal.

---

## Executive Summary

ATI has a working core: 276 tests green, deterministic observation→context→reason→risk→simulate→reflect pipeline, PydanticAI reasoner, CCXT unified venue adapter. The research confirms three strategic facts:

1. **The cheapest alpha is free.** GDELT+FinBERT (Sharpe 4.65-5.87, free), SEC EDGAR insider data (free), exchange WebSocket feeds (free), and Binance data archives (free) form a world-class data stack at zero cost.
2. **Slippage is unmeasurable today.** ATI's `ExecutionReport` has no fee, venue, or maker/taker field. No execution algorithm can be validated. Measurement must ship before optimization.
3. **Microstructure needs L2 deltas, not snapshots.** Order Flow Imbalance (the highest-R² microstructure signal, 65-87% variance explained) requires event-level L2 deltas. Our current `watch_order_book` returns snapshots. This is the single highest-leverage prerequisite fix.

**The path to top-10 is not more features — it's better execution, better data, and honest measurement.** Every recommendation below is ranked by its expected contribution to risk-adjusted profit.

---

## Part I — Ranked Integration Roadmap

### Tier 1 — Immediate (zero cost, direct profit impact)

| # | Initiative | Source | Est. Impact | Effort | What it does |
|---|---|---|---|---|---|
| 1 | **GDELT + FinBERT sentiment** | Alt-data agent | Sharpe +1.0-2.0 on macro | 2 days | Free 15-min global news → sentiment score → macro alpha feature. Academic Sharpe 4.65-5.87. |
| 2 | **SEC EDGAR insider/13F parsing** | Alt-data agent | IR +0.5-1.0 | 2 days | Free `edgartools` (MIT) → insider trading & institutional holdings → contrarian signal. |
| 3 | **Execution measurement** | Execution agent | Unlocks everything | 3 days | Extend `ExecutionReport` with fee, venue, is_maker, arrival_price. Without this, no slippage number is falsifiable. |
| 4 | **Maker/taker + post-only routing** | Execution agent | 4-5 bps/fill saved | 2 days | `time_in_force` + `post_only` on CCXT orders. At current size this beats VWAP/SOR/TCA combined. |
| 5 | **Fractional Kelly position sizing** | Risk agent | Cuts max drawdown 50%→20% | 1 day | Replace fixed sizing with half-Kelly on the risk gate. Single highest-impact risk change. |
| 6 | **Hierarchical Risk Parity** | Risk agent | -30% max drawdown | 3 days | `riskfolio-lib` (BSD) wrapped behind a portfolio port. Robust to estimation error. |
| 7 | **CVaR tail-risk optimization** | Risk agent | -25-40% tail risk | 3 days | Rockafellar-Uryasev via `cvxpy`. Used by Citadel/Two Sigma. Convex LP, fast. |
| 8 | **L2 delta ingestion fix** | Microstructure agent | Enables OFI | 3 days | Replace snapshot `watch_order_book` with incremental L2 deltas. Begin recording deltas to disk TODAY (unrecoverable otherwise). |

### Tier 2 — Near-term (weeks, moderate cost or complexity)

| # | Initiative | Source | Est. Impact | Effort | What it does |
|---|---|---|---|---|---|
| 9 | **Integrated Order Flow Imbalance** | Microstructure agent | 3-8 bp/signal at 10-30s | 1 week | Multi-level OFI via PCA. R² 87% integrated (Cont 2014/2023). Highest-R² microstructure variable. Requires Tier 1 #8. |
| 10 | **Micro-price (Stoikov)** | Microstructure agent | Removes fair-value bias | 2 days | Imbalance-spread fair-value anchor. Beats mid-price as benchmark. Removes adverse-selection cost on every fill. |
| 11 | **Depth-weighted OBI + book slope** | Microstructure agent | ρ≈0.20 at 10s | 2 days | L1-L10 imbalance. Conditions when NOT to cross the spread. Works on current snapshot feed. |
| 12 | **Kyle's λ normalizer** | Microstructure agent | Makes OFI stationary | 2 days | Rolling OLS impact coefficient. Time-varying, removes intraday seasonality from flow signals. |
| 13 | **VPIN as risk gate** | Microstructure agent | Drawdown defense | 3 days | Wire VPIN to `CircuitBreakerRiskGate`. Withdraw when toxicity enters top quartile. Build own (don't trust mlfinlab). |
| 14 | **hftbacktest validation harness** | Microstructure agent | Falsifies phantom edges | 3 days | Numba-JIT queue-position simulation. MIT license. Every microstructure signal must pass this before going live. |
| 15 | **Lightweight Charts dashboard** | ADR 0013 | Operator visibility | 2 days | Apache-2.0 canvas charting. Candles + order book visualization. The operator must see what ATI sees. |
| 16 | **StockTwits social sentiment** | Alt-data agent | Contrarian signal | 1 day | Free tier 500K req/mo. Retail sentiment as contrarian indicator. |
| 17 | **Purged walk-forward CV** | ML agent | +0.6-1.5 protective Sharpe | 2 days | Marcos Lopez de Prado purged K-fold. Without this, backtests are phantom. Foundation for all ML. |
| 18 | **Regime detection (HMM + ruptures)** | ML agent | +0.1-0.30 Sharpe | 3 days | `hmmlearn` (BSD) + `ruptures`. Market regime as context feature. Shifts strategy per regime. |
| 19 | **Online drift detection (River/ADWIN)** | ML agent | Protective | 2 days | `River` (BSD-3). Detects when model inputs drift. Triggers retrain or stand-aside. |

### Tier 3 — Medium-term (months, paid or heavy)

| # | Initiative | Source | Est. Impact | Cost | What it does |
|---|---|---|---|---|---|
| 20 | **Glassnode on-chain analytics** | Alt-data agent | Sharpe +1.2-2.0 | $49-999/mo | MVRV, SOPR, exchange flows. Best-in-class BTC/ETH metrics. |
| 21 | **Quiver Quantitative** | Alt-data agent | IR +0.8-1.5 | $30-75/mo | Congress trades outperform market. Academic validation. |
| 22 | **CCXT Pro WebSocket** | Execution agent | 1-3 bps + real-time book | MIT (verify) | Native WS instead of REST polling. Prerequisite for SOR. |
| 23 | **Dune Analytics on-chain** | Alt-data agent | +1.0-1.5 Sharpe | Free-$399/mo | Community dashboards → on-chain signals. Free tier covers most. |
| 24 | **LightGBM + lleaves** | ML agent | +0.15-0.40 Sharpe | Free | lleaves compiles to C, 9.6μs inference. Tabular alpha from microstructure features. |
| 25 | **NATS JetStream event backbone** | ADR 0014 | Multi-process | Free (embedded) | When single-process `ObservationBus` hits limits. Durability + replay. |
| 26 | **Square-root impact calibration** | Microstructure agent | Pre-trade veto | Free | Calibrate from ATI's own fills. Don't hard-code √Q. Reject when impact > edge. |

### Tier 4 — Defer / Reject

| Candidate | Category | Verdict | Why |
|---|---|---|---|
| RavenPack | News | Defer | $10-50K/yr. Gold standard but GDELT+FinBERT is free and proven. Revisit at scale. |
| FIX engines (QuickFIX, B2BITS) | Execution | Ignore | 6μs latency is rounding error behind a multi-hundred-ms AI reasoner. Wrong layer to optimize. |
| NautilusTrader | Execution | Ignore (fork ideas only) | LGPL-3.0 + architectural inversion. Would replace ATI's spine, not extend it. |
| VWAP/TWAP/SOR algorithms | Execution | Defer | Only matter at size ATI doesn't trade yet. Measurement first. |
| YipitData / RS Metrics | Alt-data | Defer | $8K-200K/mo. Proven but premature. Scale-dependent. |
| DeepLOB / CNN-LSTM | ML | Ignore | XGBoost matches or beats with far lower latency. Defend linear baseline first. |
| RL for direction | ML | Ignore | FinRL: +$239 at 0% commission, **-$650 at 0.1%**. Negative-EV at retail costs. |
| Databento / LOBSTER | Data | Ignore | CME/NASDAQ equities. Wrong asset class. ATI is crypto-native. |
| mlfinlab | Microstructure | Ignore | Proprietary license. Discloses webhooks on install. |

---

## Part II — Research Stream Deep-Dives

### Stream 1: Ultra-Low-Latency Data Infrastructure

**Key finding:** ATI's bottleneck is NOT infrastructure latency. The AI reasoner operates at multi-hundred-millisecond timescales. Kernel bypass (Solarflare, DPDK, XDP) and FPGA (Xilinx Alveo) are irrelevant until the decision layer runs faster than ~10ms. The research confirms the current `asyncio.Queue`-based `ObservationBus` is architecturally correct for V1.

**What matters now:**
- **Snapshot→delta migration** for order book (see Stream 7) — this is a data *correctness* issue, not a latency issue
- **Disk recording of L2 deltas** starting today — historical L2 is unrecoverable once missed
- **ClickHouse / QuestDB** for tick analytics at Tier 3 (when SQLite columnar limits hit)

**Verdict:** IGNORE kernel bypass, FPGA, Aeron, Redpanda for now. The playbook's "line-rate, sub-50ms" goal is a Phase 3+ concern. Today the constraint is *signal quality*, not *signal speed*.

### Stream 2: Free/Cheap Massive Data Sources

**Key finding:** The free data stack is remarkably strong. Binance USDⓈ-M Futures WebSocket (funding rate, open interest, liquidations, 100ms book updates) + Bybit linear + OKX books-l2-tbt + Hyperliquid L2 + Polymarket/Kalshi gives ATI coverage no single paid terminal matches — at zero cost.

**Highest value free sources:**
- Binance WebSocket feeds (already in `MARKET_DATA_SOURCE_MATRIX.md`)
- `data.binance.vision` (free aggTrades archive — but NO full-depth L2 history; ATI must record its own)
- FRED / World Bank / OECD (free macro)
- SEC EDGAR (free regulatory)

**Paid sources worth budget (ranked):**
1. Glassnode ($49/mo entry) — on-chain alpha, academic-grade
2. Quiver Quantitative ($30/mo) — congress trade alpha, validated
3. Tardis.dev — tick L2 across crypto venues, if budget allows

**Verdict:** Integrate free sources immediately. The CCXT adapter (ADR 0012) already gives us unified access to 100+ venues. The gap is *what we do with the data*, not *where we get it*.

### Stream 3: Alternative Data for Alpha

**Key finding:** GDELT+FinBERT is the single best opportunity across ALL research streams — free data, free model, published Sharpe ratios above 5.0 out-of-sample. This is ATI's highest-ROI integration.

**Ranked alternative data (by Sharpe contribution):**
1. **GDELT + FinBERT** (free, Sharpe 4.65-5.87) — macro/FX alpha from 15-min global news
2. **SEC EDGAR + edgartools** (free, MIT) — insider trading & 13F institutional holdings
3. **Glassnode** ($49-999/mo) — MVRV, SOPR, exchange flows for BTC/ETH cycle timing
4. **StockTwits** (free tier) — retail sentiment as contrarian signal
5. **Dune Analytics** (free tier) — community on-chain dashboards → signals

**Verdict:** Build a `SentimentFeature` and a `MacroFeature` for the feature registry in Tier 1. These plug into the existing `FeatureRegistry` with zero architectural change.

### Stream 4: Execution & Order Routing

**Key finding:** ATI cannot measure its own slippage today. The `ExecutionReport` has no fee, venue, or maker/taker field. Every basis-point optimization number is currently unfalsifiable inside ATI.

**Critical insight — execution agent's disagreement with the brief:**
> "The entire institutional stack (SOR, VWAP, IS, Almgren-Chriss) exists to manage the size ATI isn't exposed to yet. A 30-line change (time_in_force + post_only) beats all of it at current size."

**This is correct.** The sequence must be:
1. **Measure** (extend `ExecutionReport` with fee/venue/is_maker/arrival_price)
2. **Reduce** (maker/taker awareness + post-only → 4-5 bps saved)
3. **Optimize** (parent/child order model → unlocks execution algorithms)
4. **Automate** (SOR, square-root impact veto)

**Verdict:** Tier 1 execution work is measurement + maker routing only. Everything else is premature.

### Stream 5: Risk Management & Portfolio Engineering

**Key finding:** Three frameworks would each transform ATI's risk-adjusted returns:

1. **Fractional Kelly** — Cuts max drawdown from 50%+ to ~20% while preserving 75%+ of optimal growth. Single highest-impact change. Half-Kelly is robust to estimation error.
2. **Hierarchical Risk Parity (HRP)** — Reduces max drawdown ~30% (e.g., 30%→15%). Eliminates matrix inversion instability. Robust to estimation error. `riskfolio-lib` (BSD, 4.3K stars) wraps 24 risk measures.
3. **CVaR Optimization** — Rockafellar-Uryasev formulation via `cvxpy`. 25-40% tail risk reduction. Convex LP → fast, deterministic, testable.

**Verdict:** Wire fractional Kelly into the `CircuitBreakerRiskGate` sizing logic immediately (Tier 1). Add HRP and CVaR as portfolio-level constraints in Tier 2.

### Stream 6: ML Infrastructure for Alpha

**Key finding:** ATI has no numpy, pandas, sklearn, or any ML library installed. Nine of ten ML infra categories solve problems ATI doesn't yet have. The agent's inversion is correct: **validation ranks above serving**. A wrong CV protocol yields negative-realized Sharpe (deploying phantom edge); a slow model yields merely a smaller positive one.

**Ranked ML infrastructure (by Sharpe contribution):**
1. **Purged walk-forward CV** (MIT, +0.6-1.5 protective Sharpe) — foundation for ALL ML. Without this, every backtest is a lie.
2. **Triple-barrier + meta-labelling** (build, +0.10-0.25) — Marcos Lopez de Prado's framework for bet sizing.
3. **LightGBM + lleaves** (MIT, 9.6μs inference, +0.15-0.40) — tabular alpha from microstructure features.
4. **HMM + ruptures regime detection** (BSD, +0.10-0.30) — market regime as context feature.
5. **River/ADWIN online drift detection** (BSD-3, +0.05-0.20 protective) — detect input drift, trigger retrain.

**The agent's license traps to avoid:**
- `mlfinlab` — proprietary, £100/user/mo, **discloses webhooks firing on install and function calls**
- `backtesting.py` — AGPL-3.0
- `Hopsworks` — AGPL
- `TensorTrade` — won't run on Python 3.14

**Verdict:** Install numpy/pandas/sklearn as the foundational ML dependency. Build purged CV first. Add LightGBM + regime detection in Tier 2. Ignore serving infrastructure (Triton, BentoML, Ray Serve) — model inference is not ATI's bottleneck.

### Stream 7: Market Microstructure Signals

**Key finding:** ATI is crypto-CEX native. This means ~60% of classical microstructure literature (Lee-Ready, EMO, LOBSTER, ITCH, PIN) solves problems crypto venues solve for free — trade direction is already known (Binance `aggTrade.m`, Bybit/OKX `side`). **Trade classification algorithms are IGNORE-tier for ATI's live path.**

**Ranked microstructure signals (by signal-to-noise):**
1. **Integrated Order Flow Imbalance (OFI)** — Best-level OFI explains 65% of mid-price variance; integrated OFI lifts to 87% (Cont 2014/2023). The dominant term in 1s-60s directional forecast. **BLOCKER: requires L2 deltas, not snapshots.**
2. **Micro-price (Stoikov)** — Imbalance-spread fair-value anchor. Martingale by construction. Beats mid-price as benchmark. Removes adverse-selection bias from every fill.
3. **Depth-weighted OBI (L1-L10)** — ρ≈0.20 at 10s, decays to noise in ~26s. Conditions when NOT to cross the spread. Works on current snapshots.
4. **Kyle's λ** — Rolling OLS impact coefficient. Makes OFI stationary across time-of-day and regimes. Gives model-free slippage estimate for the risk gate.
5. **VPIN** — Wire to risk gate (not alpha). Withdraw when toxicity enters top quartile. Build own implementation.

**Critical adversarial evidence (the agent did not sugar-coat):**
- A live experiment with 232,897 real maker orders on Binance BTC perp found **every** naive imbalance strategy lost money net of fees. The intuitive trade is the wrong trade.
- A pre-registered falsification study of six order-flow signals found **0/20 cells survived** multiple-testing correction. Gross +2.1bp vs an 18bp materiality bar.
- **ATI's effective horizon is ~2 average price changes. Any feature computed slower than ~26s (OBI decay constant) is measuring noise.**

**Verdict:** Microstructure alpha is real but hard. Start with micro-price + OBI (Tier 2, works on snapshots). Fix L2 delta capture first (Tier 1 prerequisite). Validate everything through hftbacktest before going live.

---

## Part III — The Implementation Sequence

```
PHASE A — Measure & Protect (Week 1)
├── #3 Execution measurement (ExecutionReport extension)
├── #8 L2 delta ingestion fix + begin disk recording
├── #5 Fractional Kelly in risk gate
├── #1 GDELT + FinBERT sentiment feature
└── #2 SEC EDGAR insider/13F feature

PHASE B — Reduce Cost & Risk (Week 2)
├── #4 Maker/taker + post-only routing
├── #6 Hierarchical Risk Parity
├── #7 CVaR tail-risk optimization
├── #15 Lightweight Charts dashboard
└── #16 StockTwits social sentiment

PHASE C — Alpha from Data (Weeks 3-4)
├── #9 Integrated OFI (requires Phase A #8)
├── #10 Micro-price fair-value anchor
├── #11 Depth-weighted OBI + book slope
├── #12 Kyle's λ normalizer
├── #17 Purged walk-forward CV (foundation for all ML)
└── #18 Regime detection (HMM + ruptures)

PHASE D — Validate & Optimize (Month 2+)
├── #13 VPIN risk gate
├── #14 hftbacktest validation harness
├── #19 Online drift detection
├── #20 Glassnode on-chain (if budget)
├── #21 Quiver Quantitative
├── #24 LightGBM + lleaves
└── #26 Square-root impact calibration
```

**Definition of done for every item:** green test suite, commit, and an ADR if it changes architecture.

---

## Part IV — What the Research Rejected (and why)

The agents were explicitly asked to find things to IGNORE as well as INTEGRATE. These rejections save months of wasted effort:

| Category | Rejected | Reason |
|---|---|---|
| Latency | Kernel bypass, FPGA, Aeron, colocation | AI reasoner is 1000x slower than these optimizations. Wrong bottleneck. |
| Execution | All FIX engines, commercial SOR, TCA vendors | ATI has no slippage data to analyze. Measure first. |
| Execution | VWAP, TWAP, Almgren-Chriss | Only matter at size ATI doesn't trade yet. |
| ML | RL for direction (FinRL) | Negative-EV at retail commission rates. |
| ML | DeepLOB, CNN-LSTM | XGBoost matches or beats at far lower latency. |
| ML | Triton, BentoML, Ray Serve serving infra | Model inference is not ATI's bottleneck. |
| Data | Databento, LOBSTER, NASDAQ ITCH | Wrong asset class (equities). ATI is crypto-native. |
| Microstructure | Trade classification (Lee-Ready, EMO) | Crypto venues give true aggressor flag. 100% accuracy, free. |
| Tools | mlfinlab | Proprietary license + webhooks on install. |

---

## Appendix — Agent Reports (source files)

| Stream | Agent | Report location |
|---|---|---|
| Ultra-low-latency infra | Agent 1 | (inline — see task ses_017864614ffepu1WIwEV97DjjG) |
| Free/cheap data sources | Agent 2 | (inline — see task ses_0178601c5ffebTBBVKNCHZBDj5) |
| Alternative data | Agent 3 | `docs/alternative-data-research.md` |
| Execution & order routing | Agent 4 | `research/decisions/execution-and-order-routing-landscape.md` |
| Risk/portfolio engineering | Agent 5 | `docs/Research/Risk-Management-Research.md` |
| ML for alpha | Agent 6 | `experiments/decisions/ml_infrastructure_landscape.md` |
| Market microstructure | Agent 7 | (inline — see task ses_01784c1f1ffeuz1X0UuW6GOMt5) |
