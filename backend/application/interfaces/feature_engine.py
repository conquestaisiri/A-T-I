# backend/application/interfaces/feature_engine.py
"""Interface for feature computation engine.

The FeatureEngine receives a ContextSnapshot and returns a FeatureExecutionResult
containing computed ContextFeature objects and diagnostics via ContextHealth.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.feature_execution_result import FeatureExecutionResult


class FeatureEngine(ABC):
    """Contract for computing all registered context features.

    Implementations orchestrate execution of registered feature classes and
    aggregate their results and diagnostics.
    """

    @abstractmethod
    def run(self, snapshot: ContextSnapshot) -> FeatureExecutionResult:
        """Execute features against the snapshot.

        Returns a FeatureExecutionResult with successful features and diagnostics.
        Implementations must isolate failures so that a single feature exception
        does not stop the pipeline.
        """
        raise NotImplementedError
