"""Unit tests for historical analog retrieval (task P3-002).

The acceptance criteria are:
1. State similarity is measurable.
2. Retrieval has timestamps and confidence.
3. Weak analogs are not treated as strong evidence.
"""

from __future__ import annotations

import pytest
from backend.application.research.analog_retrieval import (
    AnalogRetrievalConfig,
    AnalogRetrievalEngine,
    feature_similarity,
    make_state,
)
from backend.domain.research.analog import HistoricalAnalog, MarketState

_FEATURES = ("vol", "level")


def _state(vol: float, level: float) -> MarketState:
    return make_state(_FEATURES, (vol, level))


def _history(*pairs: tuple[str, float, float, float]) -> tuple[HistoricalAnalog, ...]:
    """Build history from (timestamp, vol, level, outcome) tuples."""
    return tuple(
        HistoricalAnalog(timestamp=timestamp, state=_state(vol, level), outcome_return_pct=outcome)
        for timestamp, vol, level, outcome in pairs
    )


# --- similarity is measurable ----------------------------------------


def test_identical_states_have_similarity_one() -> None:
    a = _state(1.0, 100.0)
    b = _state(1.0, 100.0)
    assert feature_similarity(a, b, AnalogRetrievalConfig()) == pytest.approx(1.0)


def test_similarity_falls_monotonically_with_distance() -> None:
    query = _state(1.0, 100.0)
    config = AnalogRetrievalConfig()
    assert feature_similarity(query, _state(1.0, 100.25), config) == pytest.approx(0.8)
    assert feature_similarity(query, _state(1.5, 100.0), config) == pytest.approx(1 / 1.5)
    assert feature_similarity(query, _state(2.0, 100.0), config) == pytest.approx(0.5)
    assert feature_similarity(query, _state(3.0, 100.0), config) == pytest.approx(1 / 3)


def test_scales_make_the_metric_dimensionless() -> None:
    query = _state(1.0, 100.0)
    scaled = AnalogRetrievalConfig(feature_scales=(1.0, 0.5))
    # The same 0.25 level gap is a wider move on the smaller scale.
    assert feature_similarity(query, _state(1.0, 100.25), scaled) == pytest.approx(1 / 1.5)


def test_feature_weights_control_what_matters() -> None:
    query = _state(1.0, 100.0)
    level_only = AnalogRetrievalConfig(feature_weights=(0.0, 1.0))
    vol_only = AnalogRetrievalConfig(feature_weights=(1.0, 0.0))
    # A vol difference is invisible when only level is weighted (and vice versa).
    assert feature_similarity(query, _state(1.5, 100.0), level_only) == pytest.approx(1.0)
    assert feature_similarity(query, _state(1.0, 100.25), vol_only) == pytest.approx(1.0)


def test_misaligned_states_raise() -> None:
    config = AnalogRetrievalConfig()
    other = make_state(("vol",), (1.0,))
    with pytest.raises(ValueError):
        feature_similarity(_state(1.0, 100.0), other, config)


# --- retrieval timestamps and confidence -----------------------------


def test_retrieval_returns_best_first_with_timestamps() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(
            ("t-c", 1.5, 100.0, 1.0),
            ("t-a", 1.0, 100.0, 2.0),
            ("t-b", 1.0, 100.25, -2.0),
            ("t-e", 3.0, 100.0, 5.0),  # below min_similarity
        ),
    )
    assert result.has_evidence
    assert [a.timestamp for a in result.analogs] == ["t-a", "t-b", "t-c"]
    similarities = [a.similarity for a in result.analogs]
    assert similarities == sorted(similarities, reverse=True)
    assert result.analogs[0].similarity == pytest.approx(1.0)


def test_retrieval_excludes_analogs_below_min_similarity() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(("t-e", 3.0, 100.0, 5.0)),  # sim == 1/3 < 0.5
    )
    assert not result.has_evidence
    assert result.analogs == ()


def test_retrieval_respects_top_k() -> None:
    engine = AnalogRetrievalEngine(AnalogRetrievalConfig(top_k=2))
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(
            ("a", 1.0, 100.0, 0.0),
            ("b", 1.0, 100.25, 0.0),
            ("c", 1.5, 100.0, 0.0),
        ),
    )
    assert [a.timestamp for a in result.analogs] == ["a", "b"]
    assert result.confidence == pytest.approx(1.0)


def test_weighted_expected_return_and_confidence() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(("a", 1.0, 100.0, 2.0), ("d", 2.0, 100.0, -4.0)),
    )
    # (1.0 * 2 + 0.5 * -4) / 1.5 == 0.0
    assert result.weighted_expected_return_pct == pytest.approx(0.0)
    assert result.confidence == pytest.approx(1.0)


def test_empty_history_yields_no_evidence() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(query=_state(1.0, 100.0), history=())
    assert not result.has_evidence
    assert result.weighted_expected_return_pct == pytest.approx(0.0)
    assert result.confidence == pytest.approx(0.0)
    assert result.evidence_grade == "weak"


# --- weak analogs are not strong evidence ----------------------------


def test_two_strong_analogs_grade_strong() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(("a", 1.0, 100.0, 1.0), ("b", 1.0, 100.25, -2.0)),
    )
    assert result.strong_analog_count == 2
    assert result.evidence_grade == "strong"
    assert result.weighted_expected_return_pct == pytest.approx(-1.0 / 3.0)


def test_single_strong_analog_is_not_strong_evidence() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(("a", 1.0, 100.0, 1.0)),
    )
    assert result.strong_analog_count == 1
    assert result.evidence_grade == "weak"


def test_strong_plus_weak_analog_is_not_strong_evidence() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(("a", 1.0, 100.0, 1.0), ("c", 1.5, 100.0, 5.0)),  # c: sim 0.667
    )
    assert result.strong_analog_count == 1
    assert result.evidence_grade == "weak"


def test_required_strong_is_configurable() -> None:
    engine = AnalogRetrievalEngine(AnalogRetrievalConfig(required_strong=1))
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(("a", 1.0, 100.0, 1.0)),
    )
    assert result.strong_analog_count == 1
    assert result.evidence_grade == "strong"


def test_strong_bar_is_configurable() -> None:
    engine = AnalogRetrievalEngine(AnalogRetrievalConfig(strong_similarity=0.6, required_strong=1))
    result = engine.retrieve(
        query=_state(1.0, 100.0),
        history=_history(("c", 1.5, 100.0, 5.0)),  # sim 0.667 >= 0.6
    )
    assert result.evidence_grade == "strong"


# --- validation -----------------------------------------------------


def test_state_validation() -> None:
    with pytest.raises(ValueError):
        make_state(("vol", "level"), (1.0,))
    with pytest.raises(ValueError):
        make_state((), ())
    with pytest.raises(ValueError):
        make_state(("vol",), (float("nan"),))


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        AnalogRetrievalConfig(min_similarity=1.5)
    with pytest.raises(ValueError):
        AnalogRetrievalConfig(min_similarity=0.9, strong_similarity=0.5)
    with pytest.raises(ValueError):
        AnalogRetrievalConfig(required_strong=0)
    with pytest.raises(ValueError):
        AnalogRetrievalConfig(top_k=0)
    with pytest.raises(ValueError):
        AnalogRetrievalConfig(feature_scales=(0.0,))
    with pytest.raises(ValueError):
        AnalogRetrievalConfig(feature_weights=(-1.0,))


def test_history_state_must_be_aligned() -> None:
    engine = AnalogRetrievalEngine()
    other = HistoricalAnalog(
        timestamp="t",
        state=make_state(("vol",), (1.0,)),
        outcome_return_pct=0.0,
    )
    with pytest.raises(ValueError):
        engine.retrieve(
            query=_state(1.0, 100.0),
            history=(other,),
        )


# --- as_dict --------------------------------------------------------


def test_result_as_dict_round_trip() -> None:
    engine = AnalogRetrievalEngine()
    result = engine.retrieve(query=_state(1.0, 100.0), history=_history(("a", 1.0, 100.0, 1.0)))
    data = result.as_dict()
    assert data["evidence_grade"] == "weak"
    assert data["strong_analog_count"] == 1
    assert data["analogs"][0]["timestamp"] == "a"
    assert data["analogs"][0]["similarity"] == pytest.approx(1.0)
    assert set(data.keys()) == {
        "query",
        "analogs",
        "weighted_expected_return_pct",
        "strong_analog_count",
        "evidence_grade",
        "confidence",
    }


def test_engine_is_deterministic() -> None:
    history = _history(("a", 1.0, 100.0, 1.0), ("c", 1.5, 100.0, 5.0))
    first = AnalogRetrievalEngine().retrieve(query=_state(1.0, 100.0), history=history).as_dict()
    second = AnalogRetrievalEngine().retrieve(query=_state(1.0, 100.0), history=history).as_dict()
    assert first == second
