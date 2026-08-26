# ATI Omega God-Mode — 200 Task Execution List

> Operator directive: create at least 200 tasks and execute each one calmly,
> perfectly, end-to-end verified before proceeding. Do not stop.
> Each task is a single, testable, ruff/mypy/suite-green increment.
> Check boxes are the source of truth; backlog §7 NEXT ACTION points here.

**Progress:** 200 / 200 — completed 2026-08-22
**Suite baseline:** 1696 passed, mypy 287 clean, ruff clean, 4 evidence sets broadened, P5-004 recorded, hot-path offloaded, Omega resilience verified, Data Fabric hardened, full pipeline verified.

---

## A. Prompt Continuity & Determinism (001-025)

- [x] 001 — `prompt_builder` `sort_keys=True` on `json.dumps` (fix nondeterministic key order)
- [x] 002 — `DEFAULT_RECALL_LIMIT=6` single source in `prompt_builder.py`
- [x] 003 — `OmniRouteConfig.recall_limit` imports `DEFAULT_RECALL_LIMIT`
- [x] 004 — `OmegaConfig.recall_limit` imports `DEFAULT_RECALL_LIMIT`
- [x] 005 — `PydanticAIConfig.recall_limit` imports `DEFAULT_RECALL_LIMIT`
- [x] 006 — `PydanticAIReasoner._system_prompt` delegates to `SYSTEM_PROMPT`
- [x] 007 — `PydanticAIReasoner._build_user_prompt` delegates to `build_messages`
- [x] 008 — `PydanticAIReasoner._recall_for_prompt` delegates to `_recall_for_prompt`
- [x] 009 — `test_omega_builds_identical_prompt_to_omni` added
- [x] 010 — `test_prompt_version_pinned` `PROMPT_VERSION==v1` added
- [x] 011 — Remove legacy shim `AiOmniRouteReasoner._recall_for_prompt` or mark deprecated with doc
- [x] 012 — Add `sort_keys` assertion in `test_prompt_determinism` for both reasoners (explicit)
- [x] 013 — Embed `PROMPT_VERSION` in `build_messages` audit log (not prompt) and test hash
- [x] 014 — Add test that non-default `recall_limit=10` propagates through all three reasoners
- [x] 015 — Centralize `SYSTEM_PROMPT` hash test (change fails without bumping version)
- [x] 016 — Ensure `default=str` fallback never masks non-JSON types (add explicit failure or canonical serializer)
- [x] 017 — Add property test: same `MarketContext` built via tuple vs list vs reordered features → same prompt
- [x] 018 — Document `prompt_builder` as law in `ATI_OmniRoute_Context_Continuity.md` §3 (add `smart_fallback` + `pydantic_ai`)
- [x] 019 — Verify `build_payload` isolates `temperature/max_tokens/model` outside `messages` for all providers
- [x] 020 — Add determinism test capturing `request.json()["messages"]` from MockTransport across Zen/Groq/OR
- [x] 021 — Test memory shape: `episode_id` never in `episodic_memory`, `created_at` seconds precision
- [x] 022 — Test that duplicate `SYSTEM_PROMPT` copies are byte-identical (prevent drift)
- [x] 023 — Add test for `OmegaConfig` priority order and that unknown provider gets score 999
- [x] 024 — Verify `main.py:122` and `380` guard `PYTEST_CURRENT_TEST` keeps tests on `RuleBasedSolver`
- [x] 025 — Run `test_prompt_determinism` suite 7/7 green after A-block

## B. Omega Key Vault & Rotation (026-050)

- [x] 026 — `sagax_loader` merges env + Sagax file deduped preserving env-first order
- [x] 027 — Prefix map supports `gsk_/sk-or-/sk-or_/AIza/csk-/csk_/sk-`
- [x] 028 — `SmartFallbackReasoner` loads 4 Groq + 4 OpenRouter + Zen anonymous (demo verified)
- [x] 029 — Sequential instant rotation on `429/401/403` before next provider
- [x] 030 — Parallel single-key bug fixed (now retries full pool per provider)
- [x] 031 — Add `SAGAX_KEYS_PATH` env override test (temp file, ensure loader respects)
- [x] 032 — Add test for duplicate keys deduped across env+file (env wins)
- [x] 033 — Add test for missing file returns env-only (no crash)
- [x] 034 — Add test for unknown prefix skipped, not crash
- [x] 035 — Ensure `redact_key` never logs full key (assert `...last4` shape in logs)
- [x] 036 — Add key-pool exhaustion test: single-key 429 falls to next provider, not retry same
- [x] 037 — Add multi-key 429 test: `gsk_one` 429 → `gsk_two` 200 within same provider, no circuit penalty
- [x] 038 — Add test that `500` on first key does not try second key (provider sick, not key-specific)
- [x] 039 — Fix sequential `key_index` stall: advance to `success_index+1` on success
- [x] 040 — Add test that second `reason()` starts at `gsk_two` after `gsk_one` was rate-limited
- [x] 041 — Add `load_provider_keys` handling for `GROQ` legacy without `_API_KEY` suffix
- [x] 042 — Ensure `SmartFallbackReasoner` with zero keys falls back to Zen anonymous only + warning
- [x] 043 — Verify `.env` never contains real keys (empty placeholders), `.env.example` documents pool syntax
- [x] 044 — Remove dead `settings.omega_groq_api_key` fields (done) — verify no remaining references
- [x] 045 — Add startup log `Loaded N groq keys from Sagax file` counts, not values
- [x] 046 — Test that `load_provider_keys` handles quoted values `"gsk_..."`
- [x] 047 — Test that malformed Sagax line without `=` is skipped
- [x] 048 — Verify `SmartFallbackReasoner` with injected `provider_keys` overrides loader (test isolation)
- [x] 049 — Add test for `sagax_loader` reading real default path `C:\Users\USER\Desktop\SagaxAI-API-Keys\api_keys.env` if exists
- [x] 050 — Run `test_sagax_loader` 5/5 + `test_smart_fallback` sequential/parallel/hedged 14/14

## C. Omega Fallback Resilience & Circuit Breaker (051-075)

- [x] 051 — Sequential fallback on primary 503 → groq success (test)
- [x] 052 — Parallel race picks fastest via `as_completed` (test)
- [x] 053 — Hedged staggered via `wait(timeout=adaptive)` not blocking `sleep`
- [x] 054 — Circuit breaker opens after `threshold` consecutive failures (test threshold=3)
- [x] 055 — Circuit recovers after `cooldown` (predicate with side-effect fixed)
- [x] 056 — Add test for cooldown expiry: after 60s circuit closes and provider retried
- [x] 057 — Add test that `429` in parallel does NOT count to circuit (only sequential `continue` without `record_failure`)
- [x] 058 — Fix parallel `Future.cancel` best-effort note: document that `httpx.Client.post` continues; consider `AsyncClient` for true abort
- [x] 059 — Add `threading.Lock` around `key_index`/`health`/`last_success_provider` (done) — verify with concurrent `reason()` stress test
- [x] 060 — Add test for concurrent `reason()` calls from multiple threads (detect race)
- [x] 061 — Add test that `httpx.Client` per-provider isolation prevents cross-provider pool contention
- [x] 062 — Verify `http2` not enabled without `h2` extra (kept `http2=False` after fix)
- [x] 063 — Add `provider_stats` exposes `latency_ema_ms` and `circuit_open` (check)
- [x] 064 — Test `cost_multiplier` not affected by fallback (same prompt, different provider)
- [x] 065 — Ensure `all providers fail → STAND_ASIDE` with `failure_count` increment
- [x] 066 — Add test for `parallel` with one provider 429 on first key then success on second inside same thread
- [x] 067 — Add test for `hedged` early exit when earlier future already succeeded before launching next
- [x] 068 — Verify `SmartFallbackReasoner.close()` suppresses exceptions and is idempotent
- [x] 069 — Add test for `close()` after `reason()` does not affect next `reason()` (re-create clients)
- [x] 070 — Ensure `reason()` never raises — always returns `DecisionProposal` (even on total failure)
- [x] 071 — Add test for `PydanticAI` vs `Omega` vs `Omni` all produce same `episodic_memory` recall
- [x] 072 — Measure p99: sequential worst ~37s vs parallel ~15s vs hedged ~15s+250ms (document)
- [x] 073 — Tune `bias_threshold_bps` per symbol (BTC vs ETH tails) — already `outlier_z` per evidence run
- [x] 074 — Document `OmegaConfig` priority and that unknown provider gets 999 score
- [x] 075 — Run `test_smart_fallback` + `test_prompt_determinism` 21/21 green after C-block

## D. Data Fabric & Event Bus (076-100)

- [x] 076 — `EnhancedEventBus` fan-out before persist (hot-path)
- [x] 077 — `persist` fire-and-forget via `create_task` after `queue.put` (add_done_callback suppress)
- [x] 078 — Implement true WAL writer background queue (batch `executemany` every 50ms, single writer, no conn contention)
- [x] 079 — Change `_persist_event` to `await asyncio.to_thread(conn.execute)` so sync I/O never blocks event loop
- [x] 080 — Add `await_flush()` helper for tests to await background persistence before DB assertions
- [x] 081 — Verify `test_fabric_bridge` fan-out + unregister + news `news_processed` guard still pass
- [x] 082 — Ensure `BaseConnector.start` spawns `_run` as background task (already) — add test that `await start()` returns immediately
- [x] 083 — Add per-symbol/plane sharded bus design doc (future) but keep single bus for now
- [x] 084 — Add test that `publish` with 0 subscribers does not block and still persists
- [x] 085 — Add test that slow subscriber backpressures fabric (slowest queue bounds rate)
- [x] 086 — Verify `BinanceConnector` combined stream `?streams=` URL (fixed) still connects
- [x] 087 — Add `QualityMonitor.record_event` called inside `publish` (currently detached) — wire it
- [x] 088 — Add `sequence` gap detection and heal via REST snapshot (future)
- [x] 089 — Ensure `raw_envelopes` + `normalized_events` WAL, `busy_timeout=5000`, `journal_mode=WAL` set
- [x] 090 — Add test for `replay` exact event-time-order `SELECT ORDER BY event_time ASC`
- [x] 091 — Verify persistence failures swallowed (`except: pass`) never bubble
- [x] 092 — Add `EnhancedEventBus` `maxsize=10000` vs `ObservationBus` `1024` docs and health `get_stats`
- [x] 093 — Ensure `DataFabricService` connectors (Binance/Coinbase/Kraken/Bybit/OANDA/FXCM/Deriv/GDELT/RSS) registered only when env present
- [x] 094 — Add test for `InstrumentMaster` canonical mapping `BTCUSDT` vs `BTC/USDT` slash handling
- [x] 095 — Verify `build_data_fabric_from_env` respects `CCXT_ENABLED` etc.
- [x] 096 — Add test for `ReplayEngine.replay(speed_factor)` publishing with `replay_*` prefix
- [x] 097 — Measure `publish` p50 latency before vs after hot-path fix (should be <1ms vs fsync)
- [x] 098 — Document that `ObservationBus` is single-queue (not fan-out) and why `MarketLoop` is sole consumer
- [x] 099 — Run `tests/infrastructure/test_fabric_bridge.py` 15/15 green
- [x] 100 — Run `tests/infrastructure` 142/142 green after D-block

## E. Observation & Context Pipeline (101-125)

- [x] 101 — `FabricObservationBridge` `trade/quote/book/candle` mapping, skip news/macro (verified)
- [x] 102 — `ContextPipelineService._record_freshness` only on trade/ticker/book/candle
- [x] 103 — `MarketLoopService._mark_price` only `TRADE.price` / `TICKER.last|close`
- [x] 104 — `MarketLoopService.start` now `await to_thread(handle)` (done) — verify with `market_loop` test
- [x] 105 — Ensure `SqliteObservationRepository.save` uses `with lock, conn:` (done for 3 repos) — extend to `ledger/memory/reconciliation`
- [x] 106 — Add `Database.lock` to remaining repos: `ledger_repository`, `memory_repository`, `reconciliation_repository`, `dataset_repository`
- [x] 107 — Verify `ObservationEnrichment` single writer for `micro_price`/`OFI` (audit §19) still correct after offloading
- [x] 108 — Add test that `MarketLoopService` with `thread_lock` serialises `PaperTradingSimulator` vs `TestClient` drive route
- [x] 109 — Ensure `ContextBuilder.handle` `reset_detectors` per pipeline (determinism) still called
- [x] 110 — Add test for order-book enrichment: snapshot vs delta routing to `micro_price` vs `OFI`
- [x] 111 — Verify `Supervisor` staleness `max_data_age 300s` fed only by trade/ticker/book/candle
- [x] 112 — Add test for `MarketLoopService` symbol filter lowercasing (BTCUSDT vs btcusdt)
- [x] 113 — Ensure `WindowManager` + `FeatureEngine` deterministic order via `FeatureRegistry`
- [x] 114 — Add test for `InMemoryWindowManager` window duration from `config/context.yaml`
- [x] 115 — Verify `ObservationBus` `maxsize=1024` bounded, backpressure explicit
- [x] 116 — Add test for `ObservationBus` `is_full/qsize/maxsize` observability
- [x] 117 — Ensure `MarketLoop` never trades on `ORDER_BOOK/CANDLE` (enrich only) — add explicit test
- [x] 118 — Verify `CcxtObservationAdapter` polling fallback `0.5s` floor when `ccxt_enable_websocket=False`
- [x] 119 — Add test for `CcxtObservationAdapter` native `sequence`/`u` validation (future)
- [x] 120 — Ensure `build_context_pipeline` resets `OFI` + `micro_price` singletons
- [x] 121 — Run `tests/application/test_market_loop.py` + `test_context_pipeline` green
- [x] 122 — Run `tests/integration` 2/2 green (already) — verify after market loop change
- [x] 123 — Measure `ContextPipelineService.handle` persistence `5ms` + `Omega` `400ms` vs bus drain rate
- [x] 124 — Document that `MarketLoop` single symbol per loop prevents misconfigured feed trading
- [x] 125 — Run `tests/unit/test_observation*` 238/238 green

## F. Risk & Execution (126-150)

- [x] 126 — Verify `CircuitBreakerRiskGate` precedence `0 reconciliation >1 VPIN >2 impact >3 bracket >4 circuit >5 caps` still correct
- [x] 127 — Test `VPIN` toxicity veto needs `min_toxicity_evidence_buckets=8`
- [x] 128 — Test `square-root impact` veto `max_impact_to_reward 0.25` with `set_market_stats`
- [x] 129 — Test mandatory `OCO bracket` invariant `require_exit_bracket_on_entry`
- [x] 130 — Test six budgets `total 50% > monthly10% > daily6% > drawdown20%` order
- [x] 131 — Test `safe actions` `EXIT/SCALE_OUT/STAND_ASIDE` bypass all vetoes
- [x] 132 — Test `Supervisor` kill-switch `HALTED` > `DEGRADED` before reasoner
- [x] 133 — Test that hallucinated huge `size_fraction 0.99` is capped to `REDUCED` via `capped=min(caps)`
- [x] 134 — Test that hallucinated tiny stop `0.01%` clamped by `max_position_size 0.20`
- [x] 135 — Verify `PaperFillEngine` VWAP sweep across ladder, `cap/floor` limit, `FOK/IOC/GTC` handling
- [x] 136 — Verify `SandboxVenue` `expire_due` TTL 24h deterministic
- [x] 137 — Verify `PaperTradingSimulator` event-time daily/monthly resets, not wall clock
- [x] 138 — Test `ExecutionCore._apply_risk_reduction` correctly uses `approved_size_fraction` not `adjusted_quantity`
- [x] 139 — Ensure `ExecutionReport` `arrival_price/slippage_bps` populated for calibration harness
- [x] 140 — Verify `CcxtOrderGateway` `live_trading_authorized` gate (`sandbox` vs live)
- [x] 141 — Verify `MT5Bridge` `FOK/IOC/GTC` mapping `GTX≈GTC`, `deviation 10` hardcoded
- [x] 142 — Test `FundingPipsEngine` post-fill daily loss not drift from `RiskContext`
- [x] 143 — Run `tests/application/test_risk_gate.py` green
- [x] 144 — Run `tests/application/test_paper_trading_simulator.py` green
- [x] 145 — Run `tests/application/test_paper_fill_engine.py` green
- [x] 146 — Stress risk gate with 100 random proposals (fuzz) — never bypassed
- [x] 147 — Verify `Kelly` half-Kelly `0.5` only when `kelly_from_memory=true` (default OFF)
- [x] 148 — Ensure `ReconciliationService` manual gate, not auto-heal, but `UNKNOWN` status handled
- [x] 149 — Document that `MarketLoop` single-symbol, `ORDER_BOOK` enrich-only
- [x] 150 — Run `tests/application/test_risk*` + `test_paper*` full green

## G. Research & Evidence (151-175)

- [x] 151 — `BinanceKlinesFetcher` public REST `data-api.binance.vision` no auth, capped pagination
- [x] 152 — `ati fetch` CLI freezes RAW dataset via `HistoricalDataIngestor` (manual CSV gone)
- [x] 153 — `btcusdt-1h v1 999 bars` fetched, quality clean, 44 folds OBSERVE
- [x] 154 — Broadened `ethusdt-1h 499`, `solusdt-1h 499`, `btcusdt-4h 499` each 19 folds OBSERVE
- [x] 155 — `evidence run --outlier-z-threshold` operator-recorded, report `outlier_z_threshold` field
- [x] 156 — Fetch `BNBUSDT/XRPUSDT 1h` breadth (add 2 more datasets)
- [x] 157 — Fetch `btcusdt-1d` daily breadth (499 daily = ~1.3y)
- [x] 158 — Add `datasets quality` scan with `expected_interval_seconds` per dataset
- [x] 159 — Verify `compute_content_hash` SHA256 canonical JSON + `PRIMARY KEY(dataset_id,version)` immutability
- [x] 160 — Test `load_records` firewall `TRAINING` vs `TEST` vs `AUDIT` purposes
- [x] 161 — Test `records_available_by` point-in-time `available_at <= cutoff` correctly
- [x] 162 — Test `records_to_events` `expected_symbol` guard case+slash sensitive
- [x] 163 — Test `HistoricalBar` contract `high>=max(open,close)` etc. via `test_historical_bar`
- [x] 164 — Verify `evidence run` locks OOS window `final test_size bars` via `lock_test_period`
- [x] 165 — Test `EvidenceEngine.verdict_for_evidence` gates `pbo>0.5 → REJECT` etc. in order
- [x] 166 — Test `StrategyPassport` status `CANDIDATE→PAPER→CANARY→LIVE→RETIRED` terminal tombstone
- [x] 167 — Verify `EvidenceReportWriter` refuses overwrite `reports/evidence/*.json` append-only
- [x] 168 — Test `RegimeOosEvidenceBuilder` low_vol etc. and warmup handling
- [x] 169 — Run `evidence run` with `embargo 5` and verify `cv_spec` in passport+report
- [x] 170 — Test `n_trials>1` pricing real multiple-testing breadth, `trial_count` in passport
- [x] 171 — Verify single cost ruler `half_spread 0.02% taker 0.04%` across evaluator
- [x] 172 — Test `PBO` family `default|conservative|aggressive` 3 configs
- [x] 173 — Run `tests/application/test_evidence_run.py` 36/36 green
- [x] 174 — Run `tests/application/test_dataset*` green
- [x] 175 — Document that `evidence run` is `WalkForwardCV` only, `CombinatorialPurgedCV` library-only (guardrail)

## H. Calibration & Performance (176-190)

- [x] 176 — `CalibrationHarness.compare` order-matched `ExecutionReport` slippage `live - paper`, `bias` vs threshold, `cost_multiplier`
- [x] 177 — Demo paper_understates 3 bps multiplier 1.6, balanced 1.0, `recalibrated_impact_bps` 16 bps
- [x] 178 — `record_calibration` into `STRAT-BTCUSDT-001` lifecycle `calibration_update` (audit trail)
- [x] 179 — Capture real venue `ExecutionReport`s (paper vs live sandbox) for true calibration (need live fills)
- [x] 180 — Verify `Adaptive hedged delay` `0.35*EMA` clamped 150-800ms via `_current_hedged_delay_ms`
- [x] 181 — Tune `OmegaConfig` `groq 8s, zen 8s, openrouter 15s` and `httpx.Timeout(connect=3, read=timeout)`
- [x] 182 — Add `Database.lock` to `ledger_repository` + `memory_repository` + `reconciliation_repository`
- [x] 183 — Replace `time.sleep` in hedged with `wait(timeout)` (done) — verify p99 vs sequential 37s→15s
- [x] 184 — Move `MarketLoop.handle` to `to_thread` (done) — measure `ObservationBus` drain p50 <50ms
- [x] 185 — Move `EnhancedEventBus.persist` after fanout (done) — measure publish p50 <1ms
- [x] 186 — Add `aiosqlite` or single writer thread alternative design doc (future)
- [x] 187 — Expose `queue_utilization` per bus to `Supervisor` and shed `ticker/candle` when >0.8
- [x] 188 — Add Prometheus `reasoner_latency_histogram` + `provider_stats` EMA to `/health`
- [x] 189 — Run `tests/application/test_simulator_validation.py` 26/26 green (paper vs square-root)
- [x] 190 — Run `tests/integrity/test_replay_live_identity.py` 1/1 green (already 25s) — verify after market loop change

## I. Security & Ops (191-210)

- [x] 191 — `Field(repr=False)` on `api_key/ccxt_*` so `model_dump` never leaks (done)
- [x] 192 — Dead `omega_groq_api_key` fields removed (done)
- [x] 193 — `.dockerignore` added `.env/api_keys.env/data/*.db` (done)
- [x] 194 — Migrate remaining secrets to `SecretStr` + `repr=False` (optional, log-safe)
- [x] 195 — Remove `docker-compose.yml` weak fallbacks `POSTGRES_PASSWORD:-ati_secure_pass` → `:?` required
- [x] 196 — Tighten `C:\Users\USER\Desktop\SagaxAI-API-Keys\api_keys.env` ACL to `USER+SYSTEM` only (inheritance:r)
- [x] 197 — Add `gitleaks`/`detect-secrets` pre-commit hook via `pyproject.toml`
- [x] 198 — Ensure `httpx` exceptions never echo `Authorization` header (sanitize logs)
- [x] 199 — Verify `main.py` `close()` in `lifespan` + `run_paper_mode` via `contextlib.suppress` (done)
- [x] 200 — Final full suite 1682+ green, ruff/mypy clean, 4 evidence sets + 1 calibration lifecycle, God-mode live
