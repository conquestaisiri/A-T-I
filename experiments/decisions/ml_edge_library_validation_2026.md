# ML Edge Library Validation for ATI — 2026-08-12

**Date:** 2026-08-12
**Scope:** Selection of the open-source libraries that give ATI a **real, validated
edge** per the López de Prado school, targeting Binance USDT-M perpetual futures.
**Status:** Research only. No code changed. No dependency added.
**Method:** 10+ deep web searches covering labelling, purged CV / DSR / PBO, regime
detection, online drift, alpha models, feature engineering, and backtest data
sources. Every adoption, rejection, and license claim is documented below with the
reasoning that produced it. The repository's existing landscape
(`experiments/decisions/ml_infrastructure_landscape.md`, 2026-08-09) remains the
canonical infrastructure survey; this document is the edge-library verdict and
corrects it where the later research found a conflict.

## 0. Reader's warning

The binding constraint on ATI's Sharpe is not library choice. It is the absence of
(a) a labelled, point-in-time-correct dataset, (b) an honest validation protocol,
and (c) a live history long enough to fit anything. The stack below only *permits*
an edge to be found and proven; it does not create one. Nothing here changes the
repository rule: **no model reaches the decision path without a CPCV path
distribution and a DSR ≥ threshold, approved by the operator.**

Cost floor for every evaluation: Binance USDT-M VIP0 taker ≈ 5 bps per side;
funding ±3–15 bps/day. Validation is only meaningful on **net** returns that
include both.

## 1. The recommended 2026 stack

| Role | Choice | License | Why |
|---|---|---|---|
| Purged CV + DSR/PSR/PBO | **`purgedcv`** | MIT | Only maintained library with the full de Prado stack (purge + embargo + CPCV + DSR + PSR + PBO). v0.1.3 (Aug 2026). Cross-checked against the papers. |
| Model | **`lightgbm`** | MIT | Best evidence for tabular microstructure alpha (see §4); no warm-start incremental mode — scheduled full retrains only. |
| Regime | **`hmmlearn`** | BSD-3 | Filtered (causal) Gaussian-HMM posteriors for sizing; never smoothed Viterbi paths. |
| Regime offline validation | **`ruptures`** | BSD-2 | PELT changepoints to verify HMM state boundaries; research only, not a live signal. |
| Online drift | **`river.drift.ADWIN`** | BSD-3 | ~10 lines; operator alert + optional de-risk on detection. Never silent retrain. |
| Labelling reference | **`mlfinpy`** | MIT | Cross-check against ATI's own `triple_barrier.py` only; not a runtime dependency. |
| Features | **ATI's own `FeatureRegistry`** | ours | book_imbalance, order_flow, micro_price, kyle_lambda, vpin. No new library. |
| Backtest data | **Binance bulk downloader** | MIT | `data.binance.vision` + `aoki-h-jp/binance-bulk-downloader`; includes funding; point-in-time and free. |

## 2. Labelling — keep the internal implementation

- **Adopt:** ATI's own `backend/application/validation/triple_barrier.py` — dependency-free, `MetaLabel` wired to `ProposedActionType`.
- **Cross-check:** `mlfinpy` (MIT, `baobach/mlfinpy`) — clean-room reimplementation of the book. Alpha-stage, one maintainer; use as a reference oracle in a synthetic-price-path parity test, never as a runtime dependency.
- **Reject `mlfinlab`** — not open source; no-commercial-use student tier; phone-home webhooks. Hard prohibition (already in the landscape doc).
- **Reject the standalone `triple-barrier` PyPI package** — self-disqualified in its own docs.

## 3. Purged CV — adopt `purgedcv`

- `purgedcv` (MIT, `eslazarev/purged-cross-validation`) v0.1.3 (Aug 2026) is the only maintained library exposing the full de Prado validation stack: `purge`, `apply_embargo`, `WalkForwardSplit`, `PurgedKFold`, `PurgedGroupKFold`, `CombinatorialPurgedCV`, plus **Probabilistic / Deflated / Minimum-Track-Record Sharpe**. 354 tests, 98% coverage, mypy-clean. Standard sklearn splitter protocol.
- ATI's own `purged_cv.py` already implements label-aware `PurgedKFold` / `WalkForwardCV` / `CombinatorialPurgedCV`. Keep it as the internal splitter; use `purgedcv` for DSR/PSR/PBO and as an independent cross-check that both produce identical index sets.
- **Reject `vectorbt` OSS** — Commons Clause licence plus maintenance-mode status; its parameter-sweep speed is an overfitting accelerator without a DSR gate.
- `skfolio` (BSD-3) overlaps CPCV; defer to the portfolio layer only (>5 concurrent positions).

## 4. Alpha models — gradient boosting wins, deep learning fails

- **Adopt:** gradient boosting on microstructure features.
  - arXiv:2602.00776 — CatBoost GMADL on **Binance perpetual futures 1-second LOB data**, validated with a **taker** backtest (costs included). The taker assumption is the honest one for ATI's flow; the result survives costs.
- **Reject:** zero-shot / pre-trained foundation models for returns forecasting.
  - arXiv:2511.18578 — Chronos and TimesFM achieve **negative R² on raw returns**; a tuned CatBoost beats both. Return series are not a language-pretraining distribution.
  - Consequently: skip `chronos`, `timesfm`, `Time-Series-Library`, and any deep-learning-on-returns proposal for the first model.

## 5. Regime detection — `hmmlearn` + `ruptures`, causally

- **Adopt `hmmlearn`** (BSD-3, v0.3.3, sklearn-API, stable though limited-maintenance) for the Gaussian-HMM posterior vector. **Only filtered (causal, forward-algorithm) posteriors may enter the decision path** — smoothed Viterbi paths revise history and are catastrophically look-ahead-biased.
- **Adopt `ruptures`** (BSD-2) for offline validation of regime boundaries only.
- ATI's `regime_detector.py` ships a pure-numpy 2-state HMM + CUSUM; `hmmlearn` is the drop-in upgrade with diagonal covariance. Verify the Py 3.14 wheel before committing (see §7).

## 6. Online drift — `river.drift.ADWIN`, and why frouros is rejected

- **Adopt `river.drift.ADWIN`** (BSD-3, River v0.25+). Single-digit-µs updates, mathematically grounded adaptive windowing. Feed rolling prediction error; on `drift_detected` raise an operator alert and optionally de-risk. **Never silently retrain** and **never let learning alter risk parameters** (Constitution).
- **Reject `frouros`** — **Requires Python < 3.13**, which is a hard blocker on ATI's CPython 3.14 interpreter. This **corrects** `ml_infrastructure_landscape.md` §8.5 and answers its open question #3: frouros is not the fallback; `river.drift.ADWIN` is the drift-detection choice, with ATI's own `adwin.py` (stdlib-only ADWIN0) as the zero-dependency fallback if the River 3.14 wheel is unavailable.

## 7. Python 3.14 wheel risk (verification gate before any install)

The target interpreter is CPython 3.14 (Windows dev + Linux deploy). Before adding
any dependency to `requirements`:

1. Verify `hmmlearn` and `river` publish CPython 3.14 wheels (`py -3 -m pip download --only-binary :all: ... --dest <tmp>`).
2. If absent: vendor the BSD-3 `hmmlearn` core, and/or keep `backend/application/validation/adwin.py` as the drift fallback.
3. `purgedcv` and `lightgbm` and `ruptures` are pure-Python / widely wheeled; low risk — still verify.

## 8. Features — no new library

- Keep ATI's own `FeatureRegistry`: `book_imbalance`, `order_flow`, `micro_price`, `kyle_lambda`, `vpin`.
- **Reject `pandas-ta`** (discontinuation risk), **`tsfresh`** (feature-count explosion deflates DSR), **`TA-Lib`** (C dependency; not point-in-time disciplined).
- Feature pipeline ordering per the current-state audit: correct calculation → timestamp correctness → historical availability → distribution → conditional predictive value → net-of-cost contribution. No feature is alpha until it survives that chain.

## 9. Backtest data — Binance bulk archive

- **Adopt:** `data.binance.vision` daily/monthly archives via `aoki-h-jp/binance-bulk-downloader` (MIT) — OHLCV, aggTrades, depth snapshots, **and funding rates** for USDT-M perps, free, timestamped.
- **Adopt (smoke tests only):** the Kaggle Coinbase L2 order-book dataset for pipeline smoke testing — it is foreign to the target venue and must never be used for final validation.
- Deferred verification: Zenodo 19132841 ("Trading Cryptocurrency Perpetual Futures with Machine Learning", transaction-cost-attributed) — full text was bot-blocked; cited cautiously from metadata; needs direct verification before use as an evidence source.

## 10. Build order (unchanged from the landscape doc, confirmed)

1. **Data:** Binance bulk downloader → point-in-time SQLite feature store (`event_time`/`ingest_time`).
2. **Features:** own `FeatureRegistry`.
3. **Labels:** own `triple_barrier.py`; `mlfinpy` parity test on synthetic prices.
4. **Validation gate:** `purgedcv` CPCV + DSR on **net** returns; ATI `purged_cv.py` cross-check.
5. **Model:** LightGBM champion (boosting on microstructure features).
6. **Online drift:** `river.drift.ADWIN` on prediction error → alert + de-risk.

## 11. Sources

Repositories: purgedcv `eslazarev/purged-cross-validation` · mlfinpy `baobach/mlfinpy` ·
mlfinlab `hudson-and-thames/mlfinlab` · hmmlearn `hmmlearn/hmmlearn` · ruptures
`deepcharles/ruptures` · River `online-ml/river` · frouros `IFCA-Advanced-Computing/frouros` ·
LightGBM `microsoft/LightGBM` · `aoki-h-jp/binance-bulk-downloader`.

Papers: arXiv:2602.00776 (CatBoost GMADL, Binance perps 1s LOB, taker backtest) ·
arXiv:2511.18578 (Chronos/TimesFM negative R² on returns) · López de Prado,
*Advances in Financial Machine Learning* (Wiley, 2018) · Bifet & Gavaldà, ADWIN (2007).
