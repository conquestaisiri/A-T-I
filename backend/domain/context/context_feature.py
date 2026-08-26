# backend/domain/context/context_feature.py
"""Immutable representation of a computed feature.

A :class:`ContextFeature` encapsulates the result of a feature computation
including the name, the computed value, the timestamp of the originating
snapshot and the execution time of the computation (in seconds).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class ContextFeature:
    """Immutable feature result.

    Attributes
    ----------
    name: str
        Unique feature name.
    value: Any
        Computed value – can be any JSON‑serialisable type.
    computation_timestamp: datetime
        When the feature was computed.
    execution_time: float
        Duration of the computation in seconds.
    """

    name: str
    value: Any
    computation_timestamp: datetime
    execution_time: float

    def as_dict(self) -> dict[str, Any]:
        """Serialise the feature to a plain dictionary."""
        return {
            "name": self.name,
            "value": self.value,
            "computation_timestamp": self.computation_timestamp.isoformat(timespec="milliseconds"),
            "execution_time": self.execution_time,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextFeature:
        """Reconstruct a feature from the output of :meth:`as_dict`."""
        return cls(
            name=data["name"],
            value=data["value"],
            computation_timestamp=datetime.fromisoformat(data["computation_timestamp"]),
            execution_time=data["execution_time"],
        )
