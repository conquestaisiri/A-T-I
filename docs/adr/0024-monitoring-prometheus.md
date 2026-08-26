# ADR 0024: Monitoring Prometheus

**Status:** Planned (Tier-4 deferred, T4-35/36)
**Date:** 2026-08-22
**Context:** `monitoring/prometheus.yml` + `grafana` already in `docker-compose.yml:163`, minimal operator logging only per Tier-4 guardrail.
**Decision:** Expose `queue_utilization` per bus to `Supervisor` and shed `ticker/candle` when `>0.8` (backpressure), add `reasoner_latency_histogram` + `provider_stats.latency_ema_ms` + `circuit_open` to `/health`, keep `DataQualityService` anomaly detection library-only until evidence.
**Consequences:** No new alerts until evidence gates, `prometheus_data` volume retained 30d.
