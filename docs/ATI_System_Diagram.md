# ATI Engineering Diagrams — Full System Map

Every module, class, function, API endpoint, table, and integration in the
Autonomous Trading Intelligence, with the actual wires between them (verified
against `backend/main.py` composition root and the port contracts). Renders as
Mermaid in GitHub, GitLab, VSCode, or mermaid.live.

Legend
- Solid `-->` — real call/data edge present in the code.
- Dashed `-.->` — planned/not-yet-wired (exists as a library, not composed).
- `(planned)` — the autonomy ladder is unit-tested but not wired into `main.py`.

---

## 1. Master system flow

```mermaid
flowchart LR
    subgraph EXTERNAL["External world"]
        VENUE["CCXT venue / Binance"]
        AI["AI provider (free tier)"]
        OP["Operator"]
    end

    subgraph PRES["Presentation — FastAPI + dashboard"]
        APP["backend/main.py<br/>Composition Root + lifespan"]
        API["6 routers + /health"]
        DASH["static/index.html dashboard"]
    end

    subgraph INGEST["Ingest path (application)"]
        CPS["ContextPipelineService"]
        CB["ContextBuilderImpl"]
        FE["FeatureEngineImpl"]
        ENR["ObservationEnrichment"]
    end

    subgraph DECIDE["Decision path (application)"]
        DPS["DecisionPipelineService"]
        RS["RuleBasedSolver / PydanticAIReasoner / OmniRoute"]
        SIM["PaperTradingSimulator"]
        FILL["PaperFillEngine"]
        RISK["CircuitBreakerRiskGate (RiskGate+RiskFeed)"]
        SUP["SupervisorService"]
        REFL["ReflectionService"]
    end

    subgraph LOOP["Self-feeding loop"]
        ML["MarketLoopService"]
        OBUS["ObservationBus"]
    end

    subgraph AUTONOMY["Autonomy ladder (planned wiring)"]
        PROG["AutonomyProgram"]
        PCS["PaperCampaignService"]
        PAR["PaperAutonomyRunner"]
        PROM["PromotionEngine"]
        AUDIT["autonomy_audit"]
        AGG["outcome_aggregation"]
    end

    subgraph DB["SQLite (16 tables)"]
        CORE_DB["core: obs, contexts, proposals,<br/>ledger, memory, reconciliation"]
        RES_DB["research: datasets, experiments,<br/>alt_data, final_test_claims"]
        AUT_DB["autonomy: campaigns, day_outcomes,<br/>program_runs, promotions, rollbacks"]
    end

    VENUE -->|events| OBUS
    OBUS --> ML
    ML -->|ingest.handle| CPS
    ML -->|process at mark price| DPS
    OP -->|POST /drive| API
    API -->|ingest.handle + DPS.process under operator_lock| CPS
    API -->|read/write| CORE_DB

    CPS --> CB --> FE
    CPS --> ENR
    CPS -->|persist| CORE_DB
    DPS -->|reason| RS
    DPS --> SIM
    DPS -->|freshness/toxicity feed| SUP
    DPS -->|risk feed| RISK
    DPS -->|reflect on close| REFL
    DPS -->|persist proposal + ledger| CORE_DB
    SIM --> FILL
    FILL -->|mark price| SIM
    RISK -->|evaluate| SIM
    SUP -->|may_trade gate| DPS

    APP -->|wires market loop only when<br/>ccxt_enabled AND ccxt_sandbox| ML
    DASH -->|fetch| API

    PROG -.->|RESEARCH/VALIDATION/PAPER/CANARY/PRODUCTION runners| AUTONOMY
    PCS -.->|lifecycle via campaign_state| AUTONOMY
    PCS -.-> PAR
    PCS -.->|persist days| AUT_DB
    PAR -.->|stay-limit judging| PROM
    AUDIT -.->|promotion decisions + rollbacks| AUT_DB
    AGG -.->|read-only corpus summaries| AUT_DB
    AUT_DB -.->|future operator surfaces| API

    CORE_DB --> DB
    RES_DB --> DB
    AUT_DB --> DB
```

---

## 2. Presentation layer — every endpoint

```mermaid
flowchart TB
    APP["FastAPI app<br/>lifespan: builds repos, risk gate, simulator,<br/>pipelines, operator_lock, optional market loop"]
    AUTH["verify_api_key (API-key security on all routers)"]

    subgraph R_CONTEXT["routes_context.py (prefix=/v1, tags=observability)"]
        L1["GET /context/latest"]
        L2["GET /context/history"]
        L3["GET /events/recent"]
    end
    subgraph R_DECISION["routes_decision.py (prefix=/v1)"]
        L4["GET /proposals/recent"]
        L5["GET /proposals/{id}"]
        L6["GET /ledger/recent"]
        L7["GET /ledger/open"]
        L8["GET /ledger/attribution"]
        L9["GET /ledger/{id}"]
        L10["GET /simulator"]
    end
    subgraph R_DRIVE["routes_drive.py (prefix=/v1/drive)"]
        L11["POST /drive — build TRADE event,<br/>ingest.handle → set_mark_price →<br/>pipeline.process under operator_lock"]
    end
    subgraph R_MEM["routes_memory.py (prefix=/v1)"]
        L12["GET /memory/count"]
        L13["GET /memory/recall"]
        L14["POST /reflection/reflect"]
    end
    subgraph R_RECON["routes_reconciliation.py (prefix=/v1/reconcile)"]
        L15["POST /reconcile"]
        L16["POST /reconcile/sandbox"]
        L17["GET /reports"]
        L18["GET /count"]
    end
    subgraph R_SUP["routes_supervisor.py (prefix=/v1/supervisor)"]
        L19["GET /status"]
        L20["POST /kill"]
        L21["POST /release"]
    end
    subgraph R_ROOT["backend/main.py root"]
        L22["GET /health"]
        L23["GET /context/config"]
    end

    APP --> AUTH
    APP --> R_CONTEXT --> L1 & L2 & L3
    APP --> R_DECISION --> L4 & L5 & L6 & L7 & L8 & L9 & L10
    APP --> R_DRIVE --> L11
    APP --> R_MEM --> L12 & L13 & L14
    APP --> R_RECON --> L15 & L16 & L17 & L18
    APP --> R_SUP --> L19 & L20 & L21
    APP --> L22 & L23
    APP --> DASH["static dashboard mounted last"]
```

---

## 3. Application layer — pipelines, risk, simulation

```mermaid
flowchart TB
    subgraph INGEST["backend/application/pipeline + context"]
        CPS["ContextPipelineService<br/>handle() · start() · stop()<br/>_record_freshness · _record_toxicity · _enrich"]
        CB["ContextBuilderImpl<br/>handle() · _trigger_event_id"]
        FE["FeatureEngineImpl<br/>run() · _invoke_compute"]
        WM["InMemoryWindowManager<br/>add() · snapshot() · clear()"]
        ENR["ObservationEnrichment<br/>enrich() · reset() · micro_price() · ofi()"]
        BUILD["bootstrap.py — 22 builders:<br/>build_context_pipeline_from_config,<br/>build_ai_decision_pipeline,<br/>build_ccxt_venue_config, build_backtest_runner…"]
    end

    subgraph DECIDE["backend/application/decision"]
        RULE["RuleBasedSolver<br/>reason() · _direction · _stop_distance<br/>_confidence · _volatility_above_cap · _alternatives"]
        OMNI["AiOmniRouteReasoner<br/>reason() · _parse · _fallback_plan · _stand_aside"]
        PYD["PydanticAIReasoner<br/>reason() · _system_prompt · _build_user_prompt<br/>_recall_for_prompt · _plan · _stand_aside"]
    end

    subgraph RISKAPP["backend/application/risk"]
        GATE["CircuitBreakerRiskGate<br/>evaluate() · _impact_veto · _kelly_cap<br/>_circuit_breaker_violation<br/>record_toxicity_flow · record_impact_fill<br/>set_market_stats · update_edge_estimate"]
        VPIN["VpinTracker<br/>record() · _roll_bucket() · state()"]
    end

    subgraph SIMAPP["backend/application/simulation"]
        PTS["PaperTradingSimulator<br/>process() · risk_snapshot() · equity()<br/>_open · _close · _quantity_for · _resolve_bracket<br/>_funding_for_slice · _bracket_at_risk"]
        PFE["PaperFillEngine<br/>submit() · cancel() · advance() · set_mark_price()<br/>_fill_market · _fill_limit · _fill_limit_partial<br/>_rest_order · _sweep · _apply_impact · _fee_for"]
        SBX["SandboxVenue<br/>submit() · cancel() · advance() · expire_due()<br/>fetch_order_status() · fetch_open_positions()"]
    end

    subgraph LOOPAPP["backend/application/pipeline"]
        DPS["DecisionPipelineService<br/>process() · risk_snapshot()<br/>_feed_impact_fill · _feed_kelly_edge<br/>_reflect_on_close · _persist_proposal"]
        MLS["MarketLoopService<br/>start() · stop() · handle()<br/>_drive_decision · _mark_price · stats()"]
    end

    subgraph SAFETY["backend/application/supervisor + reflection"]
        SUP["SupervisorService<br/>engage_kill_switch · release_kill_switch<br/>record_observation · check"]
        REFL["ReflectionService<br/>reflect() · estimate_edge()<br/>_episode_for · _summary"]
    end

    subgraph EXECAPP["backend/application/execution"]
        RCON["ReconciliationService<br/>reconcile() · recover_open_records()"]
        ATTR["ExecutionAttributionService<br/>attribute() · report()"]
        IMP["SquareRootImpactCalibrator<br/>observe() · calibration() · estimate_impact_bps()"]
    end

    BUILD --> CPS & CB & FE & WM & ENR & DPS & MLS & SUP & REFL & RCON & ATTR & IMP
    CPS --> CB --> FE
    CB --> WM
    CPS --> ENR
    MLS --> CPS
    MLS --> DPS
    DPS --> RULE
    DPS --> OMNI
    DPS --> PYD
    DPS --> PTS
    PTS --> PFE
    PTS --> GATE
    GATE --> VPIN
    SBX --> PFE
    DPS --> SUP
    DPS --> REFL
    DPS --> RCON
    RCON --> ATTR
    RCON --> IMP
```

---

## 4. Research / autonomy ladder (all harnesses)

```mermaid
flowchart TB
    subgraph LADDER["The autonomy ladder (WS1/WS2 — libraries, unit-tested)"]
        PROG["AutonomyProgram<br/>run() · _append_not_reached"]
        RP["ResearchLoop<br/>run_cycle() · _studied"]
        HYP["HypothesisGenerator · generate_hypotheses"]
        VAL["BaselineEvaluator / ValidationHarness<br/>backtest_harness · purged_cv · triple_barrier · adwin"]
        PAR["PaperAutonomyRunner<br/>run() · _monitor · _accumulate · _demote · _sharpe"]
        CAN["CanaryHarness<br/>run() · run_canary_campaign"]
        SCALE["GradualScalingRunner<br/>run() · run_gradual_scaling"]
        PROM["PromotionEngine<br/>evaluate() · rollback_required()<br/>_checks_for · promote"]
    end

    subgraph PAPER["Paper campaign durable lifecycle (WS2.1–2.4)"]
        PCS["PaperCampaignService<br/>create_campaign · start_campaign<br/>run_campaign · cancel_campaign"]
        CST["campaign_state — state machine<br/>transition · start · cancel · finish · is_terminal"]
        LPD["build_live_day_fn → LivePaperDecisionDayFn<br/>__call__(day) · _decide"]
        RDA["record_adapters — campaign_record_from_result,<br/>day_outcome_record, program_run_record,<br/>promotion_decision_record, rollback_record"]
        AGG["outcome_aggregation<br/>campaign_summary · candidate_outcomes · corpus_outcomes"]
        AUD["autonomy_audit<br/>audit_promotion_evaluation · audit_promotion_granted · audit_rollback"]
    end

    subgraph RESEARCH_TOOLS["Other research services"]
        RT1["regime_evaluation (RegimeEvaluator)<br/>scenario_engine (ScenarioEngine)<br/>robustness (RobustnessRunner)"]
        RT2["analog_retrieval (AnalogRetrievalEngine)<br/>strategy_allocator (allocate_strategies)<br/>feature_attribution (AblationRunner)"]
        RT3["label_engine (LabelEngine)<br/>dataset_service (DatasetService)<br/>experiment_registry · alt_data_service"]
    end

    PROG -->|RESEARCH runner| RP --> HYP
    PROG -->|VALIDATION runner| VAL
    PROG -->|PAPER runner| PAR -->|stay-limit judging| PROM
    PROG -->|CANARY runner| CAN -->|canary gate| PROM
    PROG -->|PRODUCTION runner| SCALE
    PAR --> LPD
    LPD -.->|fresh decision pipeline + simulator| PTS["PaperTradingSimulator / DecisionPipelineService"]
    PCS -->|lifecycle authority| CST
    PCS -->|day_fn = live day| PAR
    PCS -->|terminal records| RDA
    AUD -->|gate decisions + rollbacks| RDA
    RDA -->|immutable records| REC["records.py — CampaignRunRecord,<br/>DayOutcomeRecord, ProgramRunRecord,<br/>PromotionDecisionRecord, RollbackRecord"]
    AGG -->|read-only summaries| REC
    PROG -->|program-run records| RDA
    RT1 & RT2 & RT3 -.->|feeds research artifacts| PROG
```

---

## 5. Domain layer — models and state machines

```mermaid
flowchart TB
    subgraph DCTX["domain/context"]
        MC["MarketContext · ContextSnapshot<br/>ContextFeature · FeatureExecutionResult<br/>ContextHealth · FeatureRegistry"]
        FEATS["11 features: trend · momentum · volatility ·<br/>volume · liquidity · micro_price · order_flow<br/>book_imbalance · kyle_lambda · regime ·<br/>sentiment · insider"]
        RD["RegimeDetector / GaussianHMM<br/>update() · snapshot()"]
        EV["MarketContextCreatedEvent"]
        ERR["ContextError hierarchy"]
    end
    subgraph DDEC["domain/decision"]
        DP["DecisionProposal · RiskContext<br/>ProposedAction · ProposedActionType<br/>EvidenceItem · AlternativeConsidered"]
        TP["PreTradePlan · PostTradePlan · StopLevel<br/>stop_distance_from_volatility · bracket_plan"]
    end
    subgraph DEX["domain/execution"]
        ORD["OrderRequest · OrderSide · OrderType<br/>TimeInForce · OrderStatus"]
        REP["ExecutionReport · TradeRecord<br/>Position · PnL (realized/unrealized)"]
        LIFE["VenueOrderState — order lifecycle<br/>with_fill · as_rested · as_rejected<br/>as_cancelled · as_expired · replace_terminal"]
        RECO["ReconciliationReport · PositionDiscrepancy<br/>VenuePosition · DiscrepancyKind"]
        FUND["FundingConfig · funding_cost_for · funding_intervals"]
        ATTRD["TradeAttribution · attribute_trade"]
    end
    subgraph DOBS["domain/observation"]
        EVT["ObservationEvent · ObservationEventType<br/>event_key"]
        ADAPT["ObservationAdapter (abstract)"]
    end
    subgraph DMEM["domain/memory"]
        EPI["MemoryEpisode · MemoryOutcome"]
    end
    subgraph DRES["domain/research"]
        REC["records.py — durable corpus (6 records)"]
        CST["campaign_state — CampaignTransition,<br/>transition/start/cancel/finish/is_terminal"]
        PCP["paper_campaign — PaperCampaignResult,<br/>PaperDay, PaperDayOutcome, PaperCampaignAction"]
        PRM["promotion — CandidateEvidence,<br/>DeploymentMonitor, GateDecision,<br/>RollbackDecision, PromotionConfig, ModelEnvironment"]
        APR["autonomy_program — ProgramStage,<br/>StageVerdict, StageResult, AutonomyProgramResult"]
        RSRCHD["canary · scaling · evaluation · dataset<br/>experiment · hypothesis · label · scenario<br/>robustness · regime_evaluation · alt_data<br/>analog · allocation · attribution"]
    end
    subgraph DRISK["domain/risk"]
        RDEC["RiskDecision · RiskVerdict"]
    end

    DCTX & DDEC & DEX & DOBS & DMEM & DRES & DRISK
    DRES --> REC & CST & PCP & PRM & APR & RSRCHD
```

---

## 6. Infrastructure layer — SQLite + adapters + 16 tables

```mermaid
flowchart TB
    DB["Database<br/>initialize() · connection() · lock() · close()"]

    subgraph REPOS["SQLite repositories"]
        OB["SqliteObservationRepository — save/find_recent/count"]
        CTX["SqliteContextRepository — save/latest/history"]
        PRP["SqliteProposalRepository — save/find_by_id/find_recent/count"]
        LED["SqliteLedgerRepository — save/find/open/closed/count"]
        MEM["SqliteMemoryRepository — record/recall/count"]
        REC["SqliteReconciliationRepository — save_report/recent/count"]
        DS["SqliteDatasetRepository — append_version/list/latest/load/list_datasets"]
        EX["SqliteExperimentRepository — save/get/list/set_status/record_result/claim_final_test"]
        ALT["SqliteAltDataRepository — save_event/snapshot_at/event_count"]
        AUT["SqliteAutonomyStore — 14 methods:<br/>save/get/list campaigns, set_status,<br/>day outcomes, program runs,<br/>promotion decisions, rollbacks"]
    end

    subgraph OBSADAPT["Observation + execution adapters"]
        OBUS["ObservationBus — bounded 1024<br/>subscribe/processed_count/latency"]
        CCXT["CcxtObservationAdapter — normalize<br/>_normalize_trade/_ticker/_order_book<br/>compute_order_book_delta"]
        BIN["BinanceAdapter — normalize"]
        GW["CcxtOrderGateway — submit/cancel<br/>fetch_open_positions/fetch_order_status<br/>_assert_live_safe (live gating)"]
        EBUS["InMemoryEventBus"]
    end

    subgraph CONF["Config"]
        SET["settings (pydantic Settings)"]
        CL["load_context_settings — _parse_window_duration<br/>_parse_features · _validate_feature_parameters"]
        CC["CcxtVenueConfig"]
    end

    subgraph TABLES["16 SQLite tables"]
        T1["observation_events"]
        T2["market_contexts"]
        T3["decision_proposals"]
        T4["trade_ledger"]
        T5["memory_episodes"]
        T6["reconciliation_reports"]
        T7["dataset_versions"]
        T8["dataset_records"]
        T9["experiments"]
        T10["final_test_claims"]
        T11["alt_data_events"]
        T12["autonomy_campaigns"]
        T13["autonomy_day_outcomes"]
        T14["autonomy_program_runs"]
        T15["autonomy_promotion_decisions"]
        T16["autonomy_rollbacks"]
    end

    DB --> OB & CTX & PRP & LED & MEM & REC & DS & EX & ALT & AUT
    OB & CTX & PRP & LED & MEM & REC & DS & EX & ALT & AUT --> T1 & T2 & T3 & T4 & T5 & T6 & T7 & T8 & T9 & T10 & T11 & T12 & T13 & T14 & T15 & T16
    OBUS --> CCXT
    OBUS --> BIN
    GW -->|venue calls| CCXT
    SET --> CL
    SET --> CC
    CC --> CCXT
    CC --> GW
```

---

## 7. Campaign state machine (WS2.1)

```mermaid
stateDiagram-v2
    [*] --> PENDING: create
    PENDING --> RUNNING: start (idempotent)
    PENDING --> CANCELLED: cancel
    RUNNING --> COMPLETED: verdict (advanced/hold)
    RUNNING --> RETIRED: stay-limit breach
    RUNNING --> CANCELLED: cancel
    COMPLETED --> [*]
    RETIRED --> [*]
    CANCELLED --> [*]
    note right of RUNNING
        Only RUNNING may reach a verdict.
        Terminal states are absorbing — never reopened.
    end note
```

---

## 8. Live decision path — one event (sequence)

```mermaid
sequenceDiagram
    participant V as Venue / Operator
    participant ML as MarketLoopService / Drive route
    participant CP as ContextPipelineService
    participant CB as ContextBuilderImpl
    participant DP as DecisionPipelineService
    participant SR as Reasoner (Rule/PydanticAI)
    participant RG as RiskGate
    participant SM as PaperTradingSimulator
    participant FE as PaperFillEngine
    participant LB as Ledger

    V->>ML: ObservationEvent
    ML->>CP: handle(event)  [under operator_lock if drive]
    CP->>CB: freshness → toxicity → save obs → enrich
    CB->>CB: snapshot + features
    CP-->>ML: MarketContext
    ML->>DP: process(context, mark_price)
    DP->>SR: supervisor.may_trade? → reason(context, risk)
    SR-->>DP: DecisionProposal (persisted)
    DP->>SM: process(proposal, mark_price)
    SM->>RG: evaluate(proposal, mark)
    RG-->>SM: RiskDecision
    SM->>FE: submit (paper)
    FE-->>SM: ExecutionReport
    SM-->>DP: SimulationStep
    DP->>LB: save TradeRecord (if closed)
    DP->>RG: feed impact/toxic fill
    DP->>RL: reflect on close → memory
    ML-->>V: step / stats
```

---

## 9. Dependency direction & layer rules

```
Presentation → Application → Domain ← Infrastructure
                    ↑                ↑
                    └────────────────┘
   (application depends on domain types + port interfaces;
    infrastructure implements the ports against SQLite/CCXT)
```

- **Domain** imports nothing from application/infrastructure — pure, immutable,
  JSON-serializable, no time/storage sources.
- **Application** orchestrates via injected ports (`AIReasoner`, `RiskGate`,
  `AutonomyStore`, …); contains no thresholds it does not own.
- **Infrastructure** implements ports; the composition root
  (`backend/main.py`) is the only place objects are constructed.
- **State machines are pure** — `campaign_state` never persists; the caller
  applies a clock and writes the transition.

## Drift

This map is generated by inspection of the composition root, route tables, DB
schema, and service contracts. When you change a boundary, edge, endpoint, or
table, update this file in the same change.
