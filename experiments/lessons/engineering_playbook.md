# Engineering Playbook: Actionable Lessons

## 1. Venue-Agnostic Execution Core
**Source:** `Prediction-Markets-Trading-Bot-Toolkits-main`
**Action:** Strictly enforce the adapter pattern. Strategies and AI agents must NEVER import an exchange SDK or know which exchange they are trading on. They must only interact with our internal `MarketEvent` and `OrderRequest` models. Add a new exchange by writing one adapter, not by changing the strategy.

## 2. Multi-Layer Risk Circuit Breakers
**Source:** `Polymarket-bot-main`
**Action:** Implement a hard-coded, decoupled Risk Management service that intercepts all `OrderRequest` messages before they reach the exchange adapter.
Enforce 4 layers of protection:
- Daily Loss Limit (e.g., 5% halt)
- Monthly Loss Limit (e.g., 15% halt)
- Maximum Drawdown Limit (e.g., 25% halt)
- Total Loss Halt
The risk layer must be able to veto any AI or algorithmic trade.

## 3. Tick Data Ingestion via OLAP
**Source:** `polybot-main`
**Action:** Do not use PostgreSQL or SQLite for tick-level market data or order book snapshots. Use an OLAP database like ClickHouse. Relational databases will corrupt or bottleneck under high-frequency ingestion. Define schemas for `canonical_trades`, `enriched_trades`, and `position_ledger`.

## 4. Out-of-Band AI Reasoning
**Source:** `PolyWeather-main` & `CloddsBot-main`
**Action:** AI inference (e.g., LLM generation) must never block the main execution or observation threads. The observation layer must run at line-rate (sub-50ms). AI reasoning must operate asynchronously, consuming snapshots of state, and placing asynchronous orders.

## 5. Dynamic Position Sizing & Gas Accounting
**Source:** `Polymarket-bot-main`
**Action:** Build a dynamic sizing module. Do not use static sizes. If the system is in a drawdown, automatically scale down position sizes. Ensure all profitability calculations include network gas fees and exchange fees before allowing a strategy to fire.

## 6. High-Frequency Separation
**Source:** `Prediction-Markets-Trading-Bot-Toolkits-main`
**Action:** If a specific arbitrage strategy requires sub-50ms execution (like Orderbook Imbalance or Cross-Market Arb), it must be carved out into a specialized microservice (potentially in Rust). Do not mix high-frequency market-making loops with slow AI reasoning loops in the same Python process.
