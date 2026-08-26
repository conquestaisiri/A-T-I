import argparse
import asyncio
import contextlib
import logging
import os
import sys
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Security
from fastapi.staticfiles import StaticFiles

from backend.application.context.bootstrap import (
    _build_reflection,
    build_ccxt_observation_adapter,
    build_ccxt_venue_config,
    build_context_pipeline_from_config,
    build_observation_enrichment,
)
from backend.application.decision.rule_based_solver import RuleBasedSolver
from backend.application.pipeline.context_pipeline_service import ContextPipelineService
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.pipeline.market_loop_service import MarketLoopService
from backend.application.risk.circuit_breaker_risk_gate import (
    CircuitBreakerRiskGate,
    RiskGateConfig,
)
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.domain.context.errors import ConfigurationError
from backend.infrastructure.ai.smart_fallback_reasoner import OmegaConfig, SmartFallbackReasoner
from backend.infrastructure.config.context_loader import load_context_settings
from backend.infrastructure.config.settings import settings
from backend.infrastructure.execution.mt5.bridge import MT5Bridge, MT5Credentials
from backend.infrastructure.observation.mt5_adapter import MT5ObservationAdapter
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.macro_calendar_adapter import SqliteMacroCalendar
from backend.infrastructure.sqlite.macro_event_repository import SqliteMacroEventRepository
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository
from backend.infrastructure.sqlite.reconciliation_repository import (
    SqliteReconciliationRepository,
)
from backend.presentation.api.auth import verify_api_key
from backend.presentation.api.routes_ai import router as ai_router
from backend.presentation.api.routes_context import router as observability_router
from backend.presentation.api.routes_decision import router as decision_router
from backend.presentation.api.routes_drive import router as drive_router
from backend.presentation.api.routes_engine import router as engine_router
from backend.presentation.api.routes_market import router as market_router
from backend.presentation.api.routes_memory import router as memory_router
from backend.presentation.api.routes_mt5 import router as mt5_router
from backend.presentation.api.routes_operator import router as operator_router
from backend.presentation.api.routes_reconciliation import router as reconciliation_router
from backend.presentation.api.routes_strategy import router as strategy_router
from backend.presentation.api.routes_supervisor import router as supervisor_router

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_CONFIG = Path("config/context.yaml")
STATIC_DIR = Path(__file__).parent / "presentation" / "static"


def _risk_gate_config_from_settings() -> RiskGateConfig:
    """Map operator .env risk settings onto the gate config (audit fix: the
    settings existed but were never consumed — defaults silently applied)."""
    return RiskGateConfig(
        max_risk_per_trade_pct=settings.risk_per_trade_pct,
        max_risk_per_symbol_pct=settings.risk_per_symbol_pct,
        max_portfolio_risk_pct=settings.risk_portfolio_pct,
        max_daily_loss_pct=settings.risk_daily_loss_pct,
        max_monthly_loss_pct=settings.risk_monthly_loss_pct,
        max_drawdown_pct=settings.risk_max_drawdown_pct,
        veto_on_toxicity=settings.risk_veto_toxicity,
        veto_on_excess_impact=settings.risk_veto_impact,
    )


def _engine_persisted_auto_trade() -> bool:
    """Restore AUTO-TRADE toggle from data/engine_state.json; default True."""
    try:
        import json as _json

        p = Path("data/engine_state.json")
        if p.exists():
            raw = _json.loads(p.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "auto_trade" in raw:
                return bool(raw["auto_trade"])
    except Exception:
        pass
    return True


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Validate context configuration and initialise persistence at startup."""
    if DEFAULT_CONTEXT_CONFIG.exists():
        load_context_settings(DEFAULT_CONTEXT_CONFIG)

    database = Database(settings.db_path)
    app.state.observation_repository = SqliteObservationRepository(database)
    app.state.context_repository = SqliteContextRepository(database)
    app.state.proposal_repository = SqliteProposalRepository(database)
    app.state.ledger_repository = SqliteLedgerRepository(database)
    app.state.memory_store = SqliteMemoryRepository(database)
    app.state.reconciliation_store = SqliteReconciliationRepository(database)
    app.state.reflection = _build_reflection(database, app.state.memory_store)

    supervisor = SupervisorService()
    app.state.supervisor = supervisor

    # Economic-calendar store (official Forex Factory weekly export). Built
    # unconditionally so research/CLI can read past events; the live poller
    # and the pre-trade event veto are gated by settings.ff_enabled.
    macro_event_repository = SqliteMacroEventRepository(database)
    app.state.macro_event_repository = macro_event_repository

    # Single shared risk gate (gap G3 wiring). One instance is the authority:
    # the simulator evaluates with it, and the ingest/decision paths feed it
    # toxicity + realized impact fills through the same object, so every layer
    # reads one coherent risk state. Operator-supplied venue stats (from
    # settings.risk_market_stats) register on startup, enabling the impact veto.
    risk_gate = CircuitBreakerRiskGate(config=_risk_gate_config_from_settings())
    for symbol, stats in settings.risk_market_stats.items():
        risk_gate.set_market_stats(symbol, **stats)
    app.state.risk_gate = risk_gate

    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=app.state.ledger_repository,
    )
    app.state.simulator = simulator
    app.state.fill_engine = fill_engine

    # Durable ingest: observations + contexts persist to SQLite, order-book
    # events enrich micro-price/OFI state, and market-data freshness feeds the
    # supervisor's stale-data gate (review gaps G1/G2). The shared risk gate is
    # also fed here: TRADE observations carrying an aggressor side become signed
    # flow for the VPIN toxicity veto (gap G3). The bus is exposed so a venue
    # adapter can publish live events later; the operator drive route goes
    # through the same synchronous handle().
    context_builder, _, _, _, _ = build_context_pipeline_from_config(DEFAULT_CONTEXT_CONFIG)
    observation_bus = ObservationBus(maxsize=1024)
    ingest_pipeline = ContextPipelineService(
        bus=observation_bus,
        context_builder=context_builder,
        observation_repository=app.state.observation_repository,
        context_repository=app.state.context_repository,
        supervisor=supervisor,
        enrichment=build_observation_enrichment(),
        risk_feed=risk_gate,
    )
    app.state.context_builder = context_builder
    app.state.observation_bus = observation_bus
    app.state.ingest_pipeline = ingest_pipeline

    # Omega God-mode: when OMEGA_ENABLED=true the decision pipeline uses the
    # multi-provider fallback reasoner (Zen->Groq->OpenRouter, same prompt
    # for every provider, instant key rotation, hedged race). Otherwise
    # keep the deterministic RuleBasedSolver so tests/backtests stay pure.
    # Under pytest we force the deterministic path so the composition-root
    # identity test never touches the network.
    use_omega = bool(settings.omega_enabled) and not os.getenv("PYTEST_CURRENT_TEST")
    omega_keys: dict[str, list[str]] | None = None
    if use_omega:
        try:
            from backend.infrastructure.secrets.sagax_loader import load_provider_keys as _load_keys

            omega_keys = _load_keys()
        except Exception:  # noqa: BLE001
            omega_keys = None

    if use_omega:
        try:
            omega_cfg = OmegaConfig(
                race_mode=settings.omega_race_mode,
                timeout_seconds=settings.omega_timeout_seconds,
                recall_limit=6,
            )
            omega_reasoner = SmartFallbackReasoner(
                omega_cfg,
                memory_store=app.state.memory_store,
                provider_keys=omega_keys or None,
            )
            decision_reasoner: Any = omega_reasoner
            logger.info(
                "Omega reasoner enabled: providers=%s race=%s",
                list(omega_reasoner._specs.keys()),
                omega_cfg.race_mode,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Omega init failed — falling back to RuleBasedSolver")
            decision_reasoner = RuleBasedSolver()
    else:
        decision_reasoner = RuleBasedSolver()

    app.state.decision_pipeline = DecisionPipelineService(
        reasoner=decision_reasoner,
        proposal_repository=app.state.proposal_repository,
        simulator=simulator,
        reflection=app.state.reflection,
        supervisor=supervisor,
        risk_feed=risk_gate,
        kelly_from_memory=settings.risk_kelly_from_memory,
        macro_calendar=(
            SqliteMacroCalendar(macro_event_repository)
            if settings.ff_enabled and settings.event_veto_enabled
            else None
        ),
        event_veto_pre_minutes=settings.event_veto_pre_minutes,
        event_veto_post_minutes=settings.event_veto_post_minutes,
        auto_trade=_engine_persisted_auto_trade(),
    )
    app.state.database = database

    # Shared operator lock: the market-data loop runs on the event loop while
    # the operator drive endpoint executes in a threadpool. Both mutate the
    # paper simulator; the lock serialises them so a live feed and a manual
    # drive never interleave on simulator state.
    operator_lock = threading.Lock()
    app.state.operator_lock = operator_lock

    # Self-feeding market-data loop (review action 3 / G4). Only wired when the
    # operator has explicitly enabled CCXT AND sandbox mode: the loop then
    # trades venue observations automatically through the paper path. Default
    # off keeps the suite and backtests deterministic.
    market_loop = None
    market_tasks: list[asyncio.Task[Any]] = []
    _mode = settings.trading_mode.strip().lower()
    _crypto_active = _mode in ("crypto", "both")
    _forex_active = _mode in ("forex", "both")
    # Live MEXC price poller feeds /ws and /ws/market in crypto/both modes.
    if _crypto_active:
        market_tasks.append(asyncio.create_task(_mexc_price_poller()))
    if settings.ff_enabled:
        from backend.application.pipeline.macro_calendar_service import (
            MacroCalendarService,
            make_http_json_fetcher,
        )

        macro_calendar_service = MacroCalendarService(
            observation_bus,
            macro_event_repository,
            fetcher=make_http_json_fetcher(settings.ff_calendar_url),
            poll_seconds=settings.ff_poll_seconds,
        )
        market_tasks.append(macro_calendar_service.start())
        app.state.macro_calendar_service = macro_calendar_service
        logger.info(
            "Macro calendar poller enabled: url=%s poll=%ss veto=%s(-%d/+%dmin)",
            settings.ff_calendar_url,
            settings.ff_poll_seconds,
            settings.event_veto_enabled,
            settings.event_veto_pre_minutes,
            settings.event_veto_post_minutes,
        )
    if settings.ccxt_enabled and settings.ccxt_sandbox and _crypto_active:
        try:
            from pydantic import SecretStr as _SecretStr

            def _unwrap(v: _SecretStr | str | None) -> str | None:
                if v is None:
                    return None
                if isinstance(v, _SecretStr):
                    return v.get_secret_value()
                return str(v)

            _ccxt_api_key = _unwrap(settings.ccxt_api_key)
            _ccxt_secret = _unwrap(settings.ccxt_secret)
            venue_config = build_ccxt_venue_config(
                venue_id=settings.ccxt_venue_id,
                api_key=_ccxt_api_key,
                secret=_ccxt_secret,
                sandbox=settings.ccxt_sandbox,
                default_symbol=settings.ccxt_default_symbol,
                enable_websocket=settings.ccxt_enable_websocket,
                market_type=settings.ccxt_market_type,
            )
            adapter = build_ccxt_observation_adapter(
                bus=observation_bus,
                config=venue_config,
                symbol=settings.ccxt_default_symbol,
            )
            market_loop = MarketLoopService(
                bus=observation_bus,
                ingest_pipeline=ingest_pipeline,
                decision_pipeline=app.state.decision_pipeline,
                fill_engine=fill_engine,
                symbols=settings.crypto_symbols.split(","),
                thread_lock=operator_lock,
            )
            app.state.market_loop = market_loop
            app.state.market_adapter = adapter
            app.state.market_loop_enabled = True
            market_tasks = [
                asyncio.create_task(adapter.start()),
                asyncio.create_task(market_loop.start()),
            ]
            logger.info(
                "Market loop enabled: %s %s (ws=%s)",
                settings.ccxt_venue_id,
                settings.ccxt_default_symbol,
                settings.ccxt_enable_websocket,
            )
        except Exception:  # noqa: BLE001 -- a misconfigured venue must never block the API
            logger.exception("Failed to start CCXT market loop; continuing without it")
            app.state.market_loop_enabled = False

    # --- MT5 observation adapter (live forex ticks, same bus as crypto) ---
    # Starts whenever MT5 credentials are present in .env; the MetaTrader5
    # terminal itself is only touched lazily on first poll, so a closed
    # terminal never blocks API startup. The adapter publishes
    # ObservationEvent(TICKER/TRADE) onto the shared bus; decision pipeline,
    # risk gate and paper simulator consume it identically to CCXT events.
    try:
        _mt5_login = settings.mt5_login
        _mt5_password = settings.mt5_password
        _mt5_server = settings.mt5_server
        _fx_symbols = tuple(
            s.strip().upper() for s in settings.forex_symbols.split(",") if s.strip()
        )
        if not _forex_active:
            logger.info("Trading mode %s — MT5 adapter disabled", _mode)
        elif not (_mt5_login and _mt5_password and _mt5_server):
            logger.info("MT5 credentials incomplete — adapter disabled at startup")
        else:
            _mt5_bridge = MT5Bridge(
                MT5Credentials(
                    login=int(_mt5_login),
                    password=_mt5_password,
                    server=_mt5_server,
                )
            )
            _mt5_adapter = MT5ObservationAdapter(
                event_bus=observation_bus,
                bridge=_mt5_bridge,
                symbols=_fx_symbols,
                symbol_prefix=settings.mt5_symbol_prefix,
            )
            market_tasks.append(_mt5_adapter.start())
            app.state.mt5_adapter = _mt5_adapter
            logger.info(
                "MT5 observation adapter enabled: symbols=%s",
                settings.forex_symbols,
            )
    except Exception:  # noqa: BLE001 — a failed MT5 init must never block the API
        logger.exception("MT5 observation adapter init failed; continuing without it")
        app.state.mt5_adapter = None

    yield
    for task in market_tasks:
        task.cancel()
    for task in market_tasks:
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:  # noqa: BLE001
            logger.exception("Market loop task exited with an error")
    # Omega clients hold pooled connections — close them so long-running
    # uvicorn reloads / pytest teardown don't leak file descriptors.
    reasoner = getattr(getattr(app.state, "decision_pipeline", None), "_reasoner", None)
    close = getattr(reasoner, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):
            close()
    database.close()


app = FastAPI(
    title="Trading Intelligence API",
    description="Autonomous Trading Intelligence Backend",
    version="1.0.0",
    lifespan=lifespan,
)


# Security headers middleware
@app.middleware("http")
async def _security_headers(request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; "
        "img-src 'self' data: https://*.tradingview.com https://s3.tradingview.com; "
        # 'unsafe-eval' is required by Vue's in-DOM template compiler
        # (new Function). The dashboard is same-origin only.
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://unpkg.com https://s3.tradingview.com; "
        "connect-src 'self' https://api.llm7.io https://api.kilo.ai "
        "https://oai.endpoints.kepler.ai.cloud.ovh.net https://api.groq.com "
        "https://openrouter.ai https://api.cerebras.ai "
        "https://generativelanguage.googleapis.com https://agentrouter.ai "
        "https://opencode.ai https://*.tradingview.com https://s3.tradingview.com; "
        "frame-src 'self' https://*.tradingview.com https://s3.tradingview.com; "
        "child-src 'self' https://*.tradingview.com https://s3.tradingview.com;"
    )
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    return response


app.include_router(observability_router)
app.include_router(decision_router)
app.include_router(memory_router)
app.include_router(drive_router)
app.include_router(supervisor_router)
app.include_router(reconciliation_router)
app.include_router(market_router)
app.include_router(mt5_router)
app.include_router(operator_router)
app.include_router(engine_router)
app.include_router(ai_router)
app.include_router(strategy_router)


# --- WebSocket for real-time streaming ---
from fastapi import WebSocket, WebSocketDisconnect  # noqa: E402

# Shared market data store (updated by background poller).
# ``prices`` holds every MEXC USDT pair from one batched call; ``price`` is the
# legacy single-symbol (BTC) view kept for backward compatibility.
_market_data: dict[str, Any] = {"price": None, "ts": 0.0, "prices": {}}


async def _mexc_price_poller() -> None:
    """Background task: batched poll of ALL MEXC prices every 500ms.

    One call to ``/api/v3/ticker/price`` without a symbol returns every listed
    pair at comparable API weight to a single-symbol request — full universe
    coverage for the dashboard at ~1/N marginal cost. Failures log with a
    consecutive-failure counter and back off; a frozen feed is surfaced as
    ``stale`` on the WebSocket instead of masquerading as live.
    """
    import time as time_mod

    import httpx

    consecutive_failures = 0
    async with httpx.AsyncClient(timeout=5) as client:
        while True:
            delay = 0.5
            try:
                r = await client.get("https://api.mexc.com/api/v3/ticker/price")
                rows = r.json()
                if isinstance(rows, list):
                    prices = {row["symbol"]: float(row["price"]) for row in rows}
                    _market_data["prices"] = prices
                    btc = prices.get("BTCUSDT")
                    if btc is not None:
                        _market_data["price"] = btc
                    _market_data["ts"] = time_mod.time()
                    consecutive_failures = 0
                    supervisor: SupervisorService | None = getattr(app.state, "supervisor", None)
                    if supervisor is not None and btc is not None:
                        supervisor.record_observation("BTCUSDT", datetime.now(UTC))
                else:
                    raise ValueError(f"unexpected ticker payload type {type(rows).__name__}")
            except Exception as exc:  # noqa: BLE001 -- a data-feed failure must never kill the task
                consecutive_failures += 1
                # Exponential backoff capped at 30s; log sparsely so a MEXC
                # outage is visible in logs without flooding them.
                delay = min(30.0, 0.5 * (2 ** min(consecutive_failures - 1, 6)))
                if consecutive_failures == 1 or consecutive_failures % 20 == 0:
                    logger.warning(
                        "MEXC price poll failed (%s consecutive): %s",
                        consecutive_failures,
                        exc,
                    )
            await asyncio.sleep(delay)


def _market_price_stale(max_age_seconds: float = 10.0) -> bool:
    """True when the shared price store has not refreshed recently."""
    import time as time_mod

    ts = _market_data.get("ts") or 0.0
    return (time_mod.time() - ts) > max_age_seconds


@app.websocket("/ws/market")
async def ws_market(websocket: WebSocket) -> None:
    """Stream live MEXC prices every 500ms (all pairs + legacy BTC view)."""
    await websocket.accept()
    try:
        while True:
            stale = _market_price_stale()
            payload: dict[str, Any] = {
                "type": "prices",
                "stale": stale,
                "ts": _market_data["ts"],
            }
            price = _market_data["price"]
            if price is not None:
                payload["symbol"] = "BTCUSDT"
                payload["price"] = price
            prices = _market_data.get("prices") or {}
            if prices:
                # Cap the frame (~100KB): majors first, then alphabetical, so
                # headline pairs are never crowded out of the window.
                majors = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "BNBUSDT", "DOGEUSDT")
                ordered = [k for k in majors if k in prices] + sorted(
                    k for k in prices if k not in majors
                )
                payload["prices"] = {k: prices[k] for k in ordered[:300]}
            await websocket.send_json(payload)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("Market WS error")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Stream real-time price, portfolio, risk, and AI decisions to the browser."""
    await websocket.accept()
    logger.info("WebSocket client connected")
    try:
        while True:
            data: dict[str, Any] = {}
            sim: PaperTradingSimulator = app.state.simulator
            data["equity"] = sim.equity
            # Snapshot under the operator lock: drive/market-loop may mutate
            # positions concurrently (dict resize during iteration otherwise).
            lock: threading.Lock | None = getattr(app.state, "operator_lock", None)
            if lock is not None:
                with lock:
                    data["positions"] = {
                        sym: {
                            "side": p.side.value,
                            "quantity": p.quantity,
                            "entry": p.average_entry_price,
                        }
                        for sym, p in list(sim.positions.items())
                    }
            else:
                data["positions"] = {
                    sym: {
                        "side": p.side.value,
                        "quantity": p.quantity,
                        "entry": p.average_entry_price,
                    }
                    for sym, p in list(sim.positions.items())
                }
            risk = sim.risk_snapshot()
            data["risk"] = {
                "daily_loss_pct": risk.daily_loss_pct,
                "drawdown_pct": risk.drawdown_pct,
                "open_exposure_pct": risk.open_exposure_pct,
                "total_loss_pct": risk.total_loss_pct,
                "monthly_loss_pct": risk.monthly_loss_pct,
                "position_count": risk.position_count,
                "symbol_risk_used_pct": risk.symbol_risk_used_pct,
                "portfolio_risk_used_pct": risk.portfolio_risk_used_pct,
            }
            supervisor: SupervisorService = app.state.supervisor
            sup = supervisor.check()
            data["supervisor"] = (
                sup.status.value if hasattr(sup.status, "value") else str(sup.status)
            )
            data["supervisor_reason"] = sup.reason
            price = _market_data["price"]
            if price is not None:
                data["price"] = price
                data["price_stale"] = _market_price_stale()
            await websocket.send_json(data)
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception:
        logger.exception("WebSocket error")
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "healthy",
        "env": settings.api_env,
    }


@app.get("/context/config")
def context_config_status(_api_key: str = Security(verify_api_key)) -> dict[str, Any]:
    """Expose whether context configuration loaded successfully (auth required)."""
    if not DEFAULT_CONTEXT_CONFIG.exists():
        return {"loaded": False, "path": str(DEFAULT_CONTEXT_CONFIG)}
    try:
        context_settings = load_context_settings(DEFAULT_CONTEXT_CONFIG)
        return {
            "loaded": True,
            "path": str(DEFAULT_CONTEXT_CONFIG),
            "window_duration_seconds": int(context_settings.window_duration.total_seconds()),
            "features": {
                name: {"enabled": cfg.enabled, "parameters": dict(cfg.parameters)}
                for name, cfg in context_settings.features.items()
            },
        }
    except ConfigurationError as exc:
        return {"loaded": False, "path": str(DEFAULT_CONTEXT_CONFIG), "error": str(exc)}


# This file also acts as the Composition Root.
# Dependencies (like database repositories or brokers) will be initialized here
# and injected into routers and application use cases.

# Static operator dashboard is mounted last so it cannot shadow API routes.
if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="dashboard")


# --- CLI Entry Point ---


async def run_paper_mode(config_path: str | None = None) -> None:
    """Run ATI in paper trading mode.

    Self-feeding loop, fully offline of any broker/EA: the data fabric
    auto-fetches live market data (crypto needs no API keys), the bridge
    translates normalized fabric events onto the observation bus, the market
    loop runs each event through durable ingest -> risk-gated decision ->
    deterministic paper fills -> ledger. ``PaperFillEngine`` is the simulator's
    order gateway; no MT5 terminal or chart-attached EA is required.
    """
    from backend.application.execution.prop_rules import create_prop_engine
    from backend.application.reflection.reflection_service import ReflectionService
    from backend.infrastructure.data_fabric.service import build_data_fabric_from_env
    from backend.infrastructure.observation.fabric_bridge import FabricObservationBridge
    from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
    from backend.infrastructure.sqlite.database import Database
    from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
    from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
    from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository
    from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository

    logger.info("Starting ATI in PAPER mode")

    db_path = os.getenv("DB_PATH", "data/trading_intelligence.db")
    starting_equity = float(os.getenv("STARTING_EQUITY", "10000.0"))

    # Build data fabric (self-fetching live market data; crypto needs no keys)
    fabric = build_data_fabric_from_env(db_path=db_path)
    await fabric.start()
    logger.info("Data Fabric started")

    # Build prop engine (informational risk envelope; live path only)
    prop_firm = os.getenv("PROP_FIRM", "fundingpips")
    prop_model = os.getenv("PROP_MODEL", "flex")
    create_prop_engine(
        firm=prop_firm,
        model=prop_model,
        account_type="evaluation",
        starting_equity=starting_equity,
    )
    logger.info("Prop engine created: %s %s", prop_firm, prop_model)

    # Persistence
    database = Database(db_path)
    observation_repo = SqliteObservationRepository(database)
    context_repo = SqliteContextRepository(database)
    proposal_repo = SqliteProposalRepository(database)
    ledger_repo = SqliteLedgerRepository(database)
    memory_store = SqliteMemoryRepository(database)

    # Single shared risk gate (gap G3 wiring): the simulator evaluates with it,
    # and the ingest + decision paths feed it toxicity and realized impact
    # through the same object, so every layer reads one coherent risk state.
    risk_gate = CircuitBreakerRiskGate(config=_risk_gate_config_from_settings())
    for symbol, stats in settings.risk_market_stats.items():
        risk_gate.set_market_stats(symbol, **stats)

    supervisor = SupervisorService()

    # Durable ingest: observations + contexts persist to SQLite, order-book
    # events enrich micro-price/OFI state, freshness feeds the supervisor's
    # stale-data gate, and signed trade flow feeds the toxicity veto.
    context_builder, _, _, _, _ = build_context_pipeline_from_config(
        config_path if config_path else DEFAULT_CONTEXT_CONFIG
    )
    observation_bus = ObservationBus(maxsize=1024)
    ingest_pipeline = ContextPipelineService(
        bus=observation_bus,
        context_builder=context_builder,
        observation_repository=observation_repo,
        context_repository=context_repo,
        supervisor=supervisor,
        enrichment=build_observation_enrichment(),
        risk_feed=risk_gate,
    )

    # Paper fills: deterministic gateway (microstructure + fees), no MT5 needed.
    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=ledger_repo,
        starting_equity=starting_equity,
    )

    reflection = ReflectionService(
        ledger=ledger_repo,
        proposals=proposal_repo,
        memory=memory_store,
    )
    # Omega in paper mode too — same God logic, same continuity.
    _use_omega2 = bool(settings.omega_enabled) and not os.getenv("PYTEST_CURRENT_TEST")
    _omega_keys2: dict[str, list[str]] | None = None
    if _use_omega2:
        try:
            from backend.infrastructure.secrets.sagax_loader import (
                load_provider_keys as _load_keys2,
            )

            _omega_keys2 = _load_keys2()
        except Exception:  # noqa: BLE001
            _omega_keys2 = None

    if _use_omega2:
        try:
            _omega_cfg2 = OmegaConfig(
                race_mode=settings.omega_race_mode,
                timeout_seconds=settings.omega_timeout_seconds,
            )
            _omega_reasoner2 = SmartFallbackReasoner(
                _omega_cfg2, memory_store=memory_store, provider_keys=_omega_keys2 or None
            )
            _paper_reasoner: Any = _omega_reasoner2
            logger.info("Omega paper reasoner enabled: %s", list(_omega_reasoner2._specs.keys()))
        except Exception:  # noqa: BLE001
            logger.exception("Omega paper init failed — RuleBasedSolver fallback")
            _paper_reasoner = RuleBasedSolver()
    else:
        _paper_reasoner = RuleBasedSolver()

    decision_pipeline = DecisionPipelineService(
        reasoner=_paper_reasoner,
        proposal_repository=proposal_repo,
        simulator=simulator,
        reflection=reflection,
        supervisor=supervisor,
        risk_feed=risk_gate,
        kelly_from_memory=settings.risk_kelly_from_memory,
    )
    logger.info("Decision Pipeline created")

    # Bridge: fabric NormalizedEvents -> observation bus (market loop input).
    bridge = FabricObservationBridge(fabric.event_bus, observation_bus)

    # Self-feeding loop: bus -> ingest -> decision -> paper fills -> ledger.
    # TRADE_SYMBOL pins a single symbol; by default every configured crypto
    # symbol is tradeable (each decision still passes the full risk gate).
    crypto_symbols = os.getenv("CRYPTO_SYMBOLS", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT").split(
        ","
    )
    trade_symbol = os.getenv("TRADE_SYMBOL") or None
    market_loop = MarketLoopService(
        bus=observation_bus,
        ingest_pipeline=ingest_pipeline,
        decision_pipeline=decision_pipeline,
        fill_engine=fill_engine,
        symbol=trade_symbol,
        symbols=None if trade_symbol else [s.strip() for s in crypto_symbols if s.strip()],
    )

    # Run the system
    tasks = [
        asyncio.create_task(bridge.start()),
        asyncio.create_task(market_loop.start()),
        asyncio.create_task(_paper_health_monitor(fabric, bridge, market_loop, observation_bus)),
    ]
    logger.info(
        "ATI PAPER MODE RUNNING - Press Ctrl+C to stop (symbols=%s)",
        trade_symbol or ",".join(s.strip() for s in crypto_symbols) or "*",
    )

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        # Cleanup
        bridge.stop()
        market_loop.stop()
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001
                logger.exception("Paper mode task exited with an error")
        await fabric.stop()
        # Close Omega pooled clients if paper used them
        _paper_r = locals().get("_paper_reasoner") or locals().get("_omega_reasoner2")
        close2 = getattr(_paper_r, "close", None)
        if callable(close2):
            with contextlib.suppress(Exception):
                close2()
        database.close()
        logger.info("ATI stopped cleanly")


async def _paper_health_monitor(
    fabric: Any,
    bridge: Any,
    market_loop: Any,
    observation_bus: Any,
    interval: float = 30.0,
) -> None:
    """Periodically log live health for paper mode (non-fatal on errors)."""
    from backend.application.pipeline.market_loop_service import MarketLoopService  # noqa: I001
    from backend.infrastructure.data_fabric.service import DataFabricService  # noqa: I001
    from backend.infrastructure.observation.fabric_bridge import FabricObservationBridge  # noqa: I001
    from backend.infrastructure.observation.observation_bus import ObservationBus  # noqa: I001

    assert isinstance(fabric, DataFabricService)
    assert isinstance(bridge, FabricObservationBridge)
    assert isinstance(market_loop, MarketLoopService)
    assert isinstance(observation_bus, ObservationBus)

    while True:
        await asyncio.sleep(interval)
        try:
            fabric_status = fabric.get_status()
            sources = fabric_status.get("sources", fabric_status.get("health", []))
            if isinstance(sources, list):
                live = sum(1 for s in sources if s.get("connection_state") == "LIVE")
                logger.info(
                    "Health: sources live=%d/%d bridge=%s loop=%s bus_q=%d",
                    live,
                    len(sources),
                    bridge.stats(),
                    market_loop.stats(),
                    observation_bus.qsize,
                )
            else:
                logger.info(
                    "Health: bridge=%s loop=%s bus_q=%d",
                    bridge.stats(),
                    market_loop.stats(),
                    observation_bus.qsize,
                )
        except Exception:  # noqa: BLE001 -- health logging must never kill the loop
            logger.exception("Health monitor failed")


async def run_live_mode(config_path: str | None = None) -> None:
    """Run ATI in live trading mode."""
    logger.info("Starting ATI in LIVE mode")

    # Fail-closed: require explicit LIVE_TRADING_AUTHORIZED=true and PAPER_MODE=false
    live_auth = os.getenv("LIVE_TRADING_AUTHORIZED", "").strip().lower()
    paper_mode = os.getenv("PAPER_MODE", "true").strip().lower()
    if live_auth != "true" or paper_mode != "false":
        logger.error(
            "Live mode requires LIVE_TRADING_AUTHORIZED=true and "
            "PAPER_MODE=false (got LIVE_TRADING_AUTHORIZED=%r PAPER_MODE=%r)",
            live_auth,
            paper_mode,
        )
        sys.exit(1)

    logger.warning("LIVE MODE NOT YET FULLY IMPLEMENTED - use paper mode for now")
    # TODO: Wire live MT5 bridge, live risk feed, etc.
    sys.exit(1)


def setup_environment() -> None:
    """Load environment variables from .env file if present."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.warning("python-dotenv not installed, skipping .env load")
        return

    # Look for .env in project root
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info(f"Loaded environment from {env_path}")
    else:
        logger.warning("No .env file found, using system environment variables")


def main() -> None:
    parser = argparse.ArgumentParser(description="ATI Trading Intelligence")
    parser.add_argument(
        "--mode",
        choices=["paper", "live"],
        default="paper",
        help="Trading mode (default: paper)",
    )
    parser.add_argument(
        "--config",
        type=str,
        help="Path to config YAML file",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Log level (default: INFO)",
    )

    args = parser.parse_args()

    # Set log level
    logging.getLogger().setLevel(getattr(logging, args.log_level))

    # Setup environment
    setup_environment()

    # Run
    if args.mode == "paper":
        asyncio.run(run_paper_mode(args.config))
    else:
        asyncio.run(run_live_mode(args.config))


if __name__ == "__main__":
    main()
