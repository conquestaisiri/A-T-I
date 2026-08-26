# backend/application/research/analog_retrieval.py
"""Historical analog retrieval engine (task P3-002).

Given the current market state and a history of past states with their
subsequent outcomes, the engine returns the nearest historical analogs and an
honest, confidence-weighted read of what those analogs imply.

Design rules
------------
- **Similarity is a measurable, reproducible number.** A state is a fixed
  feature vector; distance is the scale-aware, weighted Euclidean distance and
  similarity is ``1 / (1 + distance)``, so identical states map to ``1.0`` and
  similarity falls monotonically as features diverge. Feature scales make the
  metric dimensionless (a 1%-vol difference is not the same as a 1%-return
  difference) and per-feature weights let the researcher emphasise the
  features that matter.
- **Retrieval keeps timestamps and confidence.** Every returned analog records
  both ``timestamp`` (what makes it historical) and ``similarity`` (how much a
  decision may rely on it). Results are best-similarity first.
- **Weak analogs are not treated as strong evidence.** Analogues below
  ``min_similarity`` are not retrieved at all. Those above it are kept but the
  result's ``evidence_grade`` is ``"strong"`` only when at least
  ``required_strong`` analogues meet the ``strong_similarity`` bar; otherwise
  it is ``"weak"`` and must not be acted on as established fact.

The engine is deterministic: the same (query, history, config) always returns
the same result.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

from backend.domain.research.analog import (
    AnalogEvidence,
    AnalogRetrievalResult,
    HistoricalAnalog,
    MarketState,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnalogRetrievalConfig:
    """Retrieval and evidence thresholds.

    Attributes
    ----------
    min_similarity: float
        Analogues below this similarity are discarded at retrieval time.
    strong_similarity: float
        Similarity at or above which an analog counts as strong.
    required_strong: int
        How many strong analogs are required for ``evidence_grade == "strong"``.
    top_k: int
        Maximum number of analogs to return.
    feature_scales: tuple[float, ...] | None
        Per-feature normalisation scales (positive). Defaults to unit scales.
    feature_weights: tuple[float, ...] | None
        Per-feature distance weights (non-negative). Defaults to unit weights.
    """

    min_similarity: float = 0.5
    strong_similarity: float = 0.8
    required_strong: int = 2
    top_k: int = 5
    feature_scales: tuple[float, ...] | None = None
    feature_weights: tuple[float, ...] | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_similarity <= 1.0:
            raise ValueError("min_similarity must be in [0, 1]")
        if not 0.0 <= self.strong_similarity <= 1.0:
            raise ValueError("strong_similarity must be in [0, 1]")
        if self.strong_similarity < self.min_similarity:
            raise ValueError("strong_similarity must be at least min_similarity")
        if self.required_strong < 1:
            raise ValueError("required_strong must be at least 1")
        if self.top_k < 1:
            raise ValueError("top_k must be at least 1")
        if self.feature_scales is not None and any(s <= 0.0 for s in self.feature_scales):
            raise ValueError("feature_scales must be strictly positive")
        if self.feature_weights is not None and any(w < 0.0 for w in self.feature_weights):
            raise ValueError("feature_weights must be non-negative")


def make_state(feature_names: tuple[str, ...], values: tuple[float, ...]) -> MarketState:
    """Build a :class:`MarketState`, validating the vector is aligned."""
    if len(feature_names) != len(values):
        raise ValueError("feature_names and values must have the same length")
    if len(feature_names) == 0:
        raise ValueError("a state needs at least one feature")
    if any(not math.isfinite(v) for v in values):
        raise ValueError("state values must be finite")
    return MarketState(feature_names=tuple(feature_names), values=tuple(values))


def feature_similarity(a: MarketState, b: MarketState, config: AnalogRetrievalConfig) -> float:
    """Similarity in ``[0, 1]`` between two aligned states.

    ``1.0`` for identical states, monotonically lower as the scale-aware
    weighted distance grows. Raises when the states are not aligned.
    """
    if a.feature_names != b.feature_names:
        raise ValueError("states must share the same feature_names")
    return _similarity_from_values(a.values, b.values, config)


def _similarity_from_values(
    a: tuple[float, ...],
    b: tuple[float, ...],
    config: AnalogRetrievalConfig,
) -> float:
    n = len(a)
    if n != len(b):
        raise ValueError("states must have the same number of features")
    scales = config.feature_scales or tuple(1.0 for _ in range(n))
    weights = config.feature_weights or tuple(1.0 for _ in range(n))
    if len(scales) != n or len(weights) != n:
        raise ValueError("feature_scales/feature_weights must match the feature count")
    total = 0.0
    for i in range(n):
        diff = (a[i] - b[i]) / scales[i]
        total += weights[i] * diff * diff
    distance = math.sqrt(total)
    return 1.0 / (1.0 + distance)


class AnalogRetrievalEngine:
    """Retrieve and grade the nearest historical analogs of a market state."""

    def __init__(self, config: AnalogRetrievalConfig | None = None) -> None:
        self._config = config or AnalogRetrievalConfig()

    @property
    def config(self) -> AnalogRetrievalConfig:
        return self._config

    def similarity(self, a: MarketState, b: MarketState) -> float:
        """Public similarity entry point for the configured retrieval metric."""
        return feature_similarity(a, b, self._config)

    def retrieve(
        self,
        *,
        query: MarketState,
        history: list[HistoricalAnalog] | tuple[HistoricalAnalog, ...],
    ) -> AnalogRetrievalResult:
        """Return the best analogs of ``query`` from ``history``, graded honestly.

        Analogues below ``min_similarity`` are excluded at retrieval time;
        ``evidence_grade`` is ``"strong"`` only when at least ``required_strong``
        returned analogues meet the strong-similarity bar.
        """
        if len(history) == 0:
            return analog_retrieval_result(query=query, analogs=())

        scored: list[tuple[float, HistoricalAnalog]] = []
        for historical in history:
            similarity = feature_similarity(query, historical.state, self._config)
            if similarity >= self._config.min_similarity:
                scored.append((similarity, historical))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        selected = scored[: self._config.top_k]

        analogs = tuple(
            AnalogEvidence(
                timestamp=historical.timestamp,
                similarity=round(similarity, 6),
                outcome_return_pct=historical.outcome_return_pct,
            )
            for similarity, historical in selected
        )
        return analog_retrieval_result(
            query=query,
            analogs=analogs,
            strong_similarity=self._config.strong_similarity,
            required_strong=self._config.required_strong,
        )


def analog_retrieval_result(
    *,
    query: MarketState,
    analogs: tuple[AnalogEvidence, ...],
    strong_similarity: float = 0.8,
    required_strong: int = 2,
) -> AnalogRetrievalResult:
    """Aggregate a set of (already thresholded) evidences into a graded result."""
    strong_count = sum(1 for a in analogs if a.similarity >= strong_similarity)
    if analogs:
        weight_total = sum(a.similarity for a in analogs)
        weighted_expected = sum(a.similarity * a.outcome_return_pct for a in analogs) / weight_total
        confidence = max(a.similarity for a in analogs)
    else:
        weighted_expected = 0.0
        confidence = 0.0
    grade = "strong" if strong_count >= required_strong else "weak"
    return AnalogRetrievalResult(
        query=query,
        analogs=analogs,
        weighted_expected_return_pct=round(weighted_expected, 6),
        strong_analog_count=strong_count,
        evidence_grade=grade,
        confidence=round(confidence, 6),
    )
