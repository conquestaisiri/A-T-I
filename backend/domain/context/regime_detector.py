# backend/domain/context/regime_detector.py
"""Market regime detection using Hidden Markov Model and changepoint detection.

Pure Python implementation (numpy, a core dependency, only):
- HMM: 2-state Gaussian HMM (bull/bear) via EM algorithm
- Changepoint: online CUSUM-based drift detection

Identifies market regimes to shift strategy per regime (+0.1-0.30 Sharpe).

Ownership (review gap G6): the detector is stateful feature estimation, so it
lives in the domain feature layer alongside ``micro_price`` and ``order_flow``
(their module-level state + ``get_state``/``set_*``/``reset_*`` pattern). The
application layer only owns the reset discipline (``bootstrap``) — it never
feeds it at runtime, and a domain feature never imports the application layer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegimeResult:
    """Result of regime detection."""

    regime: int  # 0 = low vol/bull, 1 = high vol/bear
    regime_label: str
    probability: float
    volatility: float
    trend: float
    changepoints: list[int]


class GaussianHMM:
    """2-state Gaussian Hidden Markov Model.

    Pure numpy implementation of EM algorithm for regime detection.
    States: 0 = low vol (bull), 1 = high vol (bear)
    """

    def __init__(self, n_states: int = 2, max_iter: int = 100, tol: float = 1e-6) -> None:
        self.n_states = n_states
        self.max_iter = max_iter
        self.tol = tol
        # Parameters
        self.means: np.ndarray = np.zeros(n_states)
        self.vars: np.ndarray = np.ones(n_states)
        self.trans: np.ndarray = np.eye(n_states) * 0.99 + 0.01 / n_states
        self.pi: np.ndarray = np.ones(n_states) / n_states

    def fit(self, X: np.ndarray) -> None:
        """Fit HMM via EM algorithm."""
        T = len(X)
        X = X.reshape(-1, 1)

        # Initialize with K-means-like split
        sorted_idx = np.argsort(X.ravel())
        split = T // 2
        self.means[0] = np.mean(X[sorted_idx[:split]])
        self.means[1] = np.mean(X[sorted_idx[split:]])
        self.vars[0] = np.var(X[sorted_idx[:split]]) + 1e-6
        self.vars[1] = np.var(X[sorted_idx[split:]]) + 1e-6

        for _iteration in range(self.max_iter):
            # E-step: compute responsibilities
            gamma = np.zeros((T, self.n_states))
            for k in range(self.n_states):
                gamma[:, k] = self.pi[k] * self._gaussian(X, self.means[k], self.vars[k])

            # Normalize
            gamma_sum = gamma.sum(axis=1, keepdims=True)
            gamma_sum[gamma_sum == 0] = 1
            gamma = gamma / gamma_sum

            # Forward-backward
            alpha = np.zeros((T, self.n_states))
            beta = np.zeros((T, self.n_states))
            scale = np.zeros(T)

            # Forward
            alpha[0] = gamma[0] * self.pi
            scale[0] = alpha[0].sum()
            if scale[0] == 0:
                scale[0] = 1
            alpha[0] = alpha[0] / scale[0]

            for t in range(1, T):
                alpha[t] = (alpha[t - 1] @ self.trans) * gamma[t]
                scale[t] = alpha[t].sum()
                if scale[t] == 0:
                    scale[t] = 1
                alpha[t] /= scale[t]

            # Backward
            beta[T - 1] = 1.0 / scale[T - 1]
            for t in range(T - 2, -1, -1):
                beta[t] = self.trans @ (gamma[t + 1] * beta[t + 1])
                beta[t] /= scale[t]

            # Posterior
            post = alpha * beta
            post_sum = post.sum(axis=1, keepdims=True)
            post_sum[post_sum == 0] = 1
            post = post / post_sum

            # M-step
            old_means = self.means.copy()
            for k in range(self.n_states):
                nk = post[:, k].sum()
                if nk > 0:
                    self.means[k] = (post[:, k] * X.ravel()).sum() / nk
                    self.vars[k] = (post[:, k] * (X.ravel() - self.means[k]) ** 2).sum() / nk + 1e-6

            # Transition matrix
            for i in range(self.n_states):
                for j in range(self.n_states):
                    self.trans[i, j] = (
                        alpha[:-1, i] * self.trans[i, j] * gamma[1:, j] * beta[1:, j]
                    ).sum()
                row_sum = self.trans[i].sum()
                if row_sum > 0:
                    self.trans[i] /= row_sum

            # Check convergence
            if np.abs(self.means - old_means).max() < self.tol:
                break

    def _gaussian(self, X: np.ndarray, mean: float, var: float) -> np.ndarray:
        """Gaussian PDF."""
        result: np.ndarray = np.exp(-0.5 * ((X.ravel() - mean) ** 2) / var) / np.sqrt(
            2 * np.pi * var
        )
        return result

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Predict regime for each observation."""
        T = len(X)
        X = X.reshape(-1, 1)
        gamma = np.zeros((T, self.n_states))
        for k in range(self.n_states):
            gamma[:, k] = self.pi[k] * self._gaussian(X, self.means[k], self.vars[k])

        gamma_sum = gamma.sum(axis=1, keepdims=True)
        gamma_sum[gamma_sum == 0] = 1
        gamma = gamma / gamma_sum

        regimes = np.argmax(gamma, axis=1)
        probs = np.max(gamma, axis=1)
        return regimes, probs


class RegimeDetector:
    """Market regime detector combining HMM + online changepoint detection."""

    def __init__(
        self,
        *,
        window: int = 60,
        threshold: float = 2.0,
        refit_interval: int = 50,
        bars_per_year: float = 365 * 24 * 12,
    ) -> None:
        self._window = window
        self._threshold = threshold
        self._refit_interval = refit_interval
        self._bars_per_year = bars_per_year
        self._returns: list[float] = []
        self._last_price: float = 0.0
        self._hmm = GaussianHMM(n_states=2)
        self._current_regime: int = 0
        self._regime_prob: float = 0.5

    def update(self, price: float) -> RegimeResult:
        """Update with new price and return current regime."""
        # Ignore non-positive prices: they carry no price signal and must not
        # clobber the previous valid price (which would break the next return).
        if price > 0:
            if self._last_price > 0:
                ret = (price - self._last_price) / self._last_price
                self._returns.append(ret)
            self._last_price = price

        # Keep only window
        if len(self._returns) > self._window:
            self._returns = self._returns[-self._window :]

        self._maybe_refit()
        return self._build_result()

    def snapshot(self) -> RegimeResult:
        """Return the current regime state without mutating the detector.

        Use this when no new price is available (e.g. a snapshot with no
        price-carrying events): the detector reports its last known regime
        rather than being fed a fabricated price.
        """
        return self._build_result()

    def _maybe_refit(self) -> None:
        """Refit or predict with the HMM when enough returns have accumulated."""
        if len(self._returns) < 20:
            return

        X = np.array(self._returns)
        # Refit every N observations to avoid 745ms blocking
        should_refit = len(self._returns) % self._refit_interval == 0
        if should_refit:
            try:
                self._hmm.fit(X)
                regimes, probs = self._hmm.predict(X)
                self._current_regime = int(regimes[-1])
                self._regime_prob = float(probs[-1])
            except Exception as exc:
                logger.debug("HMM failed: %s", exc)
                self._current_regime = 0
                self._regime_prob = 0.5
        else:
            # Predict using existing model (use HMM's argmax, not prob threshold)
            try:
                regimes, probs = self._hmm.predict(X)
                self._current_regime = int(regimes[-1])
                self._regime_prob = float(probs[-1])
            except Exception:
                pass

    def _build_result(self) -> RegimeResult:
        """Construct the regime result from the current detector state."""
        if len(self._returns) < 20:
            return RegimeResult(
                regime=0,
                regime_label="insufficient_data",
                probability=0.5,
                volatility=0.0,
                trend=0.0,
                changepoints=[],
            )

        X = np.array(self._returns)

        # Compute volatility (annualized)
        vol = float(np.std(self._returns) * np.sqrt(self._bars_per_year))

        # Compute trend
        trend = float(np.mean(self._returns)) * len(self._returns)

        # Changepoints via CUSUM
        changepoints = self._cusum_changepoints(X)

        regime_label = "high_vol" if self._current_regime == 1 else "low_vol"

        return RegimeResult(
            regime=self._current_regime,
            regime_label=regime_label,
            probability=self._regime_prob,
            volatility=vol,
            trend=trend,
            changepoints=changepoints,
        )

    def _cusum_changepoints(self, X: np.ndarray) -> list[int]:
        """CUSUM-based changepoint detection."""
        mean = np.mean(X)
        std = np.std(X) + 1e-6
        cusum_pos = 0.0
        cusum_neg = 0.0
        changepoints = []

        for i, x in enumerate(X):
            cusum_pos = max(0, cusum_pos + (x - mean) / std - 0.5)
            cusum_neg = max(0, cusum_neg - (x - mean) / std - 0.5)

            if cusum_pos > self._threshold or cusum_neg > self._threshold:
                changepoints.append(i)
                cusum_pos = 0.0
                cusum_neg = 0.0

        return changepoints

    @property
    def current_regime(self) -> int:
        return self._current_regime

    @property
    def current_regime_prob(self) -> float:
        return self._regime_prob


# Global detector (one per symbol in production)
_detectors: dict[str, RegimeDetector] = {}


def get_detector(symbol: str) -> RegimeDetector:
    """Get or create regime detector for a symbol."""
    if symbol not in _detectors:
        _detectors[symbol] = RegimeDetector()
    return _detectors[symbol]


def reset_detectors() -> None:
    """Reset all detectors."""
    _detectors.clear()
