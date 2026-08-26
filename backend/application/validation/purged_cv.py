# backend/application/validation/purged_cv.py
"""Label-aware purged cross-validation for financial time series.

Prevents data leakage (Lopez de Prado, "Advances in Financial Machine
Learning") by two mechanisms:

1. Purging: any training sample whose **label interval** overlaps the test
   period is removed from training. A sample's label interval is the span of
   data its label is computed from; if it reaches into the test fold, the
   label leaks.
2. Embargo: an explicit gap, in the same units as the label intervals, is
   additionally cleared immediately after the test period to reduce
   serial-correlation leakage.

Interval semantics
------------------
Each sample i has a label interval ``[start_i, end_i]``. Pass parallel arrays
``label_start`` and ``label_end`` (one value per sample) to ``split``. When
omitted, every sample is treated as a point at its own index and the embargo
is measured in index units.

Design note on K-fold/CPCV: purged K-fold trains on samples both before and
after a test fold (every sample is tested in some fold). That future-in-time
training is the defining property of the K-fold design and is therefore
explicitly allowed. For strictly past-to-future validation use
:class:`WalkForwardCV`, which never trains on data at or after the test
period.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

_IntervalArrays = tuple[np.ndarray, np.ndarray]


def _label_intervals(
    n_samples: int, label_start: np.ndarray | None, label_end: np.ndarray | None
) -> _IntervalArrays:
    """Resolve label-interval arrays, validating them when provided."""
    if label_start is not None or label_end is not None:
        if label_start is None or label_end is None:
            raise ValueError("both label_start and label_end must be provided together")
        start = np.asarray(label_start, dtype=float)
        end = np.asarray(label_end, dtype=float)
        if start.shape != (n_samples,) or end.shape != (n_samples,):
            raise ValueError("label_start and label_end must each have one value per sample")
        if np.any(end < start):
            raise ValueError("label_end must be >= label_start for every sample")
        return start, end
    index = np.arange(n_samples, dtype=float)
    return index, index


def _purge_and_embargo(
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    embargo: float,
) -> np.ndarray:
    """Remove training samples overlapping the test period (K-fold designs).

    A training sample is kept only if its label interval ends entirely before
    the test period (``end < test_start``) or begins at or after the test
    period plus the embargo (``start >= test_end + embargo``). Samples whose
    intervals overlap the test period, or begin within the embargo after it,
    are purged.
    """
    if len(train_idx) == 0 or len(test_idx) == 0:
        return train_idx
    test_start = start[test_idx].min()
    test_end = end[test_idx].max()
    before = end[train_idx] < test_start
    after = start[train_idx] >= test_end + embargo
    return np.asarray(train_idx[before | after], dtype=np.intp)


def _purge_before_test(
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    start: np.ndarray,
    end: np.ndarray,
    embargo: float,
) -> np.ndarray:
    """Keep only training samples ending before the test period (walk-forward).

    A training sample is kept only if its label interval ends at or before
    ``test_start - embargo``. This enforces strict past-to-future causality:
    no training label may reach into the test period, and the embargo widens
    the safety gap.
    """
    if len(train_idx) == 0:
        return train_idx
    test_start = start[test_idx].min()
    limit = test_start - embargo
    return np.asarray(train_idx[end[train_idx] <= limit], dtype=np.intp)


@dataclass(frozen=True, slots=True)
class PurgedKFold:
    """Label-aware purged K-fold cross-validator.

    Parameters
    ----------
    n_splits: int
        Number of folds.
    embargo: float
        Explicit embargo, in label-interval units, cleared after each test
        fold (default 0 = no embargo).
    """

    n_splits: int = 5
    embargo: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Deterministic settings spec (feeds passport ``cv_spec``, T1-6-1)."""
        return {
            "method": "purged_kfold",
            "n_splits": self.n_splits,
            "embargo": self.embargo,
        }

    def split(
        self,
        X: np.ndarray,
        y: np.ndarray | None = None,
        groups: np.ndarray | None = None,
        label_start: np.ndarray | None = None,
        label_end: np.ndarray | None = None,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Generate label-aware purged train/test indices."""
        n_samples = len(X)
        if n_samples < self.n_splits:
            raise ValueError("n_samples must be >= n_splits")
        start, end = _label_intervals(n_samples, label_start, label_end)

        fold_size = n_samples // self.n_splits
        folds: list[np.ndarray] = []
        for i in range(self.n_splits):
            lo = i * fold_size
            hi = lo + fold_size if i < self.n_splits - 1 else n_samples
            folds.append(np.arange(lo, hi))

        result: list[tuple[np.ndarray, np.ndarray]] = []
        for i in range(self.n_splits):
            test_idx = folds[i]
            train_folds = [folds[j] for j in range(self.n_splits) if j != i]
            train_idx = np.concatenate(train_folds) if train_folds else np.array([], dtype=int)
            train_idx = _purge_and_embargo(train_idx, test_idx, start, end, self.embargo)
            result.append((train_idx, test_idx))
        return result


@dataclass(frozen=True, slots=True)
class WalkForwardCV:
    """Label-aware walk-forward cross-validation (expanding or rolling).

    Trains strictly on the past and tests on the future; no training sample's
    label interval may reach into the test period.

    Parameters
    ----------
    train_size: int
        Minimum number of samples for training.
    test_size: int
        Number of samples for testing.
    step_size: int
        Step size for the window advance (default = test_size).
    expanding: bool
        If True the training window expands; otherwise it rolls.
    embargo: float
        Explicit gap, in label-interval units, required between the end of any
        training label and the start of the test period (default 0).
    """

    train_size: int = 100
    test_size: int = 20
    step_size: int | None = None
    expanding: bool = True
    embargo: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Deterministic settings spec (feeds passport ``cv_spec``, T1-6-1).

        Key names are stable: existing consumers read ``train_size`` /
        ``test_size`` / ``step_size`` / ``expanding`` / ``embargo``.
        """
        return {
            "method": "walk_forward",
            "train_size": self.train_size,
            "test_size": self.test_size,
            "step_size": self.step_size,
            "expanding": self.expanding,
            "embargo": self.embargo,
        }

    def split(
        self,
        X: np.ndarray,
        y: np.ndarray | None = None,
        groups: np.ndarray | None = None,
        label_start: np.ndarray | None = None,
        label_end: np.ndarray | None = None,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Generate label-aware walk-forward train/test indices."""
        n_samples = len(X)
        if self.test_size <= 0:
            raise ValueError("test_size must be positive")
        step = self.step_size or self.test_size
        start, end = _label_intervals(n_samples, label_start, label_end)

        result: list[tuple[np.ndarray, np.ndarray]] = []
        window_start = self.train_size
        while window_start + self.test_size <= n_samples:
            test_idx = np.arange(window_start, window_start + self.test_size)
            if self.expanding:
                train_idx = np.arange(0, window_start)
            else:
                train_idx = np.arange(max(0, window_start - self.train_size), window_start)
            train_idx = _purge_before_test(train_idx, test_idx, start, end, self.embargo)
            result.append((train_idx, test_idx))
            window_start += step
        return result


@dataclass(frozen=True, slots=True)
class CombinatorialPurgedCV:
    """Combinatorial label-aware purged cross-validation (Lopez de Prado).

    Generates every combination of ``n_test_groups`` test folds and purges
    training labels overlapping each test combination, then combines the
    results for lower-variance performance estimates.

    Parameters
    ----------
    n_splits: int
        Total number of folds.
    n_test_groups: int
        Number of folds used for testing per combination (rest train).
    embargo: float
        Explicit embargo, in label-interval units, cleared after each test
        combination (default 0).
    """

    n_splits: int = 10
    n_test_groups: int = 2
    embargo: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        """Deterministic settings spec (feeds passport ``cv_spec``, T1-6-1)."""
        return {
            "method": "combinatorial_purged_cv",
            "n_splits": self.n_splits,
            "n_test_groups": self.n_test_groups,
            "embargo": self.embargo,
        }

    def split(
        self,
        X: np.ndarray,
        y: np.ndarray | None = None,
        groups: np.ndarray | None = None,
        label_start: np.ndarray | None = None,
        label_end: np.ndarray | None = None,
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """Generate combinatorial purged train/test indices."""
        n_samples = len(X)
        if n_samples < self.n_splits:
            raise ValueError("n_samples must be >= n_splits")
        if not 0 < self.n_test_groups < self.n_splits:
            raise ValueError("n_test_groups must be between 1 and n_splits - 1")
        start, end = _label_intervals(n_samples, label_start, label_end)

        fold_size = n_samples // self.n_splits
        folds: list[np.ndarray] = []
        for i in range(self.n_splits):
            lo = i * fold_size
            hi = lo + fold_size if i < self.n_splits - 1 else n_samples
            folds.append(np.arange(lo, hi))

        result: list[tuple[np.ndarray, np.ndarray]] = []
        for test_group in combinations(range(self.n_splits), self.n_test_groups):
            test_idx = np.concatenate([folds[i] for i in test_group])
            train_folds = [folds[i] for i in range(self.n_splits) if i not in test_group]
            train_idx = np.concatenate(train_folds) if train_folds else np.array([], dtype=int)
            train_idx = _purge_and_embargo(train_idx, test_idx, start, end, self.embargo)
            if len(train_idx) > 0:
                result.append((train_idx, test_idx))
        return result


def compute_purged_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    cv: PurgedKFold | WalkForwardCV | CombinatorialPurgedCV,
    X: np.ndarray,
    *,
    label_start: np.ndarray | None = None,
    label_end: np.ndarray | None = None,
) -> dict[str, Any]:
    """Compute cross-validated metrics with purged CV.

    Forwards ``label_start``/``label_end`` to the splitter when provided so
    metric folds use the same label-aware purging as training.
    """
    scores = []
    for _train_idx, test_idx in cv.split(X, label_start=label_start, label_end=label_end):
        y_test = y_true[test_idx]
        y_pred_test = y_pred[test_idx]

        # Filter valid
        valid = ~(np.isnan(y_test) | np.isnan(y_pred_test))
        if valid.sum() < 2:
            continue

        y_t = y_test[valid]
        y_p = y_pred_test[valid]

        # R²
        ss_res = np.sum((y_t - y_p) ** 2)
        ss_tot = np.sum((y_t - np.mean(y_t)) ** 2)
        r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0

        # Correlation
        corr = np.corrcoef(y_t, y_p)[0, 1] if len(y_t) > 1 else 0.0

        scores.append({"r2": r2, "corr": corr, "n_test": len(y_t)})

    if not scores:
        return {"mean_r2": 0.0, "mean_corr": 0.0, "n_folds": 0}

    return {
        "mean_r2": float(np.mean([s["r2"] for s in scores])),
        "mean_corr": float(np.mean([s["corr"] for s in scores])),
        "n_folds": len(scores),
        "scores": scores,
    }
