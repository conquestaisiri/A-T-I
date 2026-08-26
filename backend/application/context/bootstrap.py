# backend/application/context/bootstrap.py
"""Composition root helpers for the Context Builder pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from backend.application.backtest.backtest_runner import BacktestRunner
from backend.application.backtest.report import ReplayStep
from backend.application.context_builder_impl import ContextBuilderImpl
from backend.application.decision.omni_route_reasoner import (
    AiOmniRouteReasoner,
    OmniRouteConfig,
)
from backend.application.decision.rule_based_solver import RuleBasedSolver, SolverConfig
from backend.application.feature_engine_impl import FeatureEngineImpl
from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.interfaces.context_builder import ContextBuilder
from backend.application.interfaces.context_settings import ContextSettings
from backend.application.interfaces.event_bus import EventBus
from backend.application.interfaces.feature_engine import FeatureEngine
from backend.application.interfaces.memory_store import MemoryStore
from backend.application.interfaces.supervisor import Supervisor
from backend.application.interfaces.window_manager import WindowManager
from backend.application.pipeline.context_pipeline_service import ContextPipelineService
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.pipeline.observation_enrichment import (
    ObservationEnrichment,
    reset_observation_enrichment_state,
)
from backend.application.reflection.reflection_service import ReflectionService
from backend.application.risk.circuit_breaker_risk_gate import (
    CircuitBreakerRiskGate,
    RiskGateConfig,
)
from backend.application.simulation.paper_fill_engine import PaperFeeConfig, PaperFillEngine
from backend.application.simulation.paper_trading_simulator import PaperTradingSimulator
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.application.window_manager_impl import InMemoryWindowManager
from backend.domain.context.feature_registry import FeatureRegistry
from backend.domain.context.features import ALL_FEATURES
from backend.domain.context.features.order_flow import OFITracker, set_ofi_tracker
from backend.domain.context.features.sentiment import set_service
from backend.domain.context.regime_detector import reset_detectors
from backend.domain.observation.event import ObservationEvent
from backend.infrastructure.ccxt_config import CcxtVenueConfig
from backend.infrastructure.config.context_loader import load_context_settings
from backend.infrastructure.event_bus.in_memory_event_bus import InMemoryEventBus
from backend.infrastructure.execution.ccxt_gateway import CcxtOrderGateway
from backend.infrastructure.observation.ccxt_adapter import CcxtObservationAdapter
from backend.infrastructure.observation.observation_bus import ObservationBus
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.ledger_repository import SqliteLedgerRepository
from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository
from backend.infrastructure.sqlite.proposal_repository import SqliteProposalRepository

if TYPE_CHECKING:
    from backend.application.ai.pydantic_ai_reasoner import (
        PydanticAIConfig,
        PydanticAIReasoner,
    )
    from backend.application.portfolio import PortfolioRiskManager
    from backend.application.research.ensemble_allocator import EnsembleAllocator
    from backend.application.research.evidence_engine import EvidenceEngine
    from backend.application.research.strategy_population import (
        StrategyPopulationService,
    )
    from backend.application.sec_edgar import EdgarService
    from backend.application.sentiment import SentimentService
    from backend.application.validation.tick_recorder import TickRecorder

DEFAULT_CONTEXT_CONFIG = Path("config/context.yaml")


def build_feature_registry() -> FeatureRegistry:
    """Create a registry with all baseline context features registered."""
    registry = FeatureRegistry()
    for feature_cls in ALL_FEATURES:
        registry.register(feature_cls)
    return registry


def build_feature_registry_with_sentiment(
    sentiment_service: SentimentService | None = None,
) -> FeatureRegistry:
    """Create registry with sentiment service wired into SentimentFeature."""
    registry = FeatureRegistry()
    for feature_cls in ALL_FEATURES:
        registry.register(feature_cls)
    if sentiment_service is not None:
        set_service(sentiment_service)
    return registry


def build_context_pipeline(
    settings: ContextSettings,
    event_bus: EventBus | None = None,
) -> tuple[ContextBuilder, WindowManager, FeatureEngine, EventBus]:
    """Wire the full Context Builder pipeline from settings."""
    # A fresh pipeline owns a fresh regime-detector state. The detector is a
    # per-symbol online estimator (module-level singleton); without this reset,
    # two pipelines built in the same process leak state into each other and a
    # replay of the same events no longer produces identical contexts (ADR 0007).
    reset_detectors()
    # Same invariant for the order-book features: micro-price and OFI caches
    # are module-level singletons that must not leak between pipelines.
    reset_observation_enrichment_state()
    bus = event_bus or InMemoryEventBus()
    window_manager = InMemoryWindowManager(settings)
    registry = build_feature_registry()
    feature_engine = FeatureEngineImpl(registry, settings)
    context_builder = ContextBuilderImpl(window_manager, feature_engine, bus)
    return context_builder, window_manager, feature_engine, bus


def build_context_pipeline_from_config(
    config_path: str | Path | None = None,
    event_bus: EventBus | None = None,
) -> tuple[ContextBuilder, WindowManager, FeatureEngine, EventBus, ContextSettings]:
    """Load configuration and wire the Context Builder pipeline."""
    path = Path(config_path) if config_path is not None else DEFAULT_CONTEXT_CONFIG
    settings = load_context_settings(path)
    builder, window_manager, feature_engine, bus = build_context_pipeline(settings, event_bus)
    return builder, window_manager, feature_engine, bus, settings


def build_context_pipeline_service(
    db_path: str | Path = "data/trading_intelligence.db",
    bus_maxsize: int = 1024,
    *,
    supervisor: Supervisor | None = None,
    enrichment: ObservationEnrichment | None = None,
) -> ContextPipelineService:
    """Wire the durable observation -> context pipeline from a SQLite store.

    Pass a supervisor to feed market-data freshness into the stale-data gate:
    every trade/ticker/order-book/candle event records its timestamp per symbol,
    so the decision pipeline later degrades when a feed goes quiet.

    Pass an :class:`ObservationEnrichment` (or rely on the default built from
    :func:`build_observation_enrichment`) so order-book events update micro-price
    and OFI state before context is built (audit §19).
    """
    if bus_maxsize <= 0:
        raise ValueError("bus_maxsize must be a positive integer")

    database = Database(db_path)
    observation_repository = SqliteObservationRepository(database)
    context_repository = SqliteContextRepository(database)

    builder, _, _, _, _ = build_context_pipeline_from_config()
    bus = ObservationBus(maxsize=bus_maxsize)

    return ContextPipelineService(
        bus=bus,
        context_builder=builder,
        observation_repository=observation_repository,
        context_repository=context_repository,
        supervisor=supervisor,
        enrichment=enrichment or build_observation_enrichment(),
    )


def build_decision_pipeline(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    solver_config: SolverConfig | None = None,
    risk_config: RiskGateConfig | None = None,
    starting_equity: float = 100_000.0,
    memory_store: MemoryStore | None = None,
    reflection: ReflectionService | None = None,
    supervisor: Supervisor | None = None,
) -> tuple[DecisionPipelineService, PaperTradingSimulator, PaperFillEngine]:
    """Wire the deterministic decision pipeline against a SQLite store.

    Returns the pipeline service plus the simulator and fill engine so callers
    can set mark prices and read live portfolio state. Reflection is wired by
    default: every closed trade writes its outcome to episodic memory
    (Constitution Document 05). An optional supervisor adds the platform
    kill-switch / stale-data gate (blueprint Tier 1); backtests leave it unset
    to preserve replay determinism (ADR 0007).
    """
    database = Database(db_path)
    proposal_repository = SqliteProposalRepository(database)
    ledger_repository = SqliteLedgerRepository(database)

    reasoner: AIReasoner = RuleBasedSolver(solver_config or SolverConfig())
    risk_gate = CircuitBreakerRiskGate(risk_config or RiskGateConfig())
    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=ledger_repository,
        starting_equity=starting_equity,
    )

    pipeline = DecisionPipelineService(
        reasoner=reasoner,
        proposal_repository=proposal_repository,
        simulator=simulator,
        reflection=reflection or _build_reflection(database, memory_store),
        supervisor=supervisor,
    )
    return pipeline, simulator, fill_engine


def build_memory_pipeline(db_path: str | Path = "data/trading_intelligence.db") -> MemoryStore:
    """Create the episodic memory store backed by SQLite (ADR 0010/0004)."""
    return SqliteMemoryRepository(Database(db_path))


def build_sentiment_service(
    *,
    symbols: tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP"),
    update_interval_seconds: int = 900,
    model_name: str = "ProsusAI/finbert",
) -> SentimentService:
    """Create and configure the GDELT + FinBERT sentiment service.

    The service runs a background task fetching GDELT news (15-min updates)
    and running FinBERT inference. Call ``start()`` on the returned service
    to begin background updates, and ``stop()`` to clean up.
    """
    from backend.application.sentiment import SentimentService as _SentimentService

    service = _SentimentService(
        symbols=symbols,
        update_interval_seconds=update_interval_seconds,
        model_name=model_name,
    )
    # Wire the service into the SentimentFeature singleton
    set_service(service)
    return service


def build_edgar_service(
    *,
    symbols: tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP"),
    update_interval_hours: int = 24,
    lookback_days: int = 90,
) -> EdgarService:
    """Create and configure the SEC EDGAR insider/13F service.

    The service runs a background task fetching SEC EDGAR Form 4 (insider
    transactions) and 13F (institutional holdings) filings. Call ``start()``
    on the returned service to begin background updates, and ``stop()`` to
    clean up.
    """
    from backend.application.sec_edgar import EdgarService as _EdgarService

    service = _EdgarService(
        symbols=symbols,
        update_interval_hours=update_interval_hours,
        lookback_days=lookback_days,
    )
    return service


def build_portfolio_risk_manager(
    *,
    risk_free_rate: float = 0.0,
) -> PortfolioRiskManager:
    """Create the portfolio-level risk manager (HRP + CVaR).

    Used for portfolio-level risk budgeting beyond the per-trade risk gate.
    """
    from backend.application.portfolio import PortfolioRiskManager as _PortfolioRiskManager

    return _PortfolioRiskManager(risk_free_rate=risk_free_rate)


def build_ofi_tracker(
    *,
    window_seconds: int = 60,
    max_levels: int = 10,
) -> OFITracker:
    """Create and register the Order Flow Imbalance tracker.

    The tracker is registered globally so the OrderFlowFeature can read
    OFI values computed from L2 delta events.
    """
    tracker = OFITracker(window_seconds=window_seconds, max_levels=max_levels)
    set_ofi_tracker(tracker)
    return tracker


def build_observation_enrichment(
    *,
    window_seconds: int = 60,
    max_levels: int = 10,
    tick_recorder: TickRecorder | None = None,
) -> ObservationEnrichment:
    """Create the canonical observation enrichment step.

    Routes order-book snapshots into micro-price state and L2 deltas into the
    OFI tracker plus an optional tick recorder (audit §19, task P0-004).
    """
    tracker = OFITracker(window_seconds=window_seconds, max_levels=max_levels)
    return ObservationEnrichment(ofi_tracker=tracker, tick_recorder=tick_recorder)


def build_supervisor(
    *,
    max_data_age_seconds: float = 300.0,
) -> SupervisorService:
    """Create the platform supervisor (kill switch + stale-data gate).

    Production wires this into a decision pipeline via ``supervisor=`` so the
    AI never acts on stale data or while an operator has pulled the kill switch.
    """
    return SupervisorService(max_data_age_seconds=max_data_age_seconds)


def build_reflection_service(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    memory_store: MemoryStore | None = None,
) -> ReflectionService:
    """Wire the reflection service that writes episodic memory from the ledger.

    Reads closed trades from the durable ledger, joins their proposals, and
    records bounded outcome episodes so the reasoner can recall them
    (Constitution Document 05; ADR 0010).
    """
    return _build_reflection(Database(db_path), memory_store)


def build_ai_decision_pipeline(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    omni_config: OmniRouteConfig | None = None,
    risk_config: RiskGateConfig | None = None,
    starting_equity: float = 100_000.0,
    memory_store: MemoryStore | None = None,
    reflection: ReflectionService | None = None,
    supervisor: Supervisor | None = None,
) -> tuple[DecisionPipelineService, PaperTradingSimulator, PaperFillEngine, AiOmniRouteReasoner]:
    """Wire the LLM-backed decision pipeline against a SQLite store.

    Uses the deterministic risk gate and paper simulator, but replaces the
    rule-based reasoner with :class:`AiOmniRouteReasoner`, which reads bounded
    episodic memory to ground its proposals. Closed trades write outcomes back
    into that same memory via reflection (ADR 0005, 0010).
    """
    database = Database(db_path)
    proposal_repository = SqliteProposalRepository(database)
    ledger_repository = SqliteLedgerRepository(database)

    memory = memory_store or SqliteMemoryRepository(database)
    reasoner = AiOmniRouteReasoner(
        omni_config or OmniRouteConfig(),
        memory_store=memory,
    )
    risk_gate = CircuitBreakerRiskGate(risk_config or RiskGateConfig())
    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=ledger_repository,
        starting_equity=starting_equity,
    )

    pipeline = DecisionPipelineService(
        reasoner=reasoner,
        proposal_repository=proposal_repository,
        simulator=simulator,
        reflection=reflection or _build_reflection(database, memory),
        supervisor=supervisor,
    )
    return pipeline, simulator, fill_engine, reasoner


def build_omega_decision_pipeline(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    omega_config: Any | None = None,
    risk_config: RiskGateConfig | None = None,
    starting_equity: float = 100_000.0,
    memory_store: MemoryStore | None = None,
    reflection: ReflectionService | None = None,
    supervisor: Supervisor | None = None,
    provider_keys: dict[str, list[str]] | None = None,
) -> tuple[DecisionPipelineService, PaperTradingSimulator, PaperFillEngine, Any]:
    """Wire the Omega (God-mode) multi-provider fallback pipeline.

    Zen -> Groq -> OpenRouter -> Cerebras/Gemini, same prompt for every
    provider, instant key rotation, hedged parallel race, circuit-breaker.
    The risk gate still vetoes — Omega never bypasses it.
    """
    from backend.application.decision.smart_fallback_reasoner import (
        OmegaConfig,
        SmartFallbackReasoner,
    )

    database = Database(db_path)
    proposal_repository = SqliteProposalRepository(database)
    ledger_repository = SqliteLedgerRepository(database)

    memory = memory_store or SqliteMemoryRepository(database)
    reasoner = SmartFallbackReasoner(
        omega_config or OmegaConfig(),
        memory_store=memory,
        provider_keys=provider_keys,
    )
    risk_gate = CircuitBreakerRiskGate(risk_config or RiskGateConfig())
    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=ledger_repository,
        starting_equity=starting_equity,
    )

    pipeline = DecisionPipelineService(
        reasoner=reasoner,
        proposal_repository=proposal_repository,
        simulator=simulator,
        reflection=reflection or _build_reflection(database, memory),
        supervisor=supervisor,
    )
    return pipeline, simulator, fill_engine, reasoner


def build_pydantic_ai_decision_pipeline(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    pydantic_config: PydanticAIConfig | None = None,
    risk_config: RiskGateConfig | None = None,
    starting_equity: float = 100_000.0,
    memory_store: MemoryStore | None = None,
    reflection: ReflectionService | None = None,
    supervisor: Supervisor | None = None,
) -> tuple[DecisionPipelineService, PaperTradingSimulator, PaperFillEngine, PydanticAIReasoner]:
    """Wire the PydanticAI-backed decision pipeline against a SQLite store.

    Uses the deterministic risk gate and paper simulator, with
    :class:`PydanticAIReasoner` providing structured-output LLM reasoning
    (ADR 0011). Closed trades write outcomes back into episodic memory
    via reflection (ADR 0010).
    """
    from backend.application.ai.pydantic_ai_reasoner import (
        PydanticAIConfig as _PydanticAIConfig,
    )
    from backend.application.ai.pydantic_ai_reasoner import (
        PydanticAIReasoner as _PydanticAIReasoner,
    )

    database = Database(db_path)
    proposal_repository = SqliteProposalRepository(database)
    ledger_repository = SqliteLedgerRepository(database)

    memory = memory_store or SqliteMemoryRepository(database)
    reasoner = _PydanticAIReasoner(
        pydantic_config or _PydanticAIConfig(),
        memory_store=memory,
    )
    risk_gate = CircuitBreakerRiskGate(risk_config or RiskGateConfig())
    fill_engine = PaperFillEngine()
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=ledger_repository,
        starting_equity=starting_equity,
    )

    pipeline = DecisionPipelineService(
        reasoner=reasoner,
        proposal_repository=proposal_repository,
        simulator=simulator,
        reflection=reflection or _build_reflection(database, memory),
        supervisor=supervisor,
    )
    return pipeline, simulator, fill_engine, reasoner


def _build_reflection(
    database: Database,
    memory_store: MemoryStore | None = None,
) -> ReflectionService:
    """Build a ReflectionService sharing the caller's database + memory store."""
    return ReflectionService(
        ledger=SqliteLedgerRepository(database),
        proposals=SqliteProposalRepository(database),
        memory=memory_store or SqliteMemoryRepository(database),
    )


def build_backtest_runner(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    symbol: str,
    ai: bool = False,
    omni_config: OmniRouteConfig | None = None,
    risk_config: RiskGateConfig | None = None,
    starting_equity: float = 100_000.0,
    memory_store: MemoryStore | None = None,
    reasoner: AIReasoner | None = None,
    fee_config: PaperFeeConfig | None = None,
) -> BacktestRunner:
    """Wire a fresh decision pipeline wrapped as a backtest runner.

    ``ai=False`` replays with the deterministic ``RuleBasedSolver``;
    ``ai=True`` replays with ``AiOmniRouteReasoner`` (dev/backtest only,
    ADR 0005). An explicit ``reasoner`` overrides both (used by out-of-sample
    evaluation to fit/select a reasoner per fold). ``fee_config`` applies
    deterministic execution costs (taker/maker/latency/impact) to the paper
    fills; when omitted the simulator stays fee-free (legacy behavior). Each
    runner owns its own pipeline + simulator, so campaigns are isolated and
    replay-deterministic (ADR 0007).
    """
    database = Database(db_path)
    proposal_repository = SqliteProposalRepository(database)
    ledger_repository = SqliteLedgerRepository(database)

    if reasoner is None:
        if ai:
            memory = memory_store or SqliteMemoryRepository(database)
            reasoner = AiOmniRouteReasoner(
                omni_config or OmniRouteConfig(),
                memory_store=memory,
            )
        else:
            reasoner = RuleBasedSolver(SolverConfig())

    risk_gate = CircuitBreakerRiskGate(risk_config or RiskGateConfig())
    fill_engine = PaperFillEngine(fee_config=fee_config)
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=fill_engine,
        ledger=ledger_repository,
        starting_equity=starting_equity,
        fee_config=fee_config,
    )
    pipeline = DecisionPipelineService(
        reasoner=reasoner,
        proposal_repository=proposal_repository,
        simulator=simulator,
    )
    return BacktestRunner(pipeline, simulator, fill_engine, symbol=symbol)


def build_replay_steps(
    events: list[ObservationEvent],
) -> tuple[list[ReplayStep], str]:
    """Turn historical observation events into replay steps.

    Feeds the events through the real context builder (window manager +
    feature engine), producing one :class:`ReplayStep` per event with the
    event's close price as the mark price. Returns the steps plus the symbol.
    """
    if not events:
        raise ValueError("replay requires at least one observation event")
    symbol = events[0].payload.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("observation event payload missing 'symbol' string field")

    builder, _, _, _, _ = build_context_pipeline_from_config()
    steps: list[ReplayStep] = []
    for event in events:
        context = builder.handle(event)
        mark_price = event.payload.get("price")
        if not isinstance(mark_price, (int, float)):
            raise ValueError(f"observation event payload missing numeric 'price' for {symbol}")
        steps.append(ReplayStep(context=context, mark_price=float(mark_price)))
    return steps, symbol


def build_evidence_engine(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    max_pbo: float = 0.5,
) -> EvidenceEngine:
    """Wire the evidence engine (P5-003): OOS reports -> durable passports.

    The passport store is the append-only ledger of every evaluated strategy;
    the evidence engine issues auditable passports with conservative verdicts
    (never promoted past paper). This is the single seam the research queue
    uses to turn evaluations into records the operator can review.
    """
    from backend.application.research.evidence_engine import EvidenceEngine
    from backend.infrastructure.sqlite.passport_repository import (
        SqlitePassportRepository,
    )

    return EvidenceEngine(
        SqlitePassportRepository(Database(db_path)),
        max_pbo=max_pbo,
    )


def build_strategy_population(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    min_ladder_candidates: int = 3,
) -> StrategyPopulationService:
    """Wire the strategy population registry (T2-12-1): passports -> views.

    The passport store is the population seed; the service is a read-side
    projection (member rows + the gated competition ladder). Library-only:
    nothing here reaches the live path, and the ladder is advisory — it
    never changes verdicts or statuses.
    """
    from backend.application.research.strategy_population import (
        StrategyPopulationService,
    )
    from backend.infrastructure.sqlite.passport_repository import (
        SqlitePassportRepository,
    )

    return StrategyPopulationService(
        SqlitePassportRepository(Database(db_path)),
        min_ladder_candidates=min_ladder_candidates,
    )


def build_ensemble_allocator(
    db_path: str | Path = "data/trading_intelligence.db",
    *,
    min_regime_fit: float = 0.0,
) -> EnsembleAllocator:
    """Wire the ensemble allocator (T2-13-1): population -> risk allocation.

    Feeds gate-passing candidates from the population registry (T2-12) into
    the risk-parity allocator (P3-003) as evidence-backed ``StrategyProfile``s
    (volatility is operator-supplied per candidate, never guessed). Research
    wiring only: never part of the live path, and the risk gate still decides
    whether any allocation is allowed.
    """
    from backend.application.research.ensemble_allocator import EnsembleAllocator
    from backend.infrastructure.sqlite.passport_repository import (
        SqlitePassportRepository,
    )

    return EnsembleAllocator(
        StrategyPopulationService(SqlitePassportRepository(Database(db_path))),
        min_regime_fit=min_regime_fit,
    )


def build_ccxt_venue_config(
    venue_id: str = "binance",
    api_key: str | None = None,
    secret: str | None = None,
    sandbox: bool = True,
    default_symbol: str = "BTC/USDT",
    enable_websocket: bool = False,
    market_type: str = "spot",
) -> CcxtVenueConfig:
    return CcxtVenueConfig(
        venue_id=venue_id,
        api_key=api_key,
        secret=secret,
        sandbox=sandbox,
        default_symbol=default_symbol,
        enable_websocket=enable_websocket,
        market_type=market_type,
    )


def build_ccxt_observation_adapter(
    bus: ObservationBus,
    config: CcxtVenueConfig | None = None,
    symbol: str | None = None,
) -> CcxtObservationAdapter:
    """Wire a CCXT-backed observation adapter behind the ObservationBus.

    One adapter per (venue, symbol). CCXT domain objects never reach the bus –
    every message is normalised to an ``ObservationEvent`` at the boundary.
    """
    return CcxtObservationAdapter(
        config=config or build_ccxt_venue_config(),
        bus=bus,
        symbol=symbol,
    )


def build_ccxt_order_gateway(
    config: CcxtVenueConfig | None = None,
    *,
    live_trading_authorized: bool = False,
) -> CcxtOrderGateway:
    """Wire a CCXT-backed order gateway for unified venue execution.

    The gateway bridges the sync ``OrderGateway`` port to CCXT's async runtime
    via a dedicated event-loop thread. Rejected orders surface as
    ``ExecutionReport(status=REJECTED)`` rather than raising.

    ``live_trading_authorized`` defaults to False (P0-014): a gateway
    configured for a live venue refuses to connect unless the operator
    explicitly authorizes live trading AND provides credentials. Sandbox mode
    works without either.
    """
    return CcxtOrderGateway(
        config=config or build_ccxt_venue_config(),
        live_trading_authorized=live_trading_authorized,
    )
