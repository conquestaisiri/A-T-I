# backend/application/research/label_engine.py
"""Label-generation framework (task P1-002).

Turns a versioned historical dataset (P1-001) into a labeled training set.

Causal correctness is enforced structurally:

1. **Features are point-in-time**: the feature snapshot for a sample at
   ``decision_time`` uses only records whose *source time* precedes it. The
   label of a sample can never be derived from the same data the features see,
   because the label window is strictly forward of the sample.
2. **The label window is explicit and recorded**: each sample stores
   ``label_start``/``label_end`` (the source-time interval its label was
   computed over). This is exactly the interval purged cross-validation needs.
3. **Labels are defined before training**: the engine takes a declarative
   :class:`LabelDefinition` and nothing else; there is no ad-hoc labeling.

Label math is delegated to :mod:`backend.application.validation.triple_barrier`
for triple-barrier and meta-labels; the fixed-horizon scheme uses the raw
price at decision time vs. the price ``horizon`` records later, which is the
explicit forward-looking window.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from backend.application.interfaces.dataset_store import DatasetStore
from backend.application.validation.triple_barrier import (
    TripleBarrierConfig,
    label_triple_barrier,
)
from backend.domain.research.dataset import DatasetRecord
from backend.domain.research.label import LabelDefinition, LabeledSample, LabelKind

logger = logging.getLogger(__name__)


class LabelEngine:
    """Generate labeled training samples from a versioned dataset."""

    def __init__(self, store: DatasetStore) -> None:
        self._store = store

    def generate(
        self,
        *,
        dataset_id: str,
        version: int,
        definition: LabelDefinition,
        price_field: str = "price",
        sample_every: int = 1,
    ) -> list[LabeledSample]:
        """Generate labeled samples for ``dataset_id`` version ``version``.

        Each sample sits at a record's *source time* — the market moment of
        decision. Its features are computed only from records whose source time
        precedes that moment, and its label is computed strictly from the
        ``horizon`` records that follow it. This is the causal structure that
        makes a label trainable without leakage.

        Parameters
        ----------
        dataset_id, version:
            Which frozen dataset snapshot to label.
        definition:
            The declarative label spec (must be chosen before training).
        price_field:
            Payload key holding the price used for feature/label math.
        sample_every:
            Label every Nth record (stride) to thin correlated samples.
        """
        if sample_every < 1:
            raise ValueError("sample_every must be a positive integer")

        records = self._store.load_records(dataset_id, version)
        if not records:
            raise ValueError(f"dataset {dataset_id} v{version} has no records")

        prices = [_extract_price(r, price_field) for r in records]
        if any(p is None for p in prices):
            raise ValueError(f"records are missing numeric '{price_field}'")

        samples: list[LabeledSample] = []
        for i in range(0, len(records), sample_every):
            decision = records[i]
            # Features use only records known before the decision moment.
            features = self._features_prior_to(
                records, cutoff=decision.source_timestamp, price_field=price_field
            )
            label = self._label_for(
                records=records,
                start_index=i,
                prices=prices,  # type: ignore[arg-type]
                definition=definition,
            )
            if label is None:
                continue
            label_start, label_value, label_end = label
            samples.append(
                LabeledSample(
                    decision_time=decision.source_timestamp,
                    label=label_value,
                    label_start=label_start,
                    label_end=label_end,
                    features=features,
                    label_definition=definition,
                    sample_index=i,
                    metadata={
                        "symbol": decision.payload.get("symbol", ""),
                        "dataset_id": dataset_id,
                        "dataset_version": version,
                    },
                )
            )
        return samples

    # -- internals -----------------------------------------------------------

    def _features_prior_to(
        self,
        records: Sequence[DatasetRecord],
        *,
        cutoff: datetime,
        price_field: str,
    ) -> dict[str, Any]:
        """Build a small point-in-time feature snapshot for ``cutoff``.

        Uses only records with source time at or before ``cutoff`` (the past).
        Returns the last known price, the number of prior records, and the
        record-to-record return against the previous price. This is
        intentionally minimal: the full feature framework (P1-004) replaces it
        with registered features.
        """
        prior = [r for r in records if r.source_timestamp <= cutoff]
        if not prior:
            return {"price": None, "n_prior": 0, "return_1": None}
        last = _extract_price(prior[-1], price_field)
        prev = _extract_price(prior[-2], price_field) if len(prior) >= 2 else None
        ret = (last - prev) / prev if (last is not None and prev) else None
        return {"price": last, "n_prior": len(prior), "return_1": ret}

    def _label_for(
        self,
        *,
        records: Sequence[DatasetRecord],
        start_index: int,
        prices: list[float],
        definition: LabelDefinition,
    ) -> tuple[datetime, float, datetime] | None:
        """Compute the forward-looking label window for sample ``start_index``.

        Returns ``(label_start, value, label_end)`` or None when the window
        cannot be resolved (insufficient future data).
        """
        if definition.kind is LabelKind.FIXED_HORIZON:
            return self._fixed_horizon_label(records, start_index, prices, definition)
        if definition.kind in (LabelKind.TRIPLE_BARRIER, LabelKind.META):
            return self._triple_barrier_label(records, start_index, prices, definition)
        raise ValueError(f"unsupported label kind: {definition.kind}")

    def _fixed_horizon_label(
        self,
        records: Sequence[DatasetRecord],
        start_index: int,
        prices: list[float],
        definition: LabelDefinition,
    ) -> tuple[datetime, float, datetime] | None:
        horizon = definition.horizon
        if start_index + horizon >= len(prices):
            return None
        entry = prices[start_index]
        exit_price = prices[start_index + horizon]
        value = 1.0 if exit_price > entry else -1.0 if exit_price < entry else 0.0
        return (
            records[start_index].source_timestamp,
            value,
            records[start_index + horizon].source_timestamp,
        )

    def _triple_barrier_label(
        self,
        records: Sequence[DatasetRecord],
        start_index: int,
        prices: list[float],
        definition: LabelDefinition,
    ) -> tuple[datetime, float, datetime] | None:
        horizon = definition.horizon
        lookback = definition.volatility_lookback
        if start_index + horizon >= len(prices):
            return None

        window = prices[start_index : start_index + horizon + 1]

        # Volatility anchor from the trailing lookback window.
        trailing = prices[max(0, start_index - lookback) : start_index]
        vol = _std(trailing) if len(trailing) >= 2 else 0.0

        config = TripleBarrierConfig(
            profit_multiple=definition.profit_multiple,
            loss_multiple=definition.loss_multiple,
            volatility=vol,
            profit_distance=definition.profit_distance,
            loss_distance=definition.loss_distance,
            max_steps=horizon,
        )
        if config.volatility == 0.0 and definition.profit_distance <= 0.0:
            # No volatility anchor and no absolute fallback: the barrier
            # distance is undefined, so the window cannot be labeled.
            return None
        label = label_triple_barrier(window, config)
        if label is None:
            return None
        return (
            records[start_index].source_timestamp,
            label.outcome,
            records[start_index + label.exit_step].source_timestamp,
        )


def _extract_price(record: DatasetRecord, price_field: str) -> float | None:
    value = record.payload.get(price_field)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    total: float = 0.0
    for value in values:
        deviation = value - mean
        total += deviation * deviation
    variance: float = total / len(values)
    return math.sqrt(variance)
