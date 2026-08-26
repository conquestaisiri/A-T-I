# ADR 0028: P1 Data Fabric

**Status:** Accepted (P1-006, Tier-1 T1-6)
**Date:** 2026-08-22
**Context:** Alternative data (GDELT, SEC EDGAR) must be historized by source timestamp.
**Decision:** `backend/infrastructure/data_fabric/*` `RawEnvelope`/`NormalizedEvent` with `source_timestamp`/`available_at`, `EnhancedEventBus` WAL batch, `BinanceKlinesFetcher` `data-api.binance.vision` public REST, `GDELT` `https` + `forex_factory` async `aiohttp` + `fxcm` venue timestamp.
**Consequences:** Backtests never use current cache, `DataFabric` breadth `btcusdt-1h/eth/sol/btc4h` honests `OBSERVE`.
