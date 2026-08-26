"""Tests for PBO / Deflated Sharpe statistics (task P5-001).

The evidence layer must be as difficult to fool as possible: a headline
surviving out-of-sample evaluation must also be priced for the multiple
testing that produced it. These tests pin down the two canonical
statistics — the Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014)
and the Probability of Backtest Overfitting (Bailey et al. 2017) — on
constructed cases where the answer is known:

1. A zero-Sharpe series with n_trials=1 must deflate to DSR = 0.5 (no
   information, no deflation).
2. Deflation must be monotone in the number of trials and never negative.
3. PBO of pure noise must hover around 0.5 (selection is useless).
4. PBO of a family with one dominant trial must be near 0 (selection works).
5. Everything is deterministic (seeded) and validated against misuse.
"""

from __future__ import annotations

import pytest
from backend.domain.research.pbo import (
    DeflatedSharpeResult,
    PboResult,
    compute_deflated_sharpe,
    compute_pbo,
    expected_max_of_normal_normals,
)


class TestExpectedMaxOfNormalNormals:
    def test_single_trial_has_no_deflation(self):
        assert expected_max_of_normal_normals(1) == 0.0
        assert expected_max_of_normal_normals(0) == 0.0

    def test_harter_table_spot_checks(self):
        assert expected_max_of_normal_normals(2) == pytest.approx(0.56419)
        assert expected_max_of_normal_normals(5) == pytest.approx(1.16296)
        assert expected_max_of_normal_normals(10) == pytest.approx(1.53875)
        assert expected_max_of_normal_normals(20) == pytest.approx(1.86748)

    def test_monotonic_increasing(self):
        values = [expected_max_of_normal_normals(n) for n in range(2, 40)]
        assert values == sorted(values)

    def test_asymptotic_region_is_sane(self):
        # Beyond the table the asymptotic form takes over; E[max] must stay
        # in (E[max@20], 4) for reasonable trial counts.
        assert 1.86748 < expected_max_of_normal_normals(100) < 4.0
        assert expected_max_of_normal_normals(1000) > expected_max_of_normal_normals(100)


class TestDeflatedSharpe:
    @staticmethod
    def symmetric_zero_mean_returns() -> list[float]:
        # Mean exactly 0, skew exactly 0, variance > 0: any deviation from
        # DSR = 0.5 is a multiple-testing artifact, not signal.
        return [-0.1, -0.05, 0.05, 0.1]

    def test_zero_sharpe_no_deflation_is_fifty_percent(self):
        result = compute_deflated_sharpe(self.symmetric_zero_mean_returns(), n_trials=1)
        assert result.dsr == pytest.approx(0.5, abs=1e-6)
        assert result.sharpe == pytest.approx(0.0, abs=1e-6)
        assert result.sr0 == pytest.approx(0.0, abs=1e-9)
        assert result.expected_max == 0.0

    def test_returns_result_shape(self):
        result = compute_deflated_sharpe(self.symmetric_zero_mean_returns(), n_trials=1)
        assert isinstance(result, DeflatedSharpeResult)
        assert result.n_observations == 4
        assert result.n_trials == 1
        assert result.as_dict()["dsr"] == result.dsr

    def test_deflation_is_monotone_in_trials(self):
        returns = [0.01, -0.005, 0.008, -0.003, 0.012, 0.004, -0.007, 0.009]
        dsr_single = compute_deflated_sharpe(returns, n_trials=1).dsr
        dsr_ten = compute_deflated_sharpe(returns, n_trials=10).dsr
        dsr_hundred = compute_deflated_sharpe(returns, n_trials=100).dsr
        assert dsr_ten < dsr_single
        assert dsr_hundred < dsr_ten

    def test_deflated_sharpe_never_exceeds_undeflated(self):
        returns = [0.02, 0.01, 0.03, -0.01, 0.015, 0.005]
        for n in (1, 5, 50):
            result = compute_deflated_sharpe(returns, n_trials=n)
            assert 0.0 <= result.dsr <= 1.0
        assert (
            compute_deflated_sharpe(returns, n_trials=50).dsr
            <= compute_deflated_sharpe(returns, n_trials=1).dsr
        )

    def test_strong_positive_sharpe_survives_moderate_deflation(self):
        # High mean, low variance: even with 20 trials the DSR must stay high.
        returns = [0.05] * 3 + [0.04, 0.06, 0.05, 0.045, 0.055]
        result = compute_deflated_sharpe(returns, n_trials=20)
        assert result.dsr > 0.95

    def test_rejects_fewer_than_four_observations(self):
        with pytest.raises(ValueError, match="at least 4"):
            compute_deflated_sharpe([0.01, 0.02, 0.03], n_trials=1)

    def test_rejects_zero_variance(self):
        with pytest.raises(ValueError, match="non-zero variance"):
            compute_deflated_sharpe([0.01, 0.01, 0.01, 0.01], n_trials=1)

    def test_rejects_invalid_trials_count(self):
        with pytest.raises(ValueError, match="n_trials"):
            compute_deflated_sharpe([0.01, 0.02, 0.03, 0.04], n_trials=0)


class TestComputePbo:
    @staticmethod
    def noise_matrix(n_trials: int = 20, t: int = 30, seed: int = 42) -> list[list[float]]:
        import random

        rng = random.Random(seed)
        return [[rng.gauss(0.0, 0.01) for _ in range(t)] for _ in range(n_trials)]

    @staticmethod
    def one_dominant_trial_matrix(n_trials: int = 10, t: int = 30) -> list[list[float]]:
        # Trial 0 is strictly better everywhere: in-sample selection must
        # survive out-of-sample ranking.
        matrix = [[0.0] * t for _ in range(n_trials)]
        for i in range(t):
            matrix[0][i] = 0.02
            for row in matrix[1:]:
                row[i] = (i % 7 - 3) * 0.001
        return matrix

    def test_pure_noise_pbo_near_fifty_percent(self):
        result = compute_pbo(self.noise_matrix())
        assert isinstance(result, PboResult)
        assert 0.25 < result.pbo < 0.75
        assert result.n_trials == 20
        assert result.n_observations == 30
        assert result.n_splits >= 1

    def test_dominant_trial_survives_selection(self):
        result = compute_pbo(self.one_dominant_trial_matrix(), n_splits=20)
        assert result.pbo == pytest.approx(0.0, abs=0.01)

    def test_is_deterministic_given_seed(self):
        a = compute_pbo(self.noise_matrix(), seed=7)
        b = compute_pbo(self.noise_matrix(), seed=7)
        assert a.as_dict() == b.as_dict()

    def test_exhausts_all_splits_when_small(self):
        # t=4 -> C(4,2)=6 halves; n_splits larger than the space uses all.
        result = compute_pbo([[1.0, 2.0, 3.0, 4.0], [4.0, 3.0, 2.0, 1.0]], n_splits=100)
        assert result.n_splits == 6
        assert result.seed is None  # no sampling needed -> seed not recorded

    def test_sharpe_metric_supported(self):
        result = compute_pbo(self.noise_matrix(), metric="sharpe")
        assert 0.0 <= result.pbo <= 1.0
        assert result.metric == "sharpe"

    def test_n_selected_from_fraction(self):
        result = compute_pbo(self.noise_matrix(n_trials=10), n_select_fraction=0.3)
        assert result.n_selected == 3

    def test_as_dict_round_trips(self):
        result = compute_pbo(self.noise_matrix())
        data = result.as_dict()
        assert data["pbo"] == result.pbo
        assert data["n_trials"] == result.n_trials

    def test_rejects_single_trial(self):
        with pytest.raises(ValueError, match="at least two trials"):
            compute_pbo([[0.01] * 10])

    def test_rejects_few_observations(self):
        with pytest.raises(ValueError, match="at least 4"):
            compute_pbo([[0.01, 0.02, 0.03], [0.01, 0.02, 0.03]])

    def test_rejects_ragged_matrix(self):
        with pytest.raises(ValueError, match="same observation count"):
            compute_pbo([[0.01, 0.02, 0.03, 0.04], [0.01, 0.02, 0.03]])

    def test_rejects_bad_select_fraction(self):
        with pytest.raises(ValueError, match="n_select_fraction"):
            compute_pbo(self.noise_matrix(), n_select_fraction=0.0)
        with pytest.raises(ValueError, match="n_select_fraction"):
            compute_pbo(self.noise_matrix(), n_select_fraction=1.5)

    def test_rejects_bad_splits_and_metric(self):
        with pytest.raises(ValueError, match="n_splits"):
            compute_pbo(self.noise_matrix(), n_splits=0)
        with pytest.raises(ValueError, match="metric"):
            compute_pbo(self.noise_matrix(), metric="median")
