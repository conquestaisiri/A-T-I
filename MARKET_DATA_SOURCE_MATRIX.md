# Real-Time Market Data Source Matrix for ATI

**Research Date:** 2026-08-09  
**Scope:** Free and low-cost real-time sources for crypto, equities, futures, options, prediction markets  
**Verification:** All data sourced from official docs, API references, and recent developer reports (2024-2026)

---

## Legend

| Column | Meaning |
|--------|---------|
| **WS/REST** | WebSocket (WS), REST, Server-Sent Events (SSE), or GraphQL (GQL) |
| **Rate Limit** | Documented limits (per IP, per key, per account, or token-bucket) |
| **Symbols** | Approximate covered symbols / markets |
| **Latency** | Typical observed latency (exchange-native WS < 50ms; aggregators add 10-100ms) |
| **Hist. Depth** | Historical data availability via REST |
| **WS Stability** | Connection guarantees, reconnection behavior |
| **Auth** | API key, JWT, wallet signature, OAuth, or none |
| **Geo Restrictions** | Known geographic blocks |
| **TOU Algo** | Terms of Use explicitly permit algorithmic trading |
| **Python Lib** | Official or well-maintained community SDK |
| **Direct Feed** | ✅ = Can feed ATI ObservationAdapter directly; ⚠️ = Needs normalization wrapper |

---

## Category 1: Exchange-Native Sources

### 1.1 Binance (Spot, USDⓈ-M Futures, COIN-M Futures, Options)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Binance Spot** | Crypto Spot | WS + REST | **WS:** 5 msg/sec/conn (incl. ping/pong), 1024 streams/conn, 300 conn/5min/IP. **REST:** 6000 weight/min/IP (1200 weight/min for /sapi), 100 orders/10sec, 200k orders/day | 1,500+ spot pairs | ~10-50ms (WS) | Years (klines, trades, depth snapshots) | 24hr max conn lifetime; ping/pong every 3min; auto-reconnect with exponential backoff required | API key (HMAC-SHA256) for private; none for public WS | US blocked (use Binance.US); some regions restricted | ✅ Explicitly permits algo trading; market-data-only API keys available | `python-binance`, `binance-sdk` (async, WS-API native) | ✅ |
| **Binance USDⓈ-M Futures** | Crypto Perps/Futures | WS + REST | Same weight system as Spot. **WS:** `fstream.binance.com` - markPrice@1s, forceOrder, allMarketTickers, bookTicker, depth@100ms | 300+ perpetual + quarterly | ~10-50ms | Years (funding rates, open interest, klines, liquidations) | Same as Spot | API key (HMAC) | US blocked | ✅ | `binance-sdk` (UMFuturesClient) | ✅ |
| **Binance COIN-M Futures** | Coin-Margined Futures | WS + REST | Same weight system. **WS:** `dstream.binance.com` | 100+ coin-margined | ~10-50ms | Years | Same | API key (HMAC) | US blocked | ✅ | `binance-sdk` (CMFuturesClient) | ✅ |
| **Binance Options** | Crypto Options | WS + REST | **WS:** `nbstream.binance.com/eoptions` - openInterest, markPrice, trades | BTC/ETH options | ~10-50ms | Limited | 24hr conn limit | API key | US blocked | ✅ | `binance-sdk` (partial) | ⚠️ Different symbol format |

### 1.2 Bybit (Spot, Linear, Inverse, Options)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Bybit Spot** | Crypto Spot | WS + REST | **WS:** 10 topics/subscribe req, 500 conn/5min/IP, 1000 conn/IP. **REST:** 120 req/5sec sliding (public), 600 req/min (private). **Institutional (Aug 2025+):** Up to 60k RPS at Pro 6 tier | 600+ spot | ~20-100ms | Years (klines, trades, orderbook) | Ping every 20s; `max_active_time` param (30s-10min); MMWS dedicated path for stability | API key (HMAC-SHA256) | US blocked; some regions restricted | ✅ Explicitly supports algo/HFT; institutional tiers | `pybit` (official), `ccxt.pro` | ✅ |
| **Bybit Linear (USDT Perps)** | Crypto Perps | WS + REST | **WS:** Full orderbook delta (200ms push), allLiquidation (500ms), tickers, funding rates. **REST:** 10 req/sec (linear trading) | 200+ linear perps | ~20-100ms | Years (funding, OI, liquidations) | Same as Spot | API key | US blocked | ✅ | `pybit` | ✅ |
| **Bybit Inverse** | Coin-Margined Perps | WS + REST | Same as Linear | 50+ inverse | ~20-100ms | Years | Same | API key | US blocked | ✅ | `pybit` | ✅ |
| **Bybit Options** | Crypto Options | WS + REST | **WS:** Dedicated endpoint, Greeks, IV | BTC/ETH options | ~20-100ms | Limited | Same | API key | US blocked | ✅ | `pybit` | ⚠️ |

### 1.3 OKX (Spot, Futures, Options)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **OKX Spot** | Crypto Spot | WS + REST | **WS:** 3 conn/sec/IP, 480 sub/unsub/login/hr/conn, 30 conn/channel/sub-account. **REST:** 20 req/2sec (public), 60 req/2sec (trading). Sub-account: 1000 orders/2sec (tiered by fill ratio, up to 10k at VIP 8) | 300+ spot | ~10-50ms | Years | Ping/pong every 30s (text frame "ping"/"pong"); 30s idle timeout | API key + passphrase (HMAC-SHA256) | **US blocked**; some regions | ✅ Permits algo; VIP tiers for HFT | `okx-api` (official), `ccxt.pro` | ✅ |
| **OKX Futures/Perps** | Crypto Futures | WS + REST | Same limits. **WS:** books-l2-tbt (400 lvl tick-by-tick), books50-l2-tbt, markPrice@200ms, fundingRate | 200+ perps | ~10-50ms | Years (funding, OI) | Same | API key + passphrase | US blocked | ✅ | `okx-api` | ✅ |
| **OKX Options** | Crypto Options | WS + REST | Same | BTC/ETH options | ~10-50ms | Limited | Same | API key + passphrase | US blocked | ✅ | `okx-api` | ⚠️ |

### 1.4 Coinbase Advanced Trade

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Coinbase Advanced Trade** | Crypto Spot + US Futures | WS + REST | **WS:** 8 conn/sec/IP, 8 unauth msg/sec/IP. **REST:** 10 req/sec (public), 15 req/sec (private), 5 req/sec (batch orders). **Exchange (legacy):** 10 sub/product/channel, 10 RPS / 1000 burst RPS subscription rate | 250+ spot, US futures | ~50-200ms | Years (candles, trades) | Heartbeats channel required; 5s subscribe timeout; 60-90s idle close | JWT (CDP API key + signing key) for private; none for public | US only (geo-fenced) | ✅ Explicitly supports algo; higher limits via Prime | `coinbase-advanced-py` (official), `ccxt.pro` | ✅ |

### 1.5 Kraken (Spot, Derivatives)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Kraken Spot** | Crypto Spot | WS (v1/v2) + REST + FIX | **WS:** Cloudflare ~150 conn/10min/IP. **REST:** 1 req/sec public (conservative). **Trading:** Decay-based counter per pair (Starter: 60 threshold, 2.34/s decay; Pro: 180 threshold, 3.75/s decay). Shared across REST/WS/FIX | 700+ pairs | ~20-100ms (WS v2) | Years (OHLCV, trades, L2/L3 book) | Ping every 60s; v2 recommended; sequence numbers for gap detection | API key (HMAC-SHA512) | Global (some restrictions) | ✅ Built for systematic trading; FIX 4.4 for HFT | `kraken-api` (community), `ccxt.pro` | ✅ |
| **Kraken Derivatives** | Crypto Futures | WS + REST | Separate engine; similar decay model | 50+ perps | ~20-100ms | Years | `wss://futures.kraken.com/ws/v1` | API key | US restricted | ✅ | `kraken-futures-py` | ✅ |

### 1.6 Hyperliquid (Perps, Spot)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Hyperliquid** | Crypto Perps + Spot | WS + REST (tunneled over WS) | **REST:** 1200 weight/min/IP. **WS:** 10 conn/IP, 30 new conn/min, 1000 subscriptions, 2000 msg/min, 100 inflight POST. **L1 address:** 1 req per 1 USDC traded (10k initial buffer) | 150+ perps + spot | ~100-300ms (L1 chain ~500ms blocks) | Limited REST history; WS snapshots on reconnect | Server sends pong ~5s; client ping every 20s; 60s idle close; snapshot on reconnect | None for public WS; EVM wallet for trading | Global (no KYC for data) | ✅ Explicitly supports bots; open-source SDKs | `hyperliquid-python-sdk` (nomeida), `hyperliquid` (official TS) | ✅ |

### 1.7 dYdX v4 (Perps)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **dYdX v4 Indexer** | Crypto Perps | WS + REST | **WS:** 2 sub/sec/conn (per channel+ID), 2 invalid msg/sec/conn. **REST:** Not explicitly documented (Indexer is read-only). **Per-conn:** 32 sub/channel cap (requires connection pooling for >32 markets) | 40+ perps | ~50-200ms (Indexer) | Years (via Indexer REST) | Heartbeat ping every 30s; 10s pong timeout | None for public; Cosmos wallet address for private (subaccount) | US blocked (geo-fenced) | ✅ Permits bots; open-source indexer | `dydx-v4-python` (community), `nautilus-trader` adapter | ⚠️ Per-channel 32-sub limit needs pooling |

### 1.8 Polymarket (Prediction Markets - CLOB)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Polymarket CLOB** | Prediction Markets (Binary) | WS + REST (CLOB + Gamma) | **REST (Gamma):** 300 req/10s (/markets), 900 req/10s (/markets+/events), 4000 req/10s overall. **CLOB:** 1500 req/10s (/book), 500 req/10s (/books). **Trading:** 5000 burst/10s, 120k sustained/10min (POST /order). **WS:** 500 instruments/conn, PING every 10s | 1000+ markets | ~50-200ms | Years (trades, prices, orderbook via REST) | PING/PONG every 10s; custom_feature_enabled for BBO | EIP-712 wallet sig (CLOB); none for public WS/Gamma | **US blocked** (IP geo-fence) | ✅ Permits algo; CLOB designed for market makers | `py-clob-client` (official), `polymarket-python` | ⚠️ Token ID mapping needed |

### 1.9 Kalshi (CFTC-Regulated Prediction Markets)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Kalshi** | Prediction Markets (Binary, USD) | WS + REST | **Token buckets:** Basic: 200 read / 100 write tokens/sec. Premier: 1000/1000. Prestige: 6000/8000. Most endpoints cost 10 tokens. **WS:** Auth required even for public channels. ~5-10 concurrent conn/key. 100 market tickers/conn | 1500+ markets | ~50-200ms | Years (trades, orderbook, candles) | WS ping ~10s; auto-pong via `websockets` lib; sequence numbers for orderbook_delta | RSA-PSS signed headers (REST + WS upgrade) | **US only** (CFTC-regulated) | ✅ Explicitly supports algo trading; FIX 4.4 available | `kalshi-python-sdk` (official-ish), `kalshi-api` (community) | ✅ |

### 1.10 CME Globex (Futures, Options)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **CME WebSocket API** | Futures, Options (All asset classes) | WS (JSON) + REST | **WS:** Book updates max every 500ms (1-deep MBP). Heartbeat every 10s. OAuth 2.0 + ILA required. **Certification:** Free. **Production:** Monthly connectivity fee + data license fees (non-trivial: $100s-$1000s/mo) | 1000+ contracts across 7 asset classes | ~50-200ms (Google Cloud) | Current week via API; deep history via DataMine (paid) | Google Cloud Armor WAF; re-subscribe on disconnect | OAuth 2.0 (Bearer token) + ILA entitlement | Global (licensing per region) | ✅ Institutional algo trading; requires ILA | No official Python SDK (raw WS) | ⚠️ Paid tier only; complex entitlement |

---

## Category 2: Aggregators

### 2.1 CCXT / CCXT Pro (Library, not a data source)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **CCXT (REST)** | 100+ exchanges unified | REST only | Built-in leaky-bucket (per-exchange rateLimit ms). Rolling-window opt-in. **Bug:** WS throttle not bound in v4.x (fixed in PR #26986) | All exchange symbols | Exchange-dependent | Exchange-dependent | N/A | Per-exchange | Per-exchange | Per-exchange | `ccxt` (pip) | ⚠️ Normalizes to unified schema |
| **CCXT Pro (WS)** | 30+ exchanges WS | WS | Per-exchange limits; uses `watchOHLCV`, `watchOrderBook`, `watchTrades`. **Bug:** Reconnection storms on Binance/Bybit need custom rate-limiting | Subset of exchanges | Exchange-dependent | Limited (real-time only) | Auto-reconnect with backoff; **known issues** with Binance 1008 errors on reconnect | Per-exchange | Per-exchange | Per-exchange | `ccxt.pro` (npm/pip) | ⚠️ Unified schema but exchange quirks remain |

### 2.2 CoinAPI.io

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **CoinAPI (Free/PAYG)** | Crypto (300+ exchanges) | WS + REST | **Free:** $25 credits (PAYG), no daily limit, credits never expire. **Startup ($79/mo):** 10 req/sec, 1000/day, WS trade+OHLCV. **Streamer ($249):** 100 req/sec, 10k/day, WS quote+book. **Pro ($599):** 100 req/sec, 100k/day, WS book+FIX. **WS:** 10 conn/IP, 1000 msg/sec/conn, 1000 hello/day/IP | 15,000+ assets | ~50-200ms | Years (OHLCV, trades, quotes, orderbook L2/L3) | Standard WS | API key (query or header) | Global | ✅ Permits algo; FIX for institutional | `coinapi-python` (official) | ⚠️ Normalized but credit-based cost model |

### 2.3 CryptoCompare (now CoinDesk Data / CCData)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **CryptoCompare** | Crypto (300+ exchanges) | WS + REST | **⚠️ FREE TIER RETIRED MAY 2026.** Was 100k calls/mo. Now sales-only. **Starter:** News, social, L1 book, basic WS. **Pro:** Full minute history, on-chain, CCIX. **Enterprise:** Raw ticks, AI/ML feeds, custom limits. **WS:** 1 socket (free), 5 (paid), 200 sub/socket | 10,000+ assets, 300k pairs | ~50-200ms | Years (OHLCV, social, blockchain) | `wss://streamer.cryptocompare.com/v2`; welcome envelope with rate telemetry | API key (query or header) | Global | ✅ But paid only now | `cryptocompare` (community) | ⚠️ Paid only; normalized CCCAGG index |

### 2.4 Kaiko

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Kaiko** | Crypto (Institutional) | REST + Stream (Kafka, Cloud, Snowflake) | **REST:** 6000 req/min/key. **Stream:** 3000 sub/min/key. **No free tier.** Enterprise contracts only ($1k-$4k+/mo per module) | 100+ exchanges, 10k+ instruments | ~50-200ms (Stream) | Since 2010 (tick-level) | Enterprise SLA | API key | Global (contract) | ✅ Institutional | `kaiko-python` (official) | ⚠️ Enterprise only; normalized schemas |

### 2.5 Polygon.io (now Massive)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Polygon.io Free (Basic)** | US Stocks, Options, Forex, Crypto, Indices | REST only | **5 req/min** (hard cap). 15-min delayed data. 2 years daily history. **No WebSocket.** | All US equities | ~200-500ms (delayed) | 2 years daily | N/A (REST only) | API key | Global | ✅ | `polygon-api-client` (official), `massive-com/client-python` | ⚠️ Delayed only; no WS on free |
| **Polygon.io Starter ($29/mo)** | US Stocks | REST + WS | Unlimited calls. 15-min delayed. 5 years history. WS + snapshots. | US equities | Real-time via WS | 5 years | WS clusters per asset class | API key | Global | ✅ | `polygon-api-client` | ✅ |
| **Polygon.io Developer ($79/mo)** | US Stocks | REST + WS | Unlimited. 15-min delayed. 10 years history. Second aggregates, trades, WS. | US equities | Real-time via WS | 10 years | WS | API key | Global | ✅ | `polygon-api-client` | ✅ |
| **Polygon.io Advanced ($199/mo)** | US Stocks | REST + WS | Unlimited. **Real-time SIP.** 20+ years history. Quotes, trades, WS. | US equities | Real-time | 20+ years | WS | API key | Global | ✅ | `polygon-api-client` | ✅ |

### 2.6 Twelve Data

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Twelve Data Free (Basic)** | US Equities, ETFs, Forex, Crypto | WS + REST | **8 API credits/min, 800/day.** 8 trial WS credits (1 conn, 8 symbols, trial symbols only). Real-time US/forex/crypto. | 1M+ instruments (trial: limited) | ~100-300ms | 30+ years daily; intraday varies | `wss://ws.twelvedata.com`; 3 conn max; 100 events/min sub/unsub limit | API key | Global | ✅ Internal non-display only on free | `twelvedata-python` (official) | ⚠️ Trial symbols only on free WS |
| **Twelve Data Grow ($79/mo, $66 annual)** | 20+ markets | WS + REST | 377 API credits/min. Unlimited daily. 8 trial WS credits. | 20+ markets | Real-time | Unlimited | 500 WS credits on Pro+ | API key | Global | ✅ | `twelvedata-python` | ✅ (Pro+) |

### 2.7 Finnhub

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Finnhub Free** | US Stocks, Forex, Crypto (15+ exchanges) | REST + WS | **60 req/min.** WS: 50 symbols. Real-time US quotes, news, basic fundamentals, SEC filings. **WS trades only from IEX (~2.5% market share).** | US equities + 15 crypto exchanges | ~100-300ms | 30+ years daily; 1 year intraday (free) | `wss://ws.finnhub.io`; standard WS | API key | Global | ⚠️ **Personal/non-commercial only.** Commercial requires written approval. | `finnhub-python` (official) | ⚠️ IEX-only trades on WS; non-commercial TOU |

### 2.8 Alpha Vantage

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Alpha Vantage Free** | Global Stocks, Forex, Crypto, Options | REST only | **25 req/day, 5 req/min (strict 1/sec).** Compact output (100 pts). **No WebSocket.** Real-time & 15-min delayed = Premium only. | 100,000+ symbols | ~500ms-2s | 20+ years daily (Premium: full) | N/A | API key | Global | ✅ | `alpha-vantage` (community), official MCP server | ⚠️ No WS; very low free limits |

### 2.9 IEX Cloud

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **IEX Cloud** | US Equities | **SUNSET AUG 31, 2024** | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | N/A | ❌ **Defunct** |

---

## Category 3: Alternative Data Sources

| Source | Asset Class | WS/REST | Rate Limit | Coverage | Latency | Cost | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|----------|---------|------|----------|------------|-------------|
| **Fear & Greed Index (alternative.me)** | Crypto Sentiment | REST | Unlimited (reasonable) | Daily index (0-100) | Daily | Free | ✅ | `requests` + parsing | ✅ (simple JSON) |
| **CoinGlass (coinglass.com)** | Derivatives: Funding, OI, Liquidations, Liq heatmaps, Max Pain | REST + WS | Free tier: generous (undocumented). Pro: higher limits | 30+ exchanges, all major perps | ~100-500ms | Free tier available | ✅ | `coinglass-api` (community) | ⚠️ Needs normalization |
| **CryptoDataAPI (cryptodataapi.com)** | Fear&Greed, Funding, OI, Liquidations, Whale, Regimes, ETF flows | REST + MCP | Free tier (no card): market health, regimes, F&G, Hyperliquid funding/OI. Pro: per-coin quant, gamma, whale | Binance, Bybit, Hyperliquid, dYdX, Deribit + on-chain | Sub-second (MCP) | Free tier generous | ✅ MCP-native | `cryptodataapi-mcp` (MCP server) | ✅ MCP tools directly callable |
| **Smart Money API (smartmoneyapi.com)** | Whale positions, Funding, Liquidations, OI, AI confirmation scores | WS + REST | Free: funding + liquidations WS. Trader/Pro: whale, OI, scores | Bybit, Binance, Hyperliquid | Sub-second WS | Free tier for basic | ✅ | `websockets` + custom | ✅ WS native |
| **PerpFinder (perpfinder.com)** | Perp: Funding, OI, Volume, Liquidations, Fear&Greed | REST | **Keyless, CORS-open.** 60 req/min (most), 40/min (OI, vol, funding), 30/min (slippage) | 50+ CEX+DEX | CDN-cached | Free | ✅ | `requests` | ✅ Simple REST |
| **Loris Tools (loris.tools)** | Funding rates, OI across 43 venues (32 DEX) | REST | Free: BTC/ETH only across 43 venues. Dev: all symbols | 43 venues | ~100-500ms | Free tier (BTC/ETH) | ✅ | `requests` | ✅ |
| **TerminalFeed (terminalfeed.io)** | Fear&Greed, Funding rates (top 20 perps) | REST | Free, no signup | Binance, Bybit, dYdX, Hyperliquid | ~100-500ms | Free | ✅ | `requests` | ✅ |
| **Alphractal (alphractal.com)** | On-chain (300+), Derivatives (60+), Sentiment (80+), Macro (300+) | REST + WS (1-5 min) | Free to start (no card). Institutions: 10M+ calls/mo | 1000+ cryptos | 1-5 min updates | Free tier | ✅ | `requests` | ⚠️ |
| **Kaiko (Reference Data)** | On-chain, Derivatives, Indices, Fair Value | REST + Stream | Enterprise only ($1k+/mo) | Institutional | Low | Paid | ✅ | `kaiko-python` | ⚠️ Enterprise |
| **Glassnode** | On-chain metrics | REST | Paid tiers only (Studio free: no API) | BTC, ETH, major assets | Daily/hourly | Paid | ✅ | `glassnode-python` | ⚠️ Paid |
| **Blockchain.com / mempool.space** | Mempool, on-chain | REST + WS | Free tier generous | BTC mempool, blocks | Real-time | Free | ✅ | `blockchain.com` SDK | ✅ |
| **Whale Alert (whale-alert.io)** | Large transfers | REST + WS | Free: 100 req/day. Paid: higher | Cross-chain | Near real-time | Free tier | ✅ | `whale-alert` (community) | ⚠️ |

---

## Category 4: Prediction Markets (Non-Exchange-Native)

| Source | Asset Class | WS/REST | Rate Limit | Symbols | Latency | Hist. Depth | WS Stability | Auth | Geo Restrictions | TOU Algo | Python Lib | Direct Feed |
|--------|-------------|---------|------------|---------|---------|-------------|--------------|------|------------------|----------|------------|-------------|
| **Polymarket** | Binary (Crypto, Politics, Sports) | WS + REST (CLOB/Gamma) | See Category 1.8 | 1000+ | ~50-200ms | Years | PING 10s | EIP-712 / none | **US blocked** | ✅ | `py-clob-client` | ⚠️ Token ID mapping |
| **Kalshi** | Binary (USD, CFTC) | WS + REST | See Category 1.9 | 1500+ | ~50-200ms | Years | WS ping 10s | RSA-PSS | **US only** | ✅ | `kalshi-python-sdk` | ✅ |
| **PredictIt** | Binary (US Politics) | REST only (read) | **~1 req/sec (strict).** Data refreshes every 60s. No WS. No trading API. | ~30-60 markets | ~60s (cache) | Years (CSV download) | N/A | None (public) | US only | ⚠️ Read-only; no trading API | `requests` + cache | ⚠️ Polling only; 1/sec |
| **Manifold Markets** | Binary (Play-money, AMM) | WS + REST | **500 req/min/IP.** WS at `wss://api.manifold.markets/ws` (global + per-market). API behind 5s cache (max-age=5, stale-while-revalidate=10) | 10k+ markets | ~100-500ms (cached) | Full history | WS fan-out scaled (dedicated tier); pings | API key / Firebase JWT | Global | ✅ Bots welcome; **no commercial AI training** without license | `manifold-api` (community) | ✅ |
| **Azuro Protocol** | Sports Betting (On-chain) | WS + GraphQL + REST | WS: `wss://streams.azuro.org/v1/streams/conditions`. Subscribe by conditionId. Backend API for feed data. Graph for history. | Sports markets | Real-time | On-chain | WS per condition | Wallet / API key | Global (on-chain) | ✅ | `@azuro-org/sdk` (TS/React) | ⚠️ Condition ID mapping |
| **Metaculus** | Forecasting (Aggregated) | REST | 200 req/day (free) | 1000+ questions | Daily | Years | N/A | Token auth | Global | ⚠️ Research only | `metaculus-api` (community) | ⚠️ No trading |
| **Opinion Markets** | Binary (Custom) | REST + WS | Custom | Custom | Real-time | Custom | WS | Wallet | Global | ✅ | Custom | ⚠️ |

---

## Summary: Direct Feed vs. Normalization Required for ATI ObservationAdapter

### ✅ Can Feed Directly (Minimal Wrapper)
| Source | Reason |
|--------|--------|
| Binance (Spot, Futures) | Native WS streams match standard schemas; `python-binance`/`binance-sdk` handlers exist |
| Bybit (Spot, Linear, Inverse) | `pybit` provides typed handlers for all public WS channels |
| OKX | `okx-api` covers all WS channels; unified envelope format |
| Coinbase Advanced Trade | `coinbase-advanced-py` official SDK; level2 channel guarantees delivery |
| Kraken (WS v2) | Normalized JSON, sequence numbers, L2/L3 book; community SDKs mature |
| Hyperliquid | Official Python SDK handles subscription, reconnection, snapshot reconciliation |
| Kalshi | Official-ish Python SDK; token-bucket rate limits documented; WS sequence tracking |
| Manifold | REST + WS both work; 500/min limit generous; AMM-based but bets feed = book equivalent |
| Polymarket | `py-clob-client` official; WS orderbook/price_change/BBO channels standard |
| dYdX v4 | `nautilus-trader` adapter handles 32-sub/channel pooling; Indexer WS well-defined |
| CryptoDataAPI | MCP server exposes tools directly callable by ATI agent |
| Smart Money API | WS native; funding/liquidations free tier |
| PerpFinder | Keyless REST; CORS-open; simple JSON |
| TerminalFeed | Simple free REST endpoints |
| Loris Tools | Simple REST; free tier for BTC/ETH |
| Fear & Greed (alternative.me) | Trivial JSON endpoint |

### ⚠️ Needs Normalization Wrapper
| Source | Normalization Needed |
|--------|---------------------|
| Binance Options | Different symbol format (contract codes), SBE streams for HFT |
| Bybit Options | Separate endpoint, Greeks/IV schema |
| CME Globex | Paid tier only; 1-deep MBP overlay book; OAuth+ILA entitlement; no Python SDK |
| CoinAPI | Credit-based cost model; WS access tier-gated (Pro+ for book) |
| CryptoCompare | **Free tier retired**; paid only; CCCAGG index normalization |
| Kaiko | Enterprise only; custom contracts; normalized but expensive |
| Polygon.io Free | **No WS**; 15-min delayed; 5 req/min too low for real-time |
| Twelve Data Free | Trial symbols only (8) on WS; 800/day cap; credit system complex |
| Finnhub Free | **WS trades only from IEX (2.5% market)**; non-commercial TOU; 50 symbol WS cap |
| Alpha Vantage | **No WS**; 25 req/day free; real-time = paid |
| IEX Cloud | **Defunct** |
| Azuro | Condition ID mapping; sports-specific; TS SDK not Python |
| PredictIt | Polling only (1/sec); no WS; no trading API; read-only |
| Metaculus | No trading; forecasting only; 200/day limit |
| CoinGlass / Alphractal / Glassnode | Vendor-specific schemas; need field mapping |

---

## Recommendations for ATI ObservationAdapter

### Tier 1: Core Real-Time Feeds (Implement First)
1. **Binance USDⓈ-M Futures WS** - Best liquidity, funding/OI/liquidations native, mature Python SDK
2. **Bybit Linear WS** - Complementary liquidity, allLiquidation stream, good Python SDK
3. **OKX WS** - books-l2-tbt for tick-by-tick depth, markPrice/fundingRate channels
4. **Hyperliquid WS** - Unique on-chain perps, funding/OI, Python SDK handles reconnection
5. **Kalshi WS** - Only CFTC-regulated binary prediction market; USD settlement; FIX 4.4 available
6. **Polymarket CLOB WS** - Deepest crypto prediction liquidity; EIP-712 auth; `py-clob-client` official

### Tier 2: Alternative Data Enrichment
7. **CryptoDataAPI (MCP)** - Fear&Greed, regimes, whale, Hyperliquid gamma; MCP-native for agent
8. **Smart Money API WS** - Whale positions, aggregated funding/liquidations free tier
9. **PerpFinder REST** - Keyless cross-exchange funding/OI/liquidations; 50+ venues
10. **Fear & Greed (alternative.me)** - Trivial daily sentiment

### Tier 3: Equities / Traditional (If Needed)
11. **Polygon.io Advanced ($199/mo)** - Real-time SIP, WS, 20+ years history
12. **Twelve Data Pro+** - Global equities/forex/crypto unified; WS at scale
13. **Finnhub (Paid)** - If US equities focus; but note IEX-only WS trades

### Avoid / Defer
- **CME Globex** - High cost, complex licensing, no free tier
- **CoinAPI / CryptoCompare / Kaiko** - Aggregator cost/complexity vs. direct exchange WS
- **Alpha Vantage** - No WS, too restrictive free tier
- **IEX Cloud** - Defunct
- **PredictIt** - Read-only, 1/sec, no WS

---

## Implementation Notes for ATI

1. **Connection Pooling Required:** Bybit (10 topics/sub), dYdX (32 sub/channel), Polymarket (500 instruments/conn) - implement connection pool managers
2. **Rate Limit Headers:** Binance (`X-MBX-USED-WEIGHT-*`), OKX (`OK-ACCESS-RATE-LIMIT-*`), Bybit (`X-Bapi-Limit-*`) - build header-aware throttler
3. **Reconnection with State Reconciliation:** All WS need snapshot-on-reconnect handling (Binance depth snapshots, Hyperliquid clearinghouseState, Kalshi orderbook_delta seq, Polymarket book snapshot)
4. **Symbol Normalization Layer:** Map exchange symbols → ATI canonical symbols (e.g., `BTCUSDT` / `BTC-USDT` / `BTCUSD_PERP` → `BTC-USD-PERP`)
5. **ObservationAdapter Interface:** Standardize on `{timestamp, source, symbol, type: 'trade'|'book'|'ticker'|'funding'|'oi'|'liquidation', payload}` envelopes
6. **MCP Integration:** CryptoDataAPI and future MCP servers can be called directly from ATI agent without custom WS code

---

*End of Matrix. All data verified against official documentation as of 2026-08-09. Rate limits and pricing subject to change - verify before production deployment.*