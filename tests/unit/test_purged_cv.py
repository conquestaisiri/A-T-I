"""Unit tests for label-aware purged cross-validation (task P0-007).

The acceptance criteria are:
1. Training labels overlapping test labels are purged.
2. Embargo is explicit.
3. No future training observations are used for a test fold unless the
   selected validation design explicitly allows it (K-fold / CPCV do, by
   definition; WalkForwardCV never does).
4. Adversarial leakage tests pass.
5. Documentation explains interval semantics (see the module docstring).

Each test below constructs an adversarial label layout and asserts the exact
train/test split that a leak-free validator must produce.
"""

from __future__ import annotations

import numpy as np
import pytest
from backend.application.validation.purged_cv import (
    CombinatorialPurgedCV,
    PurgedKFold,
    WalkForwardCV,
    compute_purged_metrics,
)
from numpy.typing import NDArray


def _train_index(
    cv: PurgedKFold | WalkForwardCV | CombinatorialPurgedCV,
    X: np.ndarray,
    fold: int,
    **kwargs: np.ndarray,
) -> NDArray[np.intp]:
    """Return the training indices of ``fold`` produced by ``cv.split``."""
    splits = cv.split(X, **kwargs)
    return splits[fold][0]


def _test_index(
    cv: PurgedKFold | WalkForwardCV | CombinatorialPurgedCV,
    X: np.ndarray,
    fold: int,
    **kwargs: np.ndarray,
) -> NDArray[np.intp]:
    """Return the test indices of ``fold`` produced by ``cv.split``."""
    splits = cv.split(X, **kwargs)
    return splits[fold][1]


# ---------------------------------------------------------------------------
# Index-fallback behaviour (no label intervals supplied).
# ---------------------------------------------------------------------------


def test_purged_kfold_index_fallback_partitions_data() -> None:
    X = np.arange(10)
    cv = PurgedKFold(n_splits=5)
    splits = cv.split(X)

    assert len(splits) == 5
    all_test = np.concatenate([test for _, test in splits])
    assert np.array_equal(np.sort(all_test), np.arange(10))

    for fold, (train, test) in enumerate(splits):
        expected_test = np.arange(2 * fold, 2 * fold + 2)
        assert np.array_equal(test, expected_test)
        # Without label intervals every point is a point at its own index, so
        # the index fallback must equal plain K-fold: train is the complement.
        expected_train = np.setdiff1d(np.arange(10), expected_test)
        assert np.array_equal(np.sort(train), np.sort(expected_train))


def test_purged_kfold_requires_at_least_as_many_samples_as_splits() -> None:
    cv = PurgedKFold(n_splits=5)
    with pytest.raises(ValueError, match="n_samples"):
        cv.split(np.arange(4))


def test_purged_kfold_uneven_last_fold() -> None:
    # 10 samples / 4 splits -> fold_size = 2, so folds are [0,1],[2,3],[4,5]
    # and the last fold absorbs the remainder: [6,7,8,9].
    X = np.arange(10)
    cv = PurgedKFold(n_splits=4)
    splits = cv.split(X)
    assert len(splits) == 4
    assert np.array_equal(splits[3][1], np.array([6, 7, 8, 9]))


# ---------------------------------------------------------------------------
# Adversarial purging: a training label reaching into the test period leaks.
# ---------------------------------------------------------------------------


def test_training_label_overlapping_test_period_is_purged() -> None:
    # 8 samples, 4 folds -> folds [0,1],[2,3],[4,5],[6,7]. Test fold = [2,3].
    # Sample 1 (a training sample) has label interval [1, 2.5]: it reaches
    # into the test period [2,3], so the label computed for sample 1 used test
    # data. A leak-free validator must drop sample 1 from fold 1's training.
    X = np.arange(8)
    start = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    end = np.array([0.0, 2.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    cv = PurgedKFold(n_splits=4)
    train = _train_index(cv, X, 1, label_start=start, label_end=end)
    test = _test_index(cv, X, 1, label_start=start, label_end=end)

    assert np.array_equal(test, np.array([2, 3]))
    assert 1 not in train, "training sample 1 leaks into the test period"
    assert np.array_equal(np.sort(train), np.array([0, 4, 5, 6, 7]))


def test_purging_is_per_fold_not_global() -> None:
    # The same leaking sample 1 is legitimate training for folds whose test
    # period starts after sample 1's label interval ends. Purging must be
    # scoped to the fold, not applied once globally.
    X = np.arange(8)
    start = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    end = np.array([0.0, 2.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    cv = PurgedKFold(n_splits=4)

    # Fold 1 tests [2,3]; sample 1's label [1, 2.5] reaches into it -> purged.
    train = _train_index(cv, X, 1, label_start=start, label_end=end)
    assert 1 not in train, "sample 1 overlaps fold 1's test period"

    # Fold 2 tests [4,5]; sample 1's label ends at 2.5, before test_start=4.
    train = _train_index(cv, X, 2, label_start=start, label_end=end)
    assert 1 in train, "sample 1 does not overlap fold 2's test period"

    # Fold 3 tests [6,7]; sample 1 is entirely in the past.
    train = _train_index(cv, X, 3, label_start=start, label_end=end)
    assert 1 in train, "sample 1 does not overlap fold 3's test period"


def test_purged_kfold_without_overlap_keeps_all_training_samples() -> None:
    X = np.arange(8)
    start = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    end = start + 0.5  # every label strictly before the next sample

    cv = PurgedKFold(n_splits=4)
    splits = cv.split(X, label_start=start, label_end=end)

    expected = np.array([0, 1, 4, 5, 6, 7])  # all but fold [2,3]
    for _, (train, test) in enumerate(splits):
        if np.array_equal(test, np.array([2, 3])):
            assert np.array_equal(np.sort(train), expected)
        else:
            assert len(train) == 6, "no sample may be purged without overlap"


# ---------------------------------------------------------------------------
# Embargo: an explicit gap, in label-interval units, after the test period.
# ---------------------------------------------------------------------------


def test_embargo_removes_training_beginning_immediately_after_test() -> None:
    # 6 samples, 3 folds -> folds [0,1],[2,3],[4,5]. Test fold = [2,3]:
    # test period [2,3]. Sample 4 starts at exactly test_end=3; with an
    # embargo of 1 it lies inside [3, 4) and must be purged; without an
    # embargo it is legitimate.
    X = np.arange(6)
    start = np.array([0.0, 1.0, 2.0, 3.0, 3.0, 5.0])
    end = np.array([0.0, 1.0, 2.0, 3.0, 3.5, 5.0])

    cv = PurgedKFold(n_splits=3, embargo=1.0)
    train = _train_index(cv, X, 1, label_start=start, label_end=end)
    assert 4 not in train, "sample 4 starts within the embargo after the test"

    cv = PurgedKFold(n_splits=3, embargo=0.0)
    train = _train_index(cv, X, 1, label_start=start, label_end=end)
    assert 4 in train, "without embargo sample 4 is legitimate"


def test_embargo_keeps_training_at_or_after_the_boundary() -> None:
    X = np.arange(6)
    start = np.array([0.0, 1.0, 2.0, 3.0, 3.0, 4.0])
    end = np.array([0.0, 1.0, 2.0, 3.0, 3.5, 4.0])

    # test_end = 3, embargo = 1 -> boundary 4. Sample 5 starts at 4: kept.
    cv = PurgedKFold(n_splits=3, embargo=1.0)
    train = _train_index(cv, X, 1, label_start=start, label_end=end)
    assert 5 in train, "sample starting at test_end + embargo must be kept"


def test_default_embargo_is_zero() -> None:
    cv = PurgedKFold(n_splits=3)
    assert cv.embargo == 0.0


# ---------------------------------------------------------------------------
# Walk-forward: strict past-to-future causality, no future training data.
# ---------------------------------------------------------------------------


def test_walk_forward_never_trains_on_future() -> None:
    X = np.arange(10)
    cv = WalkForwardCV(train_size=4, test_size=2)
    splits = cv.split(X)

    assert len(splits) == 3  # [0..3]|[4,5], [0..5]|[6,7], [0..7]|[8,9]
    for train, test in splits:
        assert len(test) == 2
        assert max(train) < min(test), "training must be strictly before test"
        # Expanding window: training grows and is always the full past.
        assert np.array_equal(train, np.arange(0, min(test)))


def test_walk_forward_purges_label_reaching_into_test() -> None:
    # First fold: test [3,4]. Sample 2's label ends at 3.5, inside the test
    # period [3,4]; it must be purged from training.
    X = np.arange(8)
    start = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    end = np.array([0.0, 1.0, 3.5, 3.0, 4.0, 5.0, 6.0, 7.0])

    cv = WalkForwardCV(train_size=3, test_size=2)
    train = _train_index(cv, X, 0, label_start=start, label_end=end)
    assert 2 not in train, "sample 2's label reaches into the test period"


def test_walk_forward_embargo_widens_the_safety_gap() -> None:
    # First fold: test [3,4], test_start=3, embargo=1 -> limit 2. Only
    # samples with end <= 2 may train; sample 2 (end=2) is the last survivor.
    X = np.arange(8)
    cv = WalkForwardCV(train_size=3, test_size=2, embargo=1.0)
    train = _train_index(cv, X, 0)
    assert np.array_equal(train, np.array([0, 1, 2]))


def test_walk_forward_rolling_window() -> None:
    X = np.arange(12)
    cv = WalkForwardCV(train_size=3, test_size=2, step_size=2, expanding=False)
    splits = cv.split(X)

    assert len(splits) == 4  # windows at 3, 5, 7, 9
    expected_tests = [
        np.array([3, 4]),
        np.array([5, 6]),
        np.array([7, 8]),
        np.array([9, 10]),
    ]
    expected_trains = [
        np.array([0, 1, 2]),
        np.array([2, 3, 4]),
        np.array([4, 5, 6]),
        np.array([6, 7, 8]),
    ]
    for (train, test), exp_train, exp_test in zip(
        splits, expected_trains, expected_tests, strict=True
    ):
        assert np.array_equal(test, exp_test)
        assert np.array_equal(train, exp_train)


def test_walk_forward_requires_positive_test_size() -> None:
    cv = WalkForwardCV(train_size=5, test_size=0)
    with pytest.raises(ValueError, match="test_size"):
        cv.split(np.arange(10))


def test_walk_forward_produces_no_folds_when_too_short() -> None:
    X = np.arange(5)
    cv = WalkForwardCV(train_size=5, test_size=2)
    assert cv.split(X) == []


# ---------------------------------------------------------------------------
# Combinatorial purged CV.
# ---------------------------------------------------------------------------


def test_cpcv_generates_all_test_combinations() -> None:
    # 4 splits, 2 test folds -> C(4,2) = 6 combinations. The extreme
    # combination (test folds 0 and 3) spans the whole series, so with point
    # labels every intermediate train sample is purged and the empty training
    # set is dropped -> 5 emitted combinations, 6 unique fold-combinations
    # tested.
    X = np.arange(8)
    cv = CombinatorialPurgedCV(n_splits=4, n_test_groups=2)
    splits = cv.split(X)

    assert len(splits) == 5
    combos = {tuple(int(i) for i in test) for _, test in splits}
    assert len(combos) == 5  # all distinct
    for train, test in splits:
        # Train and test fold indices are disjoint.
        assert len(np.intersect1d(train, test)) == 0
        assert len(test) == 4  # two folds of two samples each

    # Every sample is tested in at least one combination.
    all_test = np.concatenate([test for _, test in splits])
    assert np.array_equal(np.sort(np.unique(all_test)), np.arange(8))


def test_cpcv_purges_training_overlapping_test_combination() -> None:
    # Sample 1 has label [1, 2.5]. It must be purged from every combination
    # whose test period reaches back to or past 2.5, and kept only by the
    # combination whose test period starts after it (test folds 2 and 3 ->
    # test [4,5,6,7], test_start = 4 > 2.5).
    X = np.arange(8)
    start = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    end = np.array([0.0, 2.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    cv = CombinatorialPurgedCV(n_splits=4, n_test_groups=2)
    splits = cv.split(X, label_start=start, label_end=end)

    survivor = [train for train, test in splits if 1 in train]
    assert len(survivor) == 1, "sample 1 trains exactly one combination"
    (train,) = survivor
    assert np.array_equal(train, np.array([0, 1, 2, 3]))


def test_cpcv_validates_arguments() -> None:
    X = np.arange(10)
    with pytest.raises(ValueError, match="n_test_groups"):
        CombinatorialPurgedCV(n_splits=4, n_test_groups=0).split(X)
    with pytest.raises(ValueError, match="n_test_groups"):
        CombinatorialPurgedCV(n_splits=4, n_test_groups=4).split(X)
    with pytest.raises(ValueError, match="n_samples"):
        CombinatorialPurgedCV(n_splits=12, n_test_groups=2).split(X)


# ---------------------------------------------------------------------------
# Label-interval validation.
# ---------------------------------------------------------------------------


def test_label_arrays_must_be_provided_together() -> None:
    X = np.arange(8)
    cv = PurgedKFold(n_splits=4)
    with pytest.raises(ValueError, match="together"):
        cv.split(X, label_start=np.arange(8, dtype=float))
    with pytest.raises(ValueError, match="together"):
        cv.split(X, label_end=np.arange(8, dtype=float))


def test_label_arrays_must_match_sample_count() -> None:
    X = np.arange(8)
    cv = PurgedKFold(n_splits=4)
    with pytest.raises(ValueError, match="one value per sample"):
        cv.split(X, label_start=np.arange(7, dtype=float), label_end=np.arange(7, dtype=float))
    with pytest.raises(ValueError, match="one value per sample"):
        cv.split(
            X,
            label_start=np.arange(8, dtype=float),
            label_end=np.arange(9, dtype=float),
        )


def test_label_end_must_not_precede_label_start() -> None:
    X = np.arange(8)
    cv = PurgedKFold(n_splits=4)
    bad_end = np.array([0.0, 1.0, 0.5, 3.0, 4.0, 5.0, 6.0, 7.0])
    with pytest.raises(ValueError, match="label_end must be >= label_start"):
        cv.split(X, label_start=np.arange(8, dtype=float), label_end=bad_end)


# ---------------------------------------------------------------------------
# compute_purged_metrics.
# ---------------------------------------------------------------------------


def test_metrics_perfect_predictions() -> None:
    X = np.arange(20)
    y_true = np.arange(20, dtype=float)
    y_pred = np.arange(20, dtype=float)

    metrics = compute_purged_metrics(y_true, y_pred, PurgedKFold(n_splits=4), X)
    assert metrics["n_folds"] == 4
    assert metrics["mean_r2"] == pytest.approx(1.0)
    assert metrics["mean_corr"] == pytest.approx(1.0)
    assert all(s["n_test"] > 0 for s in metrics["scores"])


def test_metrics_forward_label_arrays_to_splitter() -> None:
    # A leaking sample means the metric folds use the same label-aware purging
    # as training; here the result must match the same adversarial layout.
    X = np.arange(8)
    y_true = np.arange(8, dtype=float)
    y_pred = np.arange(8, dtype=float)
    start = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])
    end = np.array([0.0, 2.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0])

    metrics = compute_purged_metrics(
        y_true, y_pred, PurgedKFold(n_splits=4), X, label_start=start, label_end=end
    )
    assert metrics["n_folds"] == 4


def test_metrics_all_nan_predictions_yield_empty_result() -> None:
    X = np.arange(20)
    y_true = np.arange(20, dtype=float)
    y_pred = np.full(20, np.nan)

    metrics = compute_purged_metrics(y_true, y_pred, PurgedKFold(n_splits=4), X)
    assert metrics == {"mean_r2": 0.0, "mean_corr": 0.0, "n_folds": 0}


def test_metrics_walk_forward() -> None:
    X = np.arange(20)
    y_true = np.arange(20, dtype=float)
    y_pred = np.arange(20, dtype=float)

    metrics = compute_purged_metrics(y_true, y_pred, WalkForwardCV(train_size=10, test_size=4), X)
    assert metrics["n_folds"] == 2
    assert metrics["mean_r2"] == pytest.approx(1.0)


def test_cv_settings_spec_walk_forward() -> None:
    """T1-6-1: the splitter must serialize its exact settings (embargo
    included) so evidence reports can surface the applied gap."""
    cv = WalkForwardCV(train_size=80, test_size=20, step_size=10, expanding=False, embargo=3.5)
    assert cv.as_dict() == {
        "method": "walk_forward",
        "train_size": 80,
        "test_size": 20,
        "step_size": 10,
        "expanding": False,
        "embargo": 3.5,
    }


def test_cv_settings_spec_defaults_embargo_zero() -> None:
    """The default splitter states its protection explicitly: embargo 0."""
    assert WalkForwardCV().as_dict()["embargo"] == 0.0
    assert PurgedKFold().as_dict()["embargo"] == 0.0
    assert CombinatorialPurgedCV().as_dict()["embargo"] == 0.0


def test_cv_settings_spec_purged_kfold() -> None:
    assert PurgedKFold(n_splits=6, embargo=2.0).as_dict() == {
        "method": "purged_kfold",
        "n_splits": 6,
        "embargo": 2.0,
    }


def test_cv_settings_spec_cpcv() -> None:
    assert CombinatorialPurgedCV(n_splits=8, n_test_groups=3, embargo=1.0).as_dict() == {
        "method": "combinatorial_purged_cv",
        "n_splits": 8,
        "n_test_groups": 3,
        "embargo": 1.0,
    }
