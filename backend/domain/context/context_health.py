# backend/domain/context/context_health.py
"""Diagnostics model for the Context feature execution pipeline.

`ContextHealth` aggregates execution statistics, timing information, and any
errors captured during feature computation. It is immutable so that downstream
components can safely share it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContextHealth:
    """Aggregated health information for a feature execution run.

    Attributes
    ----------
    total_features: int
        Number of registered features that were attempted.
    successful_features: int
        Number of features that completed without raising an exception.
    failed_features: int
        Number of features that raised an exception.
    execution_times: Dict[str, float]
        Mapping of feature name -> execution duration in seconds.
    errors: Dict[str, str]
        Mapping of feature name -> stringified exception message for failures.
    """

    total_features: int
    successful_features: int
    failed_features: int
    execution_times: dict[str, float]
    errors: dict[str, str]
