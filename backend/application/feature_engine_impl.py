# backend/application/feature_engine_impl.py
"""Concrete implementation of the FeatureEngine.

The engine iterates over all registered feature classes, executes their
``compute`` static method against the provided ``ContextSnapshot`` and
collects results along with diagnostics in a ``FeatureExecutionResult``.
Feature failures are isolated – a raised exception is captured, logged and
recorded in the health report but does not stop processing of remaining
features.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections.abc import Mapping
from typing import Any

from backend.application.interfaces.context_settings import ContextSettings
from backend.application.interfaces.feature_engine import FeatureEngine
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_health import ContextHealth
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.feature_execution_result import FeatureExecutionResult
from backend.domain.context.feature_registry import FeatureRegistry
from backend.domain.context.features import FeatureCls

logger = logging.getLogger(__name__)


class FeatureEngineImpl(FeatureEngine):
    """Concrete FeatureEngine that runs registered context features.

    Parameters
    ----------
    registry: FeatureRegistry
        The central registry containing all feature classes.
    settings: ContextSettings
        Immutable configuration for feature enable/disable and parameters.
    """

    def __init__(self, registry: FeatureRegistry, settings: ContextSettings) -> None:
        self._registry = registry
        self._settings = settings

    def run(self, snapshot: ContextSnapshot) -> FeatureExecutionResult:
        """Execute all enabled registered features against ``snapshot``."""
        total = 0
        successes = 0
        failures = 0
        exec_times: dict[str, float] = {}
        errors: dict[str, str] = {}
        results: list[ContextFeature] = []

        for feature_cls in self._registry.get_all():
            name = getattr(feature_cls, "name", feature_cls.__name__)
            if not self._settings.is_feature_enabled(name):
                logger.debug("Skipping disabled feature %s", name)
                continue

            total += 1
            start = time.perf_counter()
            try:
                feature = _invoke_compute(
                    feature_cls,
                    snapshot,
                    self._settings.feature_parameters(name),
                )
                results.append(feature)
                successes += 1
            except ValueError as exc:
                # Expected warm-up/data conditions (insufficient observations,
                # empty window): these are recoverable states, not bugs. Log
                # once without a traceback so early feeds stay quiet; the error
                # is still recorded in the health report for observability.
                failures += 1
                errors[name] = str(exc)
                logger.warning("Feature %s not ready: %s", name, exc)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                errors[name] = str(exc)
                logger.exception("Feature %s failed during computation", name)
            finally:
                exec_times[name] = time.perf_counter() - start

        health = ContextHealth(
            total_features=total,
            successful_features=successes,
            failed_features=failures,
            execution_times=exec_times,
            errors=errors,
        )
        return FeatureExecutionResult(features=results, health=health)


def _invoke_compute(
    feature_cls: type[FeatureCls],
    snapshot: ContextSnapshot,
    parameters: Mapping[str, Any],
) -> ContextFeature:
    """Invoke a feature's compute method, passing parameters when supported."""
    compute = feature_cls.compute
    signature = inspect.signature(compute)
    if len(signature.parameters) >= 2:
        return compute(snapshot, parameters)
    return compute(snapshot)
