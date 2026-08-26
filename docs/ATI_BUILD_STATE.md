# ATI BUILD STATE — COMPLETE RECOVERY DOCUMENT

> **THIS FILE IS THE MASTER RECOVERY DOCUMENT.** If you are a new agent session
> reading this after compaction, this file contains EVERYTHING you need to
> continue building. Read it completely before doing anything else.
>
> Last updated: 2026-08-24 by the god-mode build session.
> Suite: 1707 passed, ruff clean, mypy strict 291 clean.

---

## 1. PROJECT OVERVIEW

**What:** Autonomous AI Trading Intelligence (ATI)
**Location:** C:\Users\USER\Desktop\A-T-I\Trading-Intelligence
**Architecture:** Hexagonal (domain → application → infrastructure → presentation)
**Python:** py -3 (CPython 3.14.3)
**Test command:** py -3 -m pytest (from Trading-Intelligence dir)
**Server:** py -3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
**Paper mode:** py -3 -m backend.main --mode paper

**The AI is the trader. Rules exist only as safety constraints.**

---

## 2. CURRENT VERIFIED STATE (What Actually Works)

### 2.1 AI Chain (FREE, NO KEYS NEEDED)
- Chain: OVH → Kilo → LLM7 (all keyless, verified working)
- OVH: Qwen3-Coder-30B-A3B-Instruct, https://oai.endpoints.kepler.ai.cloud.ovh.net/v1
- Kilo: tencent/hy3:free, https://api.kilo.ai/api/gateway/v1, max_tokens=4000
- LLM7: DeepSeek-V4-Flash-0731, https://api.llm7.io/v1 (last resort, says 'hold' too often)
- Code: backend/infrastructure/ai/smart_fallback_reasoner.py
- Prompt: backend/infrastructure/ai/prompt_builder.py (SYSTEM_PROMPT v1, sort_keys=True)
- Shims: backend/application/decision/smart_fallback_reasoner.py (imports from infrastructure)
- Verified: provider used=ovh, decision=enter_long size=0.25 conf=0.85

### 2.2 MEXC Exchange (PRIMARY CRYPTO DATA)
- REST: https://api.mexc.com/api/v3/
- WebSocket: wss://wbs.mexc.com/ws (use BTC_USDT with underscore!)
- Total pairs: 2,119 / USDT pairs: 1,710
- BTC price: ~$80,678
- API Key: mx0vgll26KNTRqixl7
- API Secret: bfe12068825741dc9be7f5f4bd586233
- Key has FULL ACCESS (spot trade enabled)
- Public endpoints (klines, depth, ticker) need NO key
- Rate limit: ~1200 req/min
- REST verified: GET /api/v3/ticker/price?symbol=BTCUSDT returns real price
- REST verified: GET /api/v3/exchangeInfo returns all 2119 pairs
- REST verified: GET /api/v3/klines?symbol=BTCUSDT&interval=1h&limit=200 returns OHLCV
- WebSocket subscription format: spot@public.deals.v3.api@BTC_USDT (WITH underscore)

### 2.3 Gate.io Exchange (BACKUP CRYPTO DATA)
- WebSocket: wss://api.gateio.ws/ws/v4/
- Verified LIVE: BTC/USDT streaming at $78,943
- Subscription format: {"time": ts, "channel": "spot.trades", "event": "subscribe", "payload": ["BTC_USDT"]}
- Channels: spot.trades, spot.book_ticker, spot.book, spot.candlesticks
- Code: backend/infrastructure/data_fabric/connectors/crypto/gateio.py

### 2.4 MT5 (FOREX - DEMO ACCOUNT)
- MetaTrader5 Python: v5.0.6090 installed
- Terminal path: C:\Users\USER\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075
- Program path: C:\Program Files\MetaTrader 5
- Account: 111620066
- Server: MetaQuotes-Demo
- Balance: 100,000.00 USD (DEMO)
- Leverage: 1:100
- Trade mode: 0 (DEMO)
- Total symbols: 12,494
- EURUSD: 1.16604 (live tick verified)
- GBPUSD: 1.36291 (live tick verified)
- USD pairs: 40 available
- BTC CFDs: BTC, BTCL, BTCO, BTCW, BTCZ (available but not primary)
- MT5 terminal MUST be running for Python API to connect (IPC)
- Code: backend/infrastructure/execution/mt5/bridge.py (80% built, needs completion)

### 2.5 NETWORK STATUS (What's Reachable)
- ✅ api.mexc.com (MEXC REST)
- ✅ wbs.mexc.com (MEXC WebSocket)
- ✅ api.gateio.ws (Gate.io)
- ✅ api.groq.com (AI)
- ✅ openrouter.ai (AI)
- ✅ api.llm7.io (AI)
- ✅ api.kilo.ai (AI)
- ✅ oai.endpoints.kepler.ai.cloud.ovh.net (AI)
- ✅ google.com, cloudflare.com
- ✅ api.deriv.com, ws.derivws.com
- ❌ api.binance.com (BLOCKED)
- ❌ stream.binance.com (BLOCKED)
- ❌ advanced-trade-ws.coinbase.com (BLOCKED)
- ❌ ws.kraken.com (BLOCKED)
- ❌ stream.bybit.com (BLOCKED)
- ❌ api.bitget.com (BLOCKED)
- ❌ www.okx.com (BLOCKED)
- VPN makes things WORSE (destabilizes entire network)

### 2.6 EXECUTION LAYER (VERIFIED)
- Paper simulator: 7 trade paths verified with EXACT parity (delta=0.00000000)
  - Long entry/exit ✅
  - Short signed-PnL ✅
  - Fee accounting (0.04% taker) ✅
  - OCO bracket trigger ✅
  - Funding cost accrual ✅
  - SCALE_OUT partial close ✅
  - Multi-symbol portfolio ✅
- Parity report: docs/ATI_PARITY_REPORT_001.md
- Risk gate: 7 paths verified, no bypass found in 100-proposal fuzz
- Code: backend/application/simulation/paper_trading_simulator.py
- Fill engine: backend/application/simulation/paper_fill_engine.py

### 2.7 EVIDENCE LAYER (BUILT)
- 7 passports: BTC/ETH/SOL/BTC-4H/BNB/XRP/BTC-1D, all OBSERVE
- Evidence engine: verdict gates (PBO > 0.5 → REJECT, DSR ≤ 0 → REJECT)
- Population ladder: 7 members, BTC-1D ranked #1 (+1.73% mean excess)
- Capital allocator: refuses all 7 (never allocate on insufficient evidence)
- Code: backend/application/research/

### 2.8 DASHBOARD (BUILT, WORKING)
- Location: http://127.0.0.1:8000
- File: backend/presentation/static/index.html (46KB SPA)
- Views: Command Center, Market, AI Intelligence, Portfolio, Risk, Execution, System Health
- All 8 API endpoints verified 200 OK
- CSP fixed to allow CDN fonts/charts and inline styles/scripts
- Chart: lightweight-charts from unpkg CDN
- Screenshots verified: all 7 views rendering with real data

---

## 3. CREDENTIALS & CONFIGURATION

### 3.1 .env file (at Trading-Intelligence/.env)
```
PAPER_MODE=true
LIVE_TRADING_AUTHORIZED=false
MT5_MAGIC_NUMBER=123456
MT5_DATA_FOLDER=C:\Users\USER\AppData\Roaming\MetaQuotes\Terminal\D0E8209F77C8CF37AD8BF550E51FF075
DB_PATH=data/trading_intelligence.db
API_KEY=
API_ENV=development
API_HOST=127.0.0.1
API_PORT=8000
CCXT_ENABLED=true
CCXT_SANDBOX=true
CRYPTO_SYMBOLS=BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT
CRYPTO_CHANNELS=trade,ticker,book,candle
OMEGA_ENABLED=true
OMEGA_RACE_MODE=sequential
SAGAX_KEYS_PATH=C:\Users\USER\Desktop\SagaxAI-API-Keys\api_keys.env
RISK_PER_TRADE_PCT=0.005
STARTING_EQUITY=10000.0
PROP_FIRM=fundingpips
PROP_MODEL=flex
EXECUTION_POLICY=always_market
LOG_LEVEL=INFO
```

### 3.2 MEXC Credentials (STORE IN .env, NEVER HARDCODE)
```
MEXC_API_KEY=mx0vgll26KNTRqixl7
MEXC_API_SECRET=bfe12068825741dc9be7f5f4bd586233
```

### 3.3 AI Provider Keys (in Sagax file, auto-loaded)
- Groq: 4 keys (currently 403 - expired, need refresh)
- OpenRouter: 4 keys (untested)
- Zen: 401 (needs OPENCODE_ZEN_API_KEY)

---

## 4. WHAT WE'RE BUILDING (THE PLAN)

### USER PARAMETERS
- Risk per trade: 0.5% ($50 max loss on $10K)
- Starting capital: 10,000 USDT
- Both crypto (MEXC paper) and forex (MT5 demo) side by side
- 5 crypto pairs: BTCUSDT, ETHUSDT, SOLUSDT, XRPUSDT, DOGEUSDT
- 5 forex pairs: EURUSD, GBPUSD, AUDUSD, USDCAD, USDCHF
- User wants: real-time visuals, candlestick charts, data flow visualization
- User wants: 550+ pairs selectable, position sizing controls
- User wants: MT5 for forex, MEXC for crypto, both running simultaneously

### PHASE 1 — Crypto Trading on MEXC (CURRENT PRIORITY)

**Goal:** AI makes enter_long/enter_short decisions on live MEXC data, paper simulator fills them.

**Step 1.1 — Environment**
- Add MEXC_API_KEY and MEXC_API_SECRET to .env
- Set RISK_PER_TRADE_PCT=0.005 (0.5%)
- Set STARTING_EQUITY=10000

**Step 1.2 — MEXC OHLCV Fetcher**
- File: backend/infrastructure/data_fabric/connectors/crypto/mexc.py
- Add fetch_klines(symbol, interval, limit) using REST GET /api/v3/klines
- Add fetch_order_book(symbol, limit) using GET /api/v3/depth
- Add fetch_all_pairs() using GET /api/v3/exchangeInfo
- Fix WebSocket symbol format: BTC_USDT (underscore)

**Step 1.3 — Market Data API Routes**
- File: backend/presentation/api/routes_market.py (NEW FILE)
- GET /v1/market/klines?symbol=BTCUSDT&interval=1h&limit=200
- GET /v1/market/pairs (all 1710 USDT pairs)
- GET /v1/market/depth?symbol=BTCUSDT&limit=20
- GET /v1/market/ticker?symbol=BTCUSDT
- These fetch LIVE from MEXC REST on each call

**Step 1.4 — Feature Pre-Warming**
- File: backend/application/pipeline/market_loop_service.py
- On startup, fetch last 200 candles from MEXC REST
- Feed as TRADE events into ingest pipeline
- This warms trend (2+ prices), momentum (2+), volatility (3+)
- Without this, features are cold and solver always returns stand_aside

**Step 1.5 — Fix RuleBasedSolver**
- File: backend/application/decision/rule_based_solver.py
- Current: needs trend.direction + momentum.rate_of_change_pct to agree
- Fix: add configurable entry_threshold, log why stand_aside
- Add settings: solver_entry_threshold, solver_volatility_cap
- Lower momentum requirement from >0 to >entry_threshold

**Step 1.6 — Wire ExecutionPolicy**
- File: backend/application/pipeline/decision_pipeline_service.py
- Read settings.execution_policy
- Import build_execution_policy from execution_policy.py
- Call policy.plan_execution() after risk gate approval

**Step 1.7 — Dashboard with Candlestick Chart**
- File: backend/presentation/static/index.html
- TradingView Lightweight Charts (CDN, free)
- Fetch from /v1/market/klines → render candlesticks
- Pair selector: searchable dropdown of 1710 pairs
- Timeframe: 1m, 5m, 15m, 1h, 4h, 1d
- Auto-refresh every 5s
- Markers for AI entry/exit

### PHASE 2 — MT5 Forex Integration

**Step 2.1 — Complete MT5Bridge**
- File: backend/infrastructure/execution/mt5/bridge.py
- Already has: MT5Bridge class, submit_order, start/stop, _run_loop
- Need to add: get_account_info, get_positions, get_rates, get_ticks, close_position

**Step 2.2 — MT5 Data Feed**
- File: backend/infrastructure/data_fabric/connectors/forex/mt5_feed.py (NEW)
- Polls MT5 for tick data every 500ms
- Publishes NormalizedEvent to data fabric bus
- Pairs: EURUSD, GBPUSD, AUDUSD, USDCAD, USDCHF

**Step 2.3 — Forex Pipeline**
- File: backend/application/pipeline/forex_pipeline.py (NEW)
- Own risk gate, own decision pipeline
- Uses MT5Bridge for execution (not paper simulator)
- Requires MT5_DEMO_MODE=true safety flag

**Step 2.4 — MT5 API Routes**
- File: backend/presentation/api/routes_mt5.py (NEW)
- GET /v1/mt5/account (balance, equity, margin)
- GET /v1/mt5/positions (open positions)
- GET /v1/mt5/symbols (available pairs)
- POST /v1/mt5/order (place order)

**Step 2.5 — Dashboard Forex Panel**
- Second chart (forex pair selector)
- MT5 account summary
- Forex positions table

### PHASE 3 — Real-Time Streaming UI

**Step 3.1 — FastAPI WebSocket**
- File: backend/main.py
- Add @app.websocket("/ws") endpoint
- Streams: price, decisions, equity, positions, risk every 1 second

**Step 3.2 — Dashboard WebSocket Client**
- File: backend/presentation/static/index.html
- const ws = new WebSocket("ws://127.0.0.1:8000/ws")
- Replace polling with WebSocket for instant updates

**Step 3.3 — Data Flow Visualization**
- Animated pipeline diagram: Data → Ingest → Features → AI → Risk → Execute
- Each node lights up when data flows
- Events/second counter at each stage

**Step 3.4 — Order Book Depth**
- Horizontal bar chart: bids (green) below mid, asks (red) above
- From MEXC depth endpoint, updates every second

**Step 3.5 — Trade Markers on Chart**
- Green triangle: AI entered long
- Red X: AI entered short
- Yellow star: bracket fired
- From real trade data

**Step 3.6 — Equity Curve**
- Line chart of portfolio equity over time
- Updates as trades close
- Shows drawdown visually

---

## 5. KEY FILE LOCATIONS

### Backend Structure
```
backend/
  main.py                          # FastAPI app + lifespan + composition root
  cli.py                           # CLI tool for research
  domain/                          # Pure business logic (NO imports from application+)
    context/                       # Market context, features, regime
      features/                    # trend, momentum, volatility, micro_price, order_flow, etc.
      regime_detector.py           # HMM + CUSUM
    decision/                      # Proposal, trade plan, evidence
    execution/                     # Order, position, trade_record, PnL, funding, attribution
    research/                      # Dataset, PBO/DSR, passport, edge_monitor, capacity
    risk/                          # RiskDecision
    observation/                   # ObservationEvent, adapter_interface
    data_fabric/                   # Envelope, instrument, source configs
    memory/                        # MemoryEpisode
    validation/                    # ADWIN config
  application/                     # Use cases, ports, pipeline services
    context/                       # bootstrap.py (composition root for all builders)
    pipeline/                      # context_pipeline_service, decision_pipeline_service, market_loop_service
    decision/                      # rule_based_solver, quant_momentum_scorer
      prompt_builder.py            # SHIM → infrastructure/ai/prompt_builder
      omni_route_reasoner.py       # SHIM → infrastructure/ai/omni_route_reasoner
      smart_fallback_reasoner.py   # SHIM → infrastructure/ai/smart_fallback_reasoner
    execution/                     # execution_policy.py (NEW - ADR 0029), execution_core.py, reconciliation_service.py
    risk/                          # circuit_breaker_risk_gate.py, vpin.py
    simulation/                    # paper_trading_simulator.py, paper_fill_engine.py
    research/                      # evidence_run, decision_pipeline_evaluator, pbo, edge_monitor, etc.
    portfolio/                     # portfolio_risk.py (HRP/CVaR, lazy imports)
    sentiment/                     # sentiment_service.py (GDELT + FinBERT, lazy torch)
    supervisor/                    # supervisor_service.py
    reflection/                    # reflection_service.py
    interfaces/                    # ALL PORTS (ai_reasoner, risk_gate, order_gateway, etc.)
    ai/                            # pydantic_ai_reasoner.py (SHIM)
    validation/                    # adwin.py, purged_cv.py, tick_recorder.py
    backtest/                      # backtest_runner.py
  infrastructure/                  # Adapters, implementations
    ai/                            # omni_route_reasoner, smart_fallback_reasoner, prompt_builder, pydantic_ai_reasoner
    config/                        # settings.py, context_loader.py
    data_fabric/                   # service.py, event_bus.py, quality_monitor.py
      connectors/
        crypto/                    # binance, gateio (NEW), mexc (NEW), coinbase, kraken, bybit
        forex/                     # fxcm, oanda
        macro/                     # central_banks, forex_factory
        news/                      # gdelt, rss_news
    observation/                   # ccxt_adapter.py, observation_bus.py
    execution/                     # ccxt_gateway.py
      mt5/                         # bridge.py (80% built)
    broker/                        # deriv/connector.py
    mt5/                           # ea/bridge.py
    sqlite/                        # ALL repositories (observation, context, proposal, ledger, memory, passport, etc.)
    secrets/                       # sagax_loader.py
  presentation/
    api/                           # ALL FastAPI routes
      auth.py                      # verify_api_key (fail-closed)
      routes_context.py            # /v1/context/*
      routes_decision.py           # /v1/proposals/*, /v1/ledger/*, /v1/simulator
      routes_drive.py              # POST /v1/drive
      routes_memory.py             # /v1/memory/*
      routes_reconciliation.py     # /v1/reconcile/*
      routes_supervisor.py         # /v1/supervisor/*
    static/                        # index.html (dashboard SPA)
  cli.py                           # backend.cli command-line tool
```

### Key Configuration Files
```
pyproject.toml                     # ruff, mypy config
pytest.ini                         # pytest config with norecursedirs
config/context.yaml                # feature configuration
.env                               # environment (gitignored)
.env.example                       # template
Dockerfile                         # Python 3.13
docker-compose.yml                 # Full stack
```

### Documentation
```
docs/ATI_BACKLOG.md                # Permanent work memory (session log)
docs/ATI_CONTINUOUS_200.md         # 200-task list (200/200 complete)
docs/ATI_OMEGA_200_TASKS.md        # Omega tasks (200/200 complete)
docs/ATI_MODEL_INVENTORY.md        # AI provider benchmark
docs/ATI_PARITY_REPORT_001.md      # Execution parity results
docs/ATI_OPEN_SOURCE_RESEARCH_2026.md  # Research dossier
docs/adr/0029-execution-policy-port.md # ExecutionPolicy ADR
docs/Constitution/                 # Engineering constitution
```

---

## 6. KEY API SHAPES (Verified)

### /v1/simulator
```json
{
  "equity": 100000.0,
  "positions": {},
  "risk": {
    "account_equity": 100000.0,
    "open_exposure_pct": 0.0,
    "daily_loss_pct": 0.0,
    "monthly_loss_pct": 0.0,
    "total_loss_pct": 0.0,
    "drawdown_pct": 0.0,
    "position_count": 0,
    "symbol_risk_used_pct": 0.0,
    "portfolio_risk_used_pct": 0.0
  }
}
```

### /v1/proposals/recent
```json
{
  "proposals": [{
    "proposal_id": "prop-BTCUSDT-...",
    "symbol": "BTCUSDT",
    "created_at": "2026-08-24T...",
    "confidence": 0.95,
    "actions": [{"action_type": "stand_aside", "size_fraction": 0.1}],
    "hypothesis": {"statement": "...", "supporting_evidence": [{"source": "trend", "summary": "..."}]},
    "rationale": "..."
  }]
}
```

### /v1/ledger/recent
```json
{
  "trades": [{
    "trade_id": "trade-2-1",
    "symbol": "btcusdt",
    "side": "buy",
    "quantity": 99.01,
    "entry_price": 101.01,
    "exit_price": null,
    "realized_pnl": null,
    "status": "open"
  }]
}
```

### /v1/supervisor/status
```json
{
  "status": "healthy",
  "reason": "All systems nominal.",
  "stale_symbols": [],
  "kill_switch_engaged": false
}
```

### MEXC REST API
```
GET https://api.mexc.com/api/v3/ticker/price?symbol=BTCUSDT
  → {"symbol": "BTCUSDT", "price": "80678.39"}

GET https://api.mexc.com/api/v3/klines?symbol=BTCUSDT&interval=1h&limit=200
  → [[open_time, open, high, low, close, volume, close_time, ...], ...]

GET https://api.mexc.com/api/v3/depth?symbol=BTCUSDT&limit=20
  → {"bids": [[price, qty], ...], "asks": [[price, qty], ...]}

GET https://api.mexc.com/api/v3/exchangeInfo
  → {"symbols": [{"symbol": "BTCUSDT", "status": "ENABLED", ...}, ...]}

GET https://api.mexc.com/api/v3/trades?symbol=BTCUSDT&limit=100
  → [{"price": "...", "qty": "...", "time": ..., ...}, ...]
```

### MEXC WebSocket
```
URL: wss://wbs.mexc.com/ws
Subscribe: {"method": "SUBSCRIPTION", "params": ["spot@public.deals.v3.api@BTC_USDT"]}
Symbol format: BTC_USDT (WITH underscore)
Channels: spot@public.deals.v3.api@{SYMBOL}, spot@public.bookTicker.v3.api@{SYMBOL}, spot@public.limit.depth.v3.api@{SYMBOL}@20
```

### Gate.io WebSocket
```
URL: wss://api.gateio.ws/ws/v4/
Subscribe: {"time": ts, "channel": "spot.trades", "event": "subscribe", "payload": ["BTC_USDT"]}
Channels: spot.trades, spot.book_ticker, spot.book, spot.candlesticks
Symbol format: BTC_USDT (WITH underscore)
```

### MT5 Python API
```python
import MetaTrader5 as mt5
mt5.initialize()  # Connect to running terminal
mt5.account_info()  # → account info (login, balance, equity, etc.)
mt5.symbols_get()  # → all 12494 symbols
mt5.symbol_info_tick("EURUSD")  # → {bid, ask, last, time}
mt5.copy_rates_from_pos("EURUSD", mt5.TIMEFRAME_M1, 0, 100)  # OHLCV bars
mt5.order_send(request)  # Place order
mt5.positions_get()  # Open positions
mt5.shutdown()  # Disconnect
```

---

## 7. IMPORTANT DECISIONS MADE

1. **Zen removed from AI chain** — permanent 401, dead weight
2. **Groq left aside** — keys expired (403), user said "leave groq first"
3. **OVH is primary AI** — best quality, verified with real trading decisions
4. **Kilo max_tokens=4000** — hy3 reasoning field eats budget, 4000 prevents truncation
5. **Gate.io is primary crypto data** — MEXC WS subscription blocked, Gate.io verified working
6. **MEXC REST is primary for klines/depth** — works without WS
7. **Risk per trade = 0.5%** — user specified
8. **Starting equity = 10,000** — user specified
9. **Crypto = paper trading** — safe, learn first
10. **Forex = MT5 demo** — real execution but demo account
11. **ExecutionPolicy port** — ADR 0029, AlwaysMarket default, PassiveIfSpreadTight optional
12. **CSP fixed** — was blocking all CSS/JS/fonts, now allows CDN + inline
13. **Zen test fixtures migrated to ovh** — all 16 tests updated
14. **make_reasoner pins mocked providers** — prevents live keyless providers leaking into tests

---

## 8. KNOWN ISSUES / TECH DEBT

1. **Groq keys expired** — all 4 return 403. User needs fresh keys from console.groq.com
2. **MT5 terminal must be running** — Python API uses IPC, requires same machine
3. **MEXC WS subscription blocked** — "Blocked!" error on subscription (geo-restriction on WS channel, REST works fine)
4. **Deriv symbols invalid** — app_id 1089 doesn't have access to frx* symbols, user needs own DERIV_APP_ID
5. **Disk space low** — C: has ~588 MB free, clean __pycache__ and old logs regularly
6. **bootstrap.py is a god module** — 25+ build_* functions, needs splitting (tech debt, not urgent)
7. **bootstrap.py imports infrastructure** — application→infrastructure violation (acknowledged, composition root exception)
8. **No WebSocket endpoint yet** — dashboard uses polling (3s), WebSocket is Phase 3
9. **No candlestick chart data endpoint yet** — dashboard chart is empty, needs /v1/market/klines
10. **RuleBasedSolver always stand_aside on live data** — features not warmed up + threshold too strict, Phase 1 fixes this

---

## 9. HOW TO CONTINUE AFTER COMPACTION

1. Read this file completely
2. Check server status: `py -3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/health').read())"`
3. If server not running: `cd C:\Users\USER\Desktop\A-T-I\Trading-Intelligence && py -3 -m uvicorn backend.main:app --host 127.0.0.1 --port 8000`
4. Run tests: `py -3 -m pytest -q` (expect 1707+ passed)
5. Run lint: `py -3 -m ruff check backend tests` (expect clean)
6. Run type check: `py -3 -m mypy backend` (expect clean)
7. Continue building from where the last session left off (check git status for uncommitted changes)
8. **NEVER wire the autonomy ladder into the live path** (constitution guardrail)
9. **NEVER enable live trading without explicit operator authorization**
10. **ALWAYS run tests after every change**

---

## 10. BUILD PROGRESS TRACKER

### Phase 1 — Crypto Trading (MEXC)
- [ ] 1.1 Environment setup (.env with MEXC keys, risk=0.5%, equity=10000)
- [ ] 1.2 MEXC OHLCV fetcher (fetch_klines, fetch_order_book, fetch_all_pairs)
- [ ] 1.3 Market data API routes (/v1/market/klines, /v1/market/pairs, /v1/market/depth, /v1/market/ticker)
- [ ] 1.4 Feature pre-warming (fetch 200 candles before going live)
- [ ] 1.5 Fix RuleBasedSolver entry threshold
- [ ] 1.6 Wire ExecutionPolicy into pipeline
- [ ] 1.7 Dashboard candlestick chart + pair selector

### Phase 2 — MT5 Forex
- [ ] 2.1 Complete MT5Bridge
- [ ] 2.2 MT5 forex data feed
- [ ] 2.3 Forex decision pipeline
- [ ] 2.4 MT5 API routes
- [ ] 2.5 Dashboard forex panel

### Phase 3 — Real-Time Streaming UI
- [ ] 3.1 FastAPI WebSocket endpoint
- [ ] 3.2 Dashboard WebSocket client
- [ ] 3.3 Data flow visualization
- [ ] 3.4 Order book depth chart
- [ ] 3.5 Trade markers on chart
- [ ] 3.6 Equity curve chart
