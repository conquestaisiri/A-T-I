# backend/domain/research/analog.py
"""Historical-analog retrieval contracts (task P3-002).

A historical analog is a past market state that resembles the current one,
together with the timestamp of that state and the returns that followed it.
The retrieval engine in ``backend.application.research.analog_retrieval``
produces :class:`AnalogRetrievalResult`; this module owns the shape of the
evidence the decision layer reads.

Principles
----------
- **Similarity is measurable.** A state is a fixed feature vector; similarity
  between two states is a number in ``[0, 1]`` where ``1`` means identical,
  computed from a scale-aware, per-feature weighted distance.
- **Every retrieved analog carries its timestamp and its confidence**
  (:attr:`AnalogEvidence.similarity`). A timestamp is what makes an analog
  *historical*; a confidence is what makes it usable.
- **Weak analogs are never strong evidence.** An analog below the minimum
  similarity bar is discarded at retrieval time; an analog above the bar but
  below the strong bar stays available but never contributes to a strong
  claim. :attr:`AnalogEvidence.similarity` keeps the distinction measurable
  and the result's :attr:`AnalogRetrievalResult.evidence_grade` is
  ``"strong"`` only when enough genuinely strong analogs support it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class MarketState:
    """A point-in-time market state, as an aligned feature vector.

    ``feature_names`` is fixed per state family; every state compared together
    must share the same names (and therefore the same length).
    """

    feature_names: tuple[str, ...]
    values: tuple[float, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_names": list(self.feature_names),
            "values": list(self.values),
        }


@dataclass(frozen=True, slots=True)
class HistoricalAnalog:
    """One historical occurrence of a market state and what followed.

    Attributes
    ----------
    timestamp: str
        When the historical state was observed (ISO-8601). This is what makes
        the analog historical rather than a guess.
    state: MarketState
        The market state observed at ``timestamp``.
    outcome_return_pct: float
        The realized market return that followed the state, as a percentage.
    """

    timestamp: str
    state: MarketState
    outcome_return_pct: float


@dataclass(frozen=True, slots=True)
class AnalogEvidence:
    """A retrieved analog with the confidence a decision may act on.

    Attributes
    ----------
    timestamp: str
        The historical timestamp of the analog.
    similarity: float
        Confidence in the match, in ``[0, 1]`` (1 = identical to the query).
    outcome_return_pct: float
        The return that followed that historical state.
    """

    timestamp: str
    similarity: float
    outcome_return_pct: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "similarity": self.similarity,
            "outcome_return_pct": self.outcome_return_pct,
        }


@dataclass(frozen=True, slots=True)
class AnalogRetrievalResult:
    """Evidence from nearest historical analogs for the current state.

    Attributes
    ----------
    query: MarketState
        The state being matched.
    analogs: tuple[AnalogEvidence, ...]
        Retrieved analogs, best-similarity first. Every entry is at or above
        the retrieval minimum similarity.
    weighted_expected_return_pct: float
        Similarity-weighted mean of the analogs' outcomes (0 when no analog).
    strong_analog_count: int
        How many retrieved analogs meet the strong-similarity bar.
    evidence_grade: str
        ``"strong"`` only when at least ``required_strong`` analogs are
        genuinely strong; otherwise ``"weak"``. A weak grade means the
        evidence must not be acted on as if it were established.
    confidence: float
        The best (highest) similarity among retrieved analogs (0 when none).
    """

    query: MarketState
    analogs: tuple[AnalogEvidence, ...]
    weighted_expected_return_pct: float
    strong_analog_count: int
    evidence_grade: str
    confidence: float

    @property
    def has_evidence(self) -> bool:
        """Whether any analog was retrieved at all."""
        return len(self.analogs) > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.as_dict(),
            "analogs": [a.as_dict() for a in self.analogs],
            "weighted_expected_return_pct": self.weighted_expected_return_pct,
            "strong_analog_count": self.strong_analog_count,
            "evidence_grade": self.evidence_grade,
            "confidence": self.confidence,
        }
