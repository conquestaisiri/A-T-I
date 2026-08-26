# backend/domain/context/feature_execution_result.py
"""Immutable result of a FeatureEngine execution.

Contains the list of successfully computed ``ContextFeature`` objects and the
associated ``ContextHealth`` diagnostics. Additional fields can be added in the
future without breaking the public interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_health import ContextHealth


@dataclass(frozen=True, slots=True)
class FeatureExecutionResult:
    """Encapsulates the output of a FeatureEngine run.

    Attributes
    ----------
    features: List[ContextFeature]
        List of successfully computed features.
    health: ContextHealth
        Diagnostics information for the execution.
    """

    features: list[ContextFeature]
    health: ContextHealth
