# backend/domain/context/market_context.py
"""Domain model representing the immutable market context.

The :class:`MarketContext` aggregates a snapshot of computed features
and metadata derived from a sequence of :class:`~backend.domain.observation.event.ObservationEvent`.
It is designed to be immutable and hashable so that it can be safely cached
and reused across the application.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .context_feature import ContextFeature
from .context_snapshot import ContextSnapshot


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Immutable container for a market context.

    Attributes
    ----------
    snapshot: ContextSnapshot
        The snapshot of observation events used to compute the features.
    features: Mapping[str, ContextFeature]
        Mapping from feature name to the computed feature result. Stored
        internally as an immutable tuple of ``(name, feature)`` pairs.
    created_at: datetime
        Timestamp when the context was created.
    """

    snapshot: ContextSnapshot
    features: tuple[tuple[str, ContextFeature], ...]
    created_at: datetime

    def feature(self, name: str) -> ContextFeature:
        """Retrieve a feature by name.

        Parameters
        ----------
        name: str
            The name of the feature.
        """
        for key, value in self.features:
            if key == name:
                return value
        raise KeyError(f"Feature '{name}' not found in MarketContext")

    def as_dict(self) -> dict[str, Any]:
        """Serialise the market context to a plain dictionary.

        Returns
        -------
        dict
            Contains ``snapshot`` (as dict) and ``features`` mapping.
        """
        return {
            "snapshot": self.snapshot.as_dict(),
            "features": {k: v.as_dict() for k, v in self.features},
            "created_at": self.created_at.isoformat(timespec="milliseconds"),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MarketContext:
        """Reconstruct a context from the output of :meth:`as_dict`."""
        raw_features = data["features"]
        if not isinstance(raw_features, dict):
            raise ValueError("MarketContext dict must contain a 'features' mapping")
        features = tuple(
            (name, ContextFeature.from_dict(feature)) for name, feature in raw_features.items()
        )
        return cls(
            snapshot=ContextSnapshot.from_dict(data["snapshot"]),
            features=features,
            created_at=datetime.fromisoformat(data["created_at"]),
        )
