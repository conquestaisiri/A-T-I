"""Unit tests for portfolio-level correlation extraction (task T2-13-2)."""

from __future__ import annotations

import pytest
from backend.application.research.portfolio_correlations import correlations_from_returns
from backend.domain.research.portfolio_correlations import (
    PairCorrelationState,
    PortfolioCorrelationMatrix,
)


class TestValidation:
    def test_requires_two_series(self) -> None:
        with pytest.raises(ValueError, match="at least two series"):
            correlations_from_returns({"a": [1.0, 2.0]})

    def test_rejects_misaligned_lengths(self) -> None:
        with pytest.raises(ValueError, match="lengths differ"):
            correlations_from_returns({"a": [1.0, 2.0, 3.0], "b": [1.0, 2.0]})

    def test_rejects_single_observation(self) -> None:
        with pytest.raises(ValueError, match="at least 2 are required"):
            correlations_from_returns({"a": [1.0], "b": [2.0]})

    def test_rejects_non_numeric(self) -> None:
        with pytest.raises(ValueError, match="non-numeric"):
            correlations_from_returns({"a": [1.0, "x"], "b": [2.0, 3.0]})  # type: ignore[list-item]

    def test_rejects_non_finite(self) -> None:
        with pytest.raises(ValueError, match="non-finite"):
            correlations_from_returns({"a": [1.0, float("inf")], "b": [2.0, 3.0]})

    def test_rejects_string_series(self) -> None:
        with pytest.raises(ValueError, match="sequence of numbers"):
            correlations_from_returns({"a": "12", "b": "34"})  # type: ignore[dict-item]


class TestExtraction:
    def test_perfectly_correlated(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 2.0, 3.0, 4.0], "b": [2.0, 4.0, 6.0, 8.0]})
        assert surface.matrix[0][1] == pytest.approx(1.0)
        assert surface.matrix[1][0] == pytest.approx(1.0)
        assert surface.pairs[0].state is PairCorrelationState.MEASURED
        assert surface.pairs[0].n_shared == 4

    def test_perfectly_anti_correlated(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 2.0, 3.0], "b": [3.0, 2.0, 1.0]})
        assert surface.matrix[0][1] == pytest.approx(-1.0)

    def test_independent_series(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 2.0, 1.0, 2.0], "b": [3.0, 3.0, 3.0, 3.0]})
        # b is constant -> pair is neutral, not measured
        assert surface.pairs[0].state is PairCorrelationState.CONSTANT_SERIES
        assert surface.pairs[0].value is None
        assert surface.matrix[0][1] == 0.0

    def test_matrix_is_symmetric_with_unit_diagonal(self) -> None:
        surface = correlations_from_returns(
            {"a": [1.0, 2.0, 3.0], "b": [2.0, 1.0, 4.0], "c": [3.0, 3.0, 1.0]}
        )
        n = len(surface.ids)
        assert surface.ids == ("a", "b", "c")
        for i in range(n):
            assert surface.matrix[i][i] == 1.0
            for j in range(n):
                assert surface.matrix[i][j] == surface.matrix[j][i]

    def test_ids_are_sorted_for_determinism(self) -> None:
        surface = correlations_from_returns({"zeta": [1.0, 2.0], "alpha": [2.0, 1.0]})
        assert surface.ids == ("alpha", "zeta")
        assert surface.pairs[0].left == "alpha"
        assert surface.pairs[0].right == "zeta"

    def test_pairs_are_unique_and_complete(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 2.0], "b": [2.0, 1.0], "c": [3.0, 3.0]})
        assert len(surface.pairs) == 3  # a-b, a-c, b-c
        for pair in surface.pairs:
            assert pair.left < pair.right

    def test_as_matrix_shape(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 2.0], "b": [2.0, 1.0]})
        matrix = surface.as_matrix()
        assert matrix == [[1.0, pytest.approx(-1.0)], [pytest.approx(-1.0), 1.0]]

    def test_as_dict_round_trips_values(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 2.0], "b": [2.0, 1.0]})
        payload = surface.as_dict()
        assert payload["ids"] == ["a", "b"]
        assert payload["pairs"][0]["state"] == "measured"
        assert payload["pairs"][0]["n_shared"] == 2


class TestNeutralStates:
    def test_constant_series_pair_recorded(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 1.0, 1.0], "b": [2.0, 3.0, 4.0]})
        pair = surface.pairs[0]
        assert pair.state is PairCorrelationState.CONSTANT_SERIES
        assert pair.value is None
        assert pair.n_shared == 3

    def test_constant_pair_does_not_break_measured_pairs(self) -> None:
        surface = correlations_from_returns(
            {
                "a": [1.0, 2.0, 3.0],
                "b": [1.0, 1.0, 1.0],
                "c": [3.0, 2.0, 1.0],
            }
        )
        by_pair = {(p.left, p.right): p for p in surface.pairs}
        assert by_pair[("a", "c")].state is PairCorrelationState.MEASURED
        assert by_pair[("a", "c")].value == pytest.approx(-1.0)
        assert by_pair[("a", "b")].state is PairCorrelationState.CONSTANT_SERIES
        assert by_pair[("b", "c")].state is PairCorrelationState.CONSTANT_SERIES

    def test_type_contract(self) -> None:
        surface = correlations_from_returns({"a": [1.0, 2.0], "b": [2.0, 1.0]})
        assert isinstance(surface, PortfolioCorrelationMatrix)
