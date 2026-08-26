# ADR 0012: CCXT Venue Adapter

## Status
Accepted

## Context
ATI's `ObservationAdapter` port currently has a single `BinanceAdapter` implementation. To satisfy the Venue-Agnosticism Rule (Integration Constitution §102-108) — "one adapter per venue, all behind one port" — we need a unified crypto adapter using CCXT that supports 100+ exchanges behind a single implementation.

## Decision
Implement `CcxtObservationAdapter` and `CcxtOrderGateway` wrapping CCXT (MIT, 43.6k★, 100+ exchanges, async, paper trading). The adapters will:
- Normalize CCXT WebSocket/REST feeds to ATI's `ObservationEvent`
- Expose unified paper-trading via CCXT sandbox modes
- Keep venue-specific config (rate limits, topic schemas) in a CCXT-specific settings file
- Never leak CCXT domain objects into ATI core — all translation at adapter boundary

## Consequences
- **Positive**: Venue-agnosticism achieved for crypto; single code path for 100+ venues; battle-tested library
- **Negative**: CCXT adds ~15MB; rate-limit handling varies by venue; sandbox modes not identical to live
- **Neutral**: Equities/futures still need broker-specific adapters (Alpaca, IBKR)

## Integration Record
- Component: `CcxtObservationAdapter`, `CcxtOrderGateway`
- Purpose: Unified crypto venue connectivity
- Category: Venue Adapter
- Version: `ccxt>=4.4.0`
- Source: https://github.com/ccxt/ccxt
- License: MIT
- Status: Implemented
- Priority: High
- Entrypoint: `backend/infrastructure/observation/ccxt_adapter.py`, `backend/infrastructure/execution/ccxt_gateway.py`
- Dependencies: `ccxt`, `ccxt.pro` (optional for WS), venue API credentials
- Capabilities: REST/WS market data, order placement, paper trading, rate-limit awareness
- Configuration: `CcxtVenueConfig(venue_id, api_key, secret, sandbox, rate_limit_buffer)`
- Health: WS connected, REST reachable, rate-limit headroom > 20%
- Upgrade Path: CCXT version pin; new venues via config only
- Reason: Only library providing 100+ venue APIs behind unified interface; MIT license; async support

## Validation Gate
- Binance WS + REST parity with existing `BinanceAdapter`
- Paper-trade round-trip: observation → proposal → risk → CCXT sandbox → ledger
- Rate-limit compliance under burst (10x normal)
- Graceful degradation on venue WS disconnect (reconnect with backoff)

## References
- ADR 0003 (Observation Layer)
- ADR 0007 (Execution and Risk Architecture)
- docs/Constitution/06-Integration-Constitution.md §79-82, §102-108, §111-125
- research/repositories/polymarket_lp_tool-main (settlement/order-replace patterns)