# Sprint 4A – Developer Guide

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests
pytest tests/ -v

# Start API (validates config/context.yaml on startup)
uvicorn backend.main:app --reload
```

## Adding a New Feature

1. Create a class in `backend/domain/context/features/` with:
   - `name: str` class attribute
   - `depends_on: list[str]` (optional)
   - `@staticmethod compute(snapshot, parameters) -> ContextFeature`

2. Register in `backend/domain/context/features/__init__.py` → `ALL_FEATURES`

3. Add configuration entry to `config/context.yaml`

4. Add parameter validation in `context_loader._validate_feature_parameters`

5. Write unit tests in `tests/unit/test_features.py`

Example:

```python
class MyFeature:
    name = "my_feature"

    @staticmethod
    def compute(snapshot, parameters=None):
        return ContextFeature(
            name="my_feature",
            value={"result": 42},
            computation_timestamp=snapshot.end_timestamp,
            execution_time=0.0,
        )
```

## Wiring the Pipeline

```python
from backend.application.context.bootstrap import build_context_pipeline_from_config
from backend.infrastructure.event_bus.in_memory_event_bus import InMemoryEventBus

bus = InMemoryEventBus()
builder, window_manager, feature_engine, _, settings = build_context_pipeline_from_config(
    "config/context.yaml", bus
)

context = builder.handle(observation_event)
```

## Configuration Reference

```yaml
window:
  duration_seconds: 300   # Rolling window length per symbol

features:
  trend:
    enabled: true
    parameters:
      lookback: 10
      flat_threshold_pct: 0.05
```

Invalid configuration raises `ConfigurationError` at startup.

## Testing Patterns

### Create trade events

```python
from tests.conftest import build_price_series_events

events = build_price_series_events(make_trade_event, [100, 101, 102, 103, 104])
```

### Replay determinism check

```python
from tests.conftest import context_semantic_dict

result_a = context_semantic_dict(builder.handle(event))
result_b = context_semantic_dict(builder.handle(event))
assert result_a == result_b  # Compare values, not execution_time
```

### Test failure isolation

Register a feature that raises, verify other features still succeed via `result.health`.

## Logging

Structured logging uses standard `logging` module:
- `ContextBuilderImpl`: DEBUG on context creation
- `FeatureEngineImpl`: EXCEPTION on feature failure
- `InMemoryEventBus`: DEBUG on publish

## Common Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `KeyError: symbol` | Event payload missing symbol | Ensure adapter sets `payload["symbol"]` |
| `ConfigurationError` | Invalid context.yaml | Check parameter types and required features |
| `DuplicateFeatureError` | Feature registered twice | Use `registry.clear()` in tests |
| `ValueError` in feature | Insufficient data in window | Add more events or reduce lookback |

## File Locations

| What | Where |
|------|-------|
| Orchestration | `backend/application/context_builder_impl.py` |
| Features | `backend/domain/context/features/` |
| Config | `config/context.yaml` |
| Loader | `backend/infrastructure/config/context_loader.py` |
| Bootstrap | `backend/application/context/bootstrap.py` |
| Tests | `tests/unit/`, `tests/integration/` |
