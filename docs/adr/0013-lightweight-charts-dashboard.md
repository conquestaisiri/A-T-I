# ADR 0013: Lightweight Charts Operator Dashboard

## Status
Proposed

## Context
ATI's current operator dashboard (`backend/presentation/static/index.html`) is a minimal static SPA with polling. It lacks market visualization (candlesticks, indicators, proposal markers). TradingView Lightweight Charts (Apache-2.0, 11.1k★) is the industry-standard embeddable financial charting library.

## Decision
Integrate Lightweight Charts as a React/Vue/Svelte component (or vanilla TS) into ATI's dashboard:
- Add dedicated `/v1/market/candles` endpoint (symbol, timeframe, limit, freshness)
- Render candles + volume + proposal markers (entry/exit/veto) + risk veto markers
- Keep all execution authority in backend — chart is read-only observability
- Preserve TradingView attribution per license

## Consequences
- **Positive**: Professional market visualization; proposal/risk annotations; minimal bundle (~50KB gz)
- **Negative**: Requires frontend build step (Vite/esbuild); new `/v1/market/candles` contract
- **Neutral**: Does not change backend decision pipeline

## Integration Record
- Component: `LightweightChartsDashboard`
- Purpose: Operator market visualization
- Category: Frontend Component
- Version: `lightweight-charts>=5.0.0`
- Source: https://github.com/tradingview/lightweight-charts
- License: Apache-2.0 (attribution required)
- Status: Planned
- Priority: Medium
- Entrypoint: `backend/presentation/static/dashboard/` (new Vite project)
- Dependencies: `lightweight-charts`, Vite, TypeScript
- Capabilities: Candlestick/line/area charts, series, markers, custom plugins, time-scale
- Configuration: `ChartConfig(symbol, timeframe, indicators, proposal_markers, risk_markers)`
- Health: `/v1/market/candles` responds < 100ms; chart mounts without JS errors
- Upgrade Path: Lightweight Charts minor versions backward-compatible; major via migration guide
- Reason: Only Apache-2.0, embeddable, zero-dependency financial charting library with proposal annotation support

## Validation Gate
- Chart renders 500 candles + 50 markers at 60fps
- Proposal markers align with backend timestamps (deterministic replay verification)
- Attribution notice visible per TradingView requirements
- No execution capability exposed in frontend

## References
- ADR 0012 (CCXT Venue Adapter — provides market data)
- docs/Constitution/06-Integration-Constitution.md §25-36 (WRAP decision)
- research/repositories/CloddsBot-main (operator console patterns)