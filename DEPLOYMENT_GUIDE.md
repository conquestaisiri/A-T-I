# ATI Trading Intelligence - Turnkey Deployment Guide

## 🚀 Quick Start (Paper Mode)

```bash
# 1. Clone/navigate to project
cd Trading-Intelligence

# 2. Copy environment template
cp .env.example .env

# 3. Run paper mode (no external accounts needed!)
python -m backend.main --mode paper
```

That's it for paper mode! The system will:
- Start Data Fabric (14 connectors: crypto, forex, macro, news)
- Start MT5 Bridge in paper mode (no MT5 needed)
- Start Prop Engine (FundingPips Flex)
- Start Execution Core + Decision Pipeline
- Run RuleBasedSolver against live market data

---

## 🔑 Live Mode Requirements (What YOU Must Do)

| Step | Action | Time | Link |
|------|--------|------|------|
| 1 | **Create FXCM Demo** | 5 min | https://www.fxcm.com/uk/demo/ |
| 2 | **Get FXCM API Token** | 2 min | FXCM Dashboard → API Settings |
| 3 | **Create Deriv Demo** | 3 min | https://deriv.com/demo-account |
| 4 | **Get Deriv API Token** | 1 min | Deriv Dashboard → API Token |
| 5 | **Install MT5** | 5 min | https://www.metatrader5.com/en/download |
| 6 | **Login MT5** | 1 min | Use FXCM/Demo credentials |
| 7 | **Install EA** | 3 min | See [EA Install Guide](#-mt5-ea-installation) |

---

## 📁 Project Structure

```
Trading-Intelligence/
├── .env.example              # Copy to .env and fill in
├── docker-compose.yml        # One-command deployment
├── backend/
│   ├── main.py               # CLI entry: python -m backend.main --mode paper
│   ├── infrastructure/
│   │   ├── data_fabric/      # 14 connectors (crypto, forex, macro, news)
│   │   ├── mt5/ea/           # MT5 EA (MQ5) + Python Bridge
│   │   └── broker/deriv/     # Deriv connector (Nigeria-supported)
│   ├── application/
│   │   ├── execution/        # ExecutionCore, Prop Engines
│   │   ├── risk/             # CircuitBreakerRiskGate
│   │   └── decision/         # AI Reasoners (OmniRoute, PydanticAI)
│   └── ...
├── tests/                    # 1636 tests (all passing)
└── docs/                     # Architecture docs
```

---

## ⚙️ Configuration (.env)

Copy `.env.example` to `.env` and fill in:

```bash
# === REQUIRED FOR LIVE ===
FXCM_API_TOKEN=your_fxcm_token
FXCM_ACCOUNT_ID=your_account_id
DERIV_API_TOKEN=your_deriv_token
LIVE_TRADING_AUTHORIZED=true
PAPER_MODE=false

# === OPTIONAL ===
PROP_FIRM=fundingpips          # fundingpips | fortradrs
PROP_MODEL=flex                # flex | classic | rapid | strike | fast
STARTING_EQUITY=50000
MT5_MAGIC_NUMBER=123456
DB_PATH=data/trading_intelligence.db
API_KEY=your_api_key_here      # For REST API auth
```

---

## 🐳 Docker Deployment (Recommended)

```bash
# Build and run everything in containers
docker-compose up -d

# View logs
docker-compose logs -f ati

# Stop
docker-compose down
```

**docker-compose.yml includes:**
- `ati-api` - FastAPI backend
- `ati-data-fabric` - 14 connectors
- `ati-execution` - ExecutionCore + Prop Engines
- `ati-mt5-bridge` - Python ↔ MT5 EA bridge
- `postgres` - Database (replaces SQLite for production)
- `redis` - Event bus / caching
- `prometheus` + `grafana` - Monitoring

---

## 🤖 MT5 EA Installation (Required for Live)

### Method 1: Auto-Install Script (Windows)
```powershell
# Run as Administrator
powershell -ExecutionPolicy Bypass -File scripts/install_mt5_ea.ps1
```

### Method 2: Manual (3 minutes)
1. **Open MetaEditor**: Press `F4` in MT5 or Start → MetaEditor
2. **Create New**: File → New → Expert Advisor → Name: `ATI_EA`
3. **Paste Code**: Copy `backend/infrastructure/mt5/ea/ATI_EA.mq5` content
4. **Compile**: Press `F7` → Should show "0 errors, 0 warnings"
5. **Attach to Chart**: Drag `ATI_EA` from Navigator onto any chart
6. **Settings**:
   - `Allow WebRequest`: ✅ Add `http://localhost:8080`
   - `BridgePort`: `8080`
   - `MagicNumber`: `123456` (must match `.env`)
   - `AllowedSymbols`: (leave empty for all)

### Verify EA is Running
- Check MT5 **Experts** tab: Should show "ATI EA initialized on port 8080"
- Check **Journal** tab: No errors
- Python bridge logs: "MT5 Bridge started"

---

## 📊 Prop Firm Engines (Built-in)

### FundingPips (Recommended)
```bash
PROP_FIRM=fundingpips
PROP_MODEL=flex          # flex | classic | rapid
```
| Model | Daily Loss | Total Loss | Profit Target | News Restrict | Weekend |
|-------|------------|------------|---------------|---------------|---------|
| Flex  | 5%         | 10%        | 10%/5%        | 5 min         | ✅      |
| Classic| 4%        | 8%         | 8%/5%         | 5 min         | ✅      |
| Rapid | 3%         | 6%         | 5%/5%         | 5 min         | ❌      |

### For Traders (Pay After Pass)
```bash
PROP_FIRM=fortraders
PROP_MODEL=classic         # classic | strike | fast
```
- Pay small entry fee → Trade → Pass → Pay activation fee → Get funded
- Nigeria explicitly supported ✅

---

## 🌐 Data Sources (All Free, Zero-Auth)

| Source | Markets | Latency | Auth |
|--------|---------|---------|------|
| **Binance** | Crypto | ~50ms | None |
| **Coinbase** | Crypto | ~80ms | None |
| **Kraken** | Crypto | ~100ms | None |
| **Bybit** | Crypto | ~50ms | None |
| **Deriv** | Forex, Crypto, Indices | ~150ms | Optional |
| **Forex Factory** | Economic Calendar | 15 min | None |
| **Fed/ECB/BLS** | Official Releases | Real-time | None |
| **GDELT** | Global News | 15 min | None |
| **Cointelegraph/The Block** | Crypto News | Real-time | None |

---

## 🧪 Testing & Validation

```bash
# Run all tests (1636 tests)
python -m pytest tests/ -q -p no:warnings

# Specific test suites
python -m pytest tests/integrity/ -v          # Replay determinism
python -m pytest tests/application/ -v        # Application logic
python -m pytest tests/unit/ -v               # Unit tests

# Lint & Typecheck
python -m ruff check backend tests
python -m mypy backend
```

---

## 📈 Monitoring & Debugging

```bash
# API Health
curl http://localhost:8000/health

# Data Fabric Status
curl http://localhost:8000/context/config

# MT5 Bridge Logs
tail -f logs/mt5_bridge.log

# Execution Core
curl http://localhost:8000/execution/status
```

---

## 🔒 Security Best Practices

1. **Never commit `.env`** - Add to `.gitignore`
2. **Use API_KEY** - Set `API_KEY` in `.env` for REST auth
3. **Restrict MT5** - EA only connects to `localhost:8080`
3. **Read-only API** - Dashboard is read-only by default
4. **Encrypted DB** - Use PostgreSQL with TLS in production

---

## 🚨 Troubleshooting

| Issue | Solution |
|-------|----------|
| `ModuleNotFoundError: MetaTrader5` | `pip install MetaTrader5` |
| `Connection refused: 8080` | EA not running or wrong port |
| `405 Method Not Allowed` | Check `/v1/drive` endpoint exists |
| `MetaTrader5 package not installed` | Run MT5 once first, then `pip install MetaTrader5` |
| EA shows "Initialize failed" | Check MT5 path in bridge config |

---

## 📞 Support

- **Logs**: `logs/` directory
- **Tests**: `python -m pytest tests/ -v`
- **API Docs**: `http://localhost:8000/docs` (Swagger UI)
- **Architecture**: `docs/ATI_Architecture_Review.md`

---

## ✅ Pre-Flight Checklist

Before going live:
- [ ] `.env` filled with real credentials
- [ ] `LIVE_TRADING_AUTHORIZED=true`
- [ ] `PAPER_MODE=false`
- [ ] MT5 running with EA attached
- [ ] FXCM/Demo logged in MT5
- [ ] Deriv API token in `.env`
- [ ] Prop firm selected in `.env`
- [ ] Tests pass: `python -m pytest tests/ -q`
- [ ] Lint clean: `python -m ruff check backend tests`

---

**Built for Nigeria ✅ | Crypto + Forex ✅ | Prop Firm Ready ✅ | Zero-Auth Data ✅**