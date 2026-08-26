# Autonomous Trading Intelligence (ATI)

ATI is an autonomous AI trading system. The AI observes markets, understands behavior, reasons about opportunities, plans actions, executes disciplined trades, learns from outcomes, and improves continuously. **The AI is the trader; rules exist only as safety constraints.**

## Current Status

- **1696 tests passing** · mypy 292 files clean · ruff clean
- Full cognitive pipeline: Observe → Context → Reason → Risk → Simulate → Reflect → Memory
- Four reasoning backends: deterministic (`RuleBasedSolver`), LLM (`AiOmniRouteReasoner`), structured-output LLM (`PydanticAIReasoner`), hedged fallback (`SmartFallbackReasoner` Omega)
- Unified crypto venue adapter (CCXT, 100+ exchanges) + Data Fabric (Binance/Bybit/Coinbase/Kraken/Deriv/FXCM) behind `ObservationAdapter` + `OrderGateway` ports
- SQLite persistence for observations, contexts, proposals, ledger, episodic memory
- Paper-trading only — no live execution yet

## Architecture

Clean Architecture / hexagonal (ports & adapters):

- `backend/domain/` — enterprise business rules, entities (immutable, frozen)
- `backend/application/` — use cases, ports (interfaces), pipeline services
- `backend/infrastructure/` — frameworks, exchange adapters, SQLite repositories, config
- `backend/presentation/` — FastAPI routers, static operator dashboard

See `docs/Constitution/00-Master-Index.md` for the full engineering canon.

## Running

> **IMPORTANT:** Use `py -3` (CPython 3.14.3), NOT `python` on PATH (which resolves to an unrelated hermes-agent venv without pytest).

```bash
# Install
py -3 -m pip install -r requirements.txt

# Run tests
py -3 -m pytest

# Type check
py -3 -m mypy backend

# Lint
py -3 -m ruff check backend tests

# Run the API
py -3 -m uvicorn backend.main:app --reload
```

Then open `http://localhost:8000/` (operator dashboard) or `/docs` (API reference).

## Key Documents

| Document | Purpose |
|---|---|
| `docs/Constitution/00-Master-Index.md` | Engineering Constitution — read first |
| `ARCHITECTURE_REVIEW.md` | Principal engineering review (current state) |
| `docs/INTEGRATION_SYNTHESIS.md` | Profit-ranked integration roadmap (26 initiatives, 4 tiers) |
| `docs/adr/` | 19 Architecture Decision Records |
| `MARKET_DATA_SOURCE_MATRIX.md` | Free/low-cost real-time data sources |

## Integration Roadmap

The path to profitability is detailed in `docs/INTEGRATION_SYNTHESIS.md`. Top priorities:

1. **Measure execution** — extend `ExecutionReport` with fee/venue/maker
2. **Reduce cost** — maker/taker + post-only routing (4-5 bps saved)
3. **Add free alpha** — GDELT+FinBERT sentiment (Sharpe 4.65-5.87), SEC EDGAR insider data
4. **Add microstructure** — L2 delta capture, Integrated Order Flow Imbalance
5. **Upgrade risk** — fractional Kelly, Hierarchical Risk Parity, CVaR
6. **Add ML** — purged walk-forward CV, regime detection, LightGBM
