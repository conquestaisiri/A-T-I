# backend/domain/research/pbo.py
"""False-discovery statistics for the evidence layer (task P5-001).

The second external review (docs/ATI_Strategic_Review.md, Tier-1 item 7) names
PBO / Deflated Sharpe as the missing pieces of the false-discovery defense:
any strategy headline that survives the OOS evaluator must also be priced for
the multiple testing that produced it. This module implements the two
canonical statistics:

- ``compute_deflated_sharpe``: the probability that a strategy's Sharpe ratio
  is positive after deflating by the expected best-of-N under a null
  (Bailey & Lopez de Prado 2014, "The Deflated Sharpe Ratio"). The deflation
  term is the expected maximum of N i.i.d. standard normals (Harter 1961).
- ``compute_pbo``: the Probability of Backtest Overfitting (Bailey, Borwein,
  Lopez de Prado & Zhu 2017): split every trial's observation axis into
  in-sample/out-of-sample halves, pick the trials that looked best in-sample,
  and measure how often their out-of-sample relative rank falls below the
  median. PBO ~ 0.5 means selection is useless (pure noise); PBO near 0 means
  in-sample selection survives out-of-sample.

Both functions are pure, deterministic (seeded) and free of any strategy
knowledge: they consume only return matrices, so they can grade any candidate
produced anywhere in the repository.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

_MIN_OBSERVATIONS = 4

# Euler-Mascheroni constant, used by the Bailey & Lopez de Prado (2014)
# expected-maximum approximation for n > 20.
_EULER_GAMMA = 0.5772156649015329


def expected_max_of_normal_normals(n: int) -> float:
    """Expected maximum of ``n`` i.i.d. standard normals (Harter 1961).

    Deterministic and conservative: Harter's exact tabulation for n <= 20,
    then the Bailey & Lopez de Prado (2014) closed form
    ``(1 - gamma) * Phi^-1(1 - 1/n) + gamma * Phi^-1(1 - 1/(n * e))`` which
    is monotone, converges well, and slightly over-deflates — the safe
    direction for a selection-bias correction. Returns 0.0 for n <= 1
    (no deflation for one trial).
    """
    if n <= 1:
        return 0.0
    return _bias_corrected_max(n)


def _bias_corrected_max(n: int) -> float:
    # Harter (1961) exact values for n <= 20; the Bailey & LdP closed form
    # beyond that (monotone, and slightly conservative).
    table = {
        2: 0.56419,
        3: 0.84628,
        4: 1.02938,
        5: 1.16296,
        6: 1.26721,
        7: 1.35218,
        8: 1.42360,
        9: 1.48501,
        10: 1.53875,
        11: 1.58644,
        12: 1.62923,
        13: 1.66799,
        14: 1.70338,
        15: 1.73591,
        16: 1.76599,
        17: 1.79394,
        18: 1.82003,
        19: 1.84448,
        20: 1.86748,
    }
    if n in table:
        return table[n]
    inverse = statistics.NormalDist().inv_cdf
    one_minus_gamma = 1.0 - _EULER_GAMMA
    return one_minus_gamma * inverse(1.0 - 1.0 / n) + _EULER_GAMMA * inverse(
        1.0 - 1.0 / (n * math.e)
    )


@dataclass(frozen=True, slots=True)
class DeflatedSharpeResult:
    """Deflated Sharpe Ratio outcome for one strategy's return series.

    Attributes
    ----------
    sharpe: float
        Annualized-free Sharpe of the observed return series (mean / std).
    skewness: float
        Sample skewness of the series.
    kurtosis: float
        Sample kurtosis of the series (Fisher-corrected, then +3 so it is the
        raw kurtosis gamma4 used by the DSR formula).
    n_observations: int
        Number of observations the Sharpe is estimated from.
    n_trials: int
        How many strategies/experiments were tried (the multiple-testing
        deflation multiplier).
    expected_max: float
        E[max of n_trials standard normals], the deflation term.
    sr0: float
        The expected best-of-N Sharpe under a null, subtracted from ``sharpe``.
    dsr: float
        Deflated Sharpe Ratio: probability in [0, 1] that the true Sharpe is
        positive after deflation.
    """

    sharpe: float
    skewness: float
    kurtosis: float
    n_observations: int
    n_trials: int
    expected_max: float
    sr0: float
    dsr: float

    def as_dict(self) -> dict[str, object]:
        """Serialise the DSR outcome to a plain dictionary."""
        return {
            "sharpe": self.sharpe,
            "skewness": self.skewness,
            "kurtosis": self.kurtosis,
            "n_observations": self.n_observations,
            "n_trials": self.n_trials,
            "expected_max": self.expected_max,
            "sr0": self.sr0,
            "dsr": self.dsr,
        }


def compute_deflated_sharpe(
    returns: Sequence[float],
    n_trials: int = 1,
) -> DeflatedSharpeResult:
    """Deflated Sharpe Ratio of one strategy's return series.

    Parameters
    ----------
    returns: Sequence[float]
        Per-period returns (e.g., per-fold OOS returns of one strategy).
    n_trials: int
        Number of independent strategies/experiments that produced this
        candidate (the multiple-testing count). 1 means no deflation.

    Returns
    -------
    DeflatedSharpeResult
        The DSR plus the intermediate terms needed to audit the math.
    """
    values = [float(r) for r in returns]
    if len(values) < _MIN_OBSERVATIONS:
        raise ValueError(f"at least {_MIN_OBSERVATIONS} observations are required")
    if n_trials < 1:
        raise ValueError("n_trials must be >= 1")
    mean = statistics.fmean(values)
    if len(values) < 2:
        raise ValueError("at least two observations are required")
    std = statistics.pstdev(values)
    if std <= 0.0:
        raise ValueError("returns must have non-zero variance to compute a Sharpe")
    sharpe = mean / std
    skew = _skew(values, mean, std)
    kurt_raw = _kurtosis_raw(values, mean, std)

    # Variance of the Sharpe estimator under non-normality (Bailey & LdP 2014).
    denominator = 1.0 - skew * sharpe + (kurt_raw - 1.0) / 4.0 * sharpe**2
    variance = denominator / (len(values) - 1)
    expected_max = expected_max_of_normal_normals(n_trials)
    sr0 = math.sqrt(variance) * expected_max

    z = (sharpe - sr0) / math.sqrt(variance)
    dsr = _normal_cdf(z)
    return DeflatedSharpeResult(
        sharpe=round(sharpe, 6),
        skewness=round(skew, 6),
        kurtosis=round(kurt_raw, 6),
        n_observations=len(values),
        n_trials=n_trials,
        expected_max=round(expected_max, 6),
        sr0=round(sr0, 6),
        dsr=round(dsr, 6),
    )


@dataclass(frozen=True, slots=True)
class PboResult:
    """Probability of Backtest Overfitting for a family of trials.

    Attributes
    ----------
    pbo: float
        Probability in [0, 1] that the best-in-sample trials underperform the
        median out-of-sample. ~0.5 means selection is worthless (noise);
        near 0 means in-sample selection survives out-of-sample.
    mean_logit: float
        Mean of the logit lambda across all splits and selected trials
        (positive = selection works on average).
    n_trials: int
        Number of strategies in the matrix (rows).
    n_observations: int
        Number of observations per trial (columns).
    n_splits: int
        Number of in-sample/out-of-sample splits evaluated.
    n_selected: int
        Number of trials selected per split (top ``n_select_fraction``).
    metric: str
        Performance measure used for ranking ("mean" or "sharpe").
    seed: int | None
        Seed used for deterministic split sampling (None = all splits used).
    """

    pbo: float
    mean_logit: float
    n_trials: int
    n_observations: int
    n_splits: int
    n_selected: int
    metric: str
    seed: int | None

    def as_dict(self) -> dict[str, object]:
        """Serialise the PBO outcome to a plain dictionary."""
        return {
            "pbo": self.pbo,
            "mean_logit": self.mean_logit,
            "n_trials": self.n_trials,
            "n_observations": self.n_observations,
            "n_splits": self.n_splits,
            "n_selected": self.n_selected,
            "metric": self.metric,
            "seed": self.seed,
        }


def compute_pbo(
    returns_matrix: Sequence[Sequence[float]],
    n_select_fraction: float = 0.5,
    n_splits: int = 100,
    seed: int | None = 42,
    metric: str = "mean",
) -> PboResult:
    """Probability of Backtest Overfitting over a matrix of trial returns.

    Parameters
    ----------
    returns_matrix: Sequence[Sequence[float]]
        One row per strategy (trial), one column per observation period.
        Rows must share the same length.
    n_select_fraction: float
        Fraction of trials kept as "best in-sample" per split (default 0.5).
    n_splits: int
        Maximum number of random in-sample/out-of-sample splits to evaluate.
        When the total number of possible halves is smaller, all are used.
    seed: int | None
        RNG seed for split sampling; None disables determinism.
    metric: str
        "mean" (default) or "sharpe" performance measure used for ranking.

    Returns
    -------
    PboResult
        PBO plus audit terms. PBO is the honest headline: how often the
        in-sample champion ranks below the median out-of-sample.
    """
    matrix = [[float(v) for v in row] for row in returns_matrix]
    n = len(matrix)
    t = len(matrix[0]) if matrix else 0
    if n < 2:
        raise ValueError("at least two trials are required")
    if t < _MIN_OBSERVATIONS:
        raise ValueError(f"at least {_MIN_OBSERVATIONS} observations per trial are required")
    if not all(len(row) == t for row in matrix):
        raise ValueError("all trials must share the same observation count")
    if not 0.0 < n_select_fraction <= 1.0:
        raise ValueError("n_select_fraction must be in (0, 1]")
    if n_splits < 1:
        raise ValueError("n_splits must be >= 1")
    if metric not in ("mean", "sharpe"):
        raise ValueError("metric must be 'mean' or 'sharpe'")

    # Every complementary half is a candidate split; sample without
    # replacement when the combination space is large. The total number of
    # halves is C(t, t//2), which grows factorially: never materialize the
    # whole space, unrank indices instead. For huge t (e.g. 500) even
    # math.comb overflows sampling, so generate random subsets directly.
    half = t // 2
    rng = random.Random(seed)
    # Fast path for huge t to avoid huge comb
    if t > 60:
        splits = [sorted(rng.sample(range(t), half)) for _ in range(n_splits)]
        used_seed: int | None = seed
    else:
        total_halves = math.comb(t, half)
        if total_halves > n_splits:
            splits = [
                _unrank_combination(i, t, half) for i in rng.sample(range(total_halves), n_splits)
            ]
            used_seed = seed
        else:
            splits = [list(c) for c in itertools.combinations(range(t), half)]
            used_seed = None

    n_selected = max(1, int(round(n * n_select_fraction)))
    logits: list[float] = []
    for in_sample in splits:
        in_sample_set = set(in_sample)
        out_sample = [i for i in range(t) if i not in in_sample_set]
        perf_in = [_metric(row, in_sample, metric) for row in matrix]
        perf_out = [_metric(row, out_sample, metric) for row in matrix]

        # Rank trials by in-sample performance, keep the best fraction.
        in_order = sorted(range(n), key=lambda i: perf_in[i], reverse=True)
        selected = in_order[:n_selected]

        # Relative out-of-sample rank of each selected trial: 1 = best,
        # n = worst; n* in (0,1) via continuity correction.
        out_order = sorted(range(n), key=lambda i: perf_out[i], reverse=True)
        pos = {trial: p for p, trial in enumerate(out_order)}
        for trial in selected:
            n_star = (n - pos[trial] - 0.5) / n
            logits.append(math.log(n_star / (1.0 - n_star)))

    pbo = sum(1.0 for lam in logits if lam < 0.0) / len(logits)
    return PboResult(
        pbo=round(pbo, 6),
        mean_logit=round(sum(logits) / len(logits), 6),
        n_trials=n,
        n_observations=t,
        n_splits=len(splits),
        n_selected=n_selected,
        metric=metric,
        seed=used_seed,
    )


def _unrank_combination(index: int, t: int, k: int) -> list[int]:
    """The ``index``-th k-combination of ``range(t)``, lexicographic order.

    Implements the combinatorial number system so splits can be sampled
    without ever materializing the (factorial-sized) combination space.
    """
    combination: list[int] = []
    start = 0
    for position in range(k, 0, -1):
        for candidate in range(start, t):
            count = math.comb(t - candidate - 1, position - 1)
            if index < count:
                combination.append(candidate)
                start = candidate + 1
                break
            index -= count
    return combination


def _metric(values: Sequence[float], indices: Sequence[int], metric: str) -> float:
    if metric == "sharpe":
        return _sharpe(values, indices)
    return _mean(values, indices)


def _skew(values: Sequence[float], mean: float, std: float) -> float:
    """Moment coefficient of skewness: m3 / std^3 (biased, standard default)."""
    if len(values) < 3 or std <= 0.0:
        return 0.0
    m3 = sum((v - mean) ** 3 for v in values) / len(values)
    return m3 / std**3


def _kurtosis_raw(values: Sequence[float], mean: float, std: float) -> float:
    """Raw (non-excess) kurtosis gamma4: m4 / std^4 (3.0 for a normal)."""
    if len(values) < 4 or std <= 0.0:
        return 3.0
    m4 = sum((v - mean) ** 4 for v in values) / len(values)
    return m4 / std**4


def _mean(values: Sequence[float], indices: Sequence[int]) -> float:
    return statistics.fmean([values[i] for i in indices])


def _sharpe(values: Sequence[float], indices: Sequence[int]) -> float:
    subset = [values[i] for i in indices]
    mean = statistics.fmean(subset)
    if len(subset) < 2:
        return 0.0
    std = statistics.pstdev(subset)
    return mean / std if std > 0.0 else 0.0


def _normal_cdf(z: float) -> float:
    """Standard normal CDF via the error function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
