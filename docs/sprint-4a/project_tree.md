# Sprint 4A – Updated Project Tree

```
Trading-Intelligence/
├── config/
│   ├── context.yaml                 # Context Builder configuration (NEW)
│   └── sources.yaml                 # Observation source definitions
├── docs/
│   ├── sprint-4a/
│   │   ├── architecture_overview.md # Architecture + sequence diagram (NEW)
│   │   ├── developer_guide.md       # Developer guide (NEW)
│   │   ├── implementation_summary.md# Sprint summary (NEW)
│   │   └── project_tree.md          # This file (NEW)
│   ├── adr/
│   ├── Brain.md
│   ├── Knowledge_Model.md
│   ├── Market_Philosophy.md
│   ├── System_Architecture.md
│   ├── Technical_Blueprint.md
│   └── Vision.md
├── backend/
│   ├── application/
│   │   ├── context/
│   │   │   ├── __init__.py          # (NEW)
│   │   │   └── bootstrap.py         # Pipeline wiring (NEW)
│   │   ├── interfaces/
│   │   │   ├── context_builder.py
│   │   │   ├── context_settings.py  # Extended with FeatureSettings (MOD)
│   │   │   ├── event_bus.py
│   │   │   ├── feature_engine.py    # Returns FeatureExecutionResult (MOD)
│   │   │   └── window_manager.py
│   │   ├── observation/
│   │   ├── context_builder_impl.py  # Orchestration layer (NEW)
│   │   ├── feature_engine_impl.py   # Updated for enable/disable (MOD)
│   │   └── window_manager_impl.py
│   ├── domain/
│   │   ├── context/
│   │   │   ├── events/
│   │   │   │   ├── __init__.py      # (NEW)
│   │   │   │   └── market_context_created_event.py (NEW)
│   │   │   ├── features/
│   │   │   │   ├── __init__.py      # (NEW)
│   │   │   │   ├── _utils.py        # Shared extractors (NEW)
│   │   │   │   ├── trend.py         # (NEW)
│   │   │   │   ├── momentum.py      # (NEW)
│   │   │   │   ├── volatility.py    # (NEW)
│   │   │   │   ├── volume.py        # (NEW)
│   │   │   │   └── liquidity.py     # (NEW)
│   │   │   ├── context_feature.py
│   │   │   ├── context_health.py
│   │   │   ├── context_snapshot.py  # model_dump fix (MOD)
│   │   │   ├── feature_execution_result.py
│   │   │   ├── feature_registry.py
│   │   │   ├── market_context.py
│   │   │   └── errors.py
│   │   └── observation/
│   ├── infrastructure/
│   │   ├── config/
│   │   │   ├── context_loader.py    # YAML loader (NEW)
│   │   │   └── settings.py
│   │   ├── event_bus/
│   │   │   ├── __init__.py          # (NEW)
│   │   │   └── in_memory_event_bus.py (NEW)
│   │   ├── observation/
│   │   └── publishers/
│   ├── services/
│   └── main.py                      # Startup validation (MOD)
├── tests/
│   ├── conftest.py                  # Shared fixtures (NEW)
│   ├── unit/
│   │   ├── test_window_manager.py   # (NEW)
│   │   ├── test_feature_registry.py # (NEW)
│   │   ├── test_feature_engine.py   # (NEW)
│   │   ├── test_context_builder.py  # (NEW)
│   │   ├── test_features.py         # (NEW)
│   │   ├── test_context_loader.py   # (NEW)
│   │   └── test_event_bus.py        # (NEW)
│   └── integration/
│       └── test_context_pipeline.py # (NEW)
├── experiments/
├── pytest.ini                       # (NEW)
├── requirements.txt                 # Added pyyaml, pytest (MOD)
├── CLAUDE.md
└── README.md
```

## Legend

- **(NEW)** – File created in Sprint 4A
- **(MOD)** – Existing file modified in Sprint 4A
