# backend/domain/research/label.py
"""Label definitions and labeled samples (task P1-002).

Labels are the research contract that makes a model trainable *honestly*:

- **Defined before training**: a :class:`LabelDefinition` is a declarative,
  immutable specification chosen by the researcher before any model sees data.
  The engine refuses to produce labels without one.
- **Explicit forward-looking window**: every label carries the source-time
  interval ``[label_start, label_end]`` over which it was computed. This is
  exactly the interval purged cross-validation needs to purge leakage, and it
  is never guessed: it is recorded on every sample.
- **Label timestamp recorded**: each sample carries ``decision_time`` (the
  point-in-time at which the sample's features were knowable) and the label
  outcome. A model trained on these samples can never leak the label into its
  features because features only use records available at ``decision_time``.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from backend.domain.research.dataset import DatasetKind, DatasetRecord


class LabelKind(enum.StrEnum):
    """Supported label types. New label families extend this enum."""

    TRIPLE_BARRIER = "triple_barrier"
    META = "meta"
    FIXED_HORIZON = "fixed_horizon"


@dataclass(frozen=True, slots=True)
class LabelDefinition:
    """Declarative, immutable label specification.

    Parameters
    ----------
    kind: LabelKind
        Which labeling scheme to apply.
    horizon: int
        Forward-looking horizon, in records, of the label window. Must be
        positive; the forward window is explicit by construction.
    volatility_lookback: int
        Number of past records used to estimate volatility for barrier
        distances (triple-barrier only). Must be >= 1.
    profit_multiple: float
        Upper-barrier distance as a multiple of volatility (triple-barrier).
    loss_multiple: float
        Lower-barrier distance as a multiple of volatility (triple-barrier).
    profit_distance: float
        Absolute upper-barrier distance in price units, used when trailing
        volatility is zero (triple-barrier fallback; default 0 = no fallback).
    loss_distance: float
        Absolute lower-barrier distance in price units, used when trailing
        volatility is zero (triple-barrier fallback; default 0 = no fallback).
    name: str
        Human-readable label name recorded on every sample (default: kind).
    """

    kind: LabelKind
    horizon: int
    volatility_lookback: int = 20
    profit_multiple: float = 2.0
    loss_multiple: float = 2.0
    profit_distance: float = 0.0
    loss_distance: float = 0.0
    name: str = ""

    def __post_init__(self) -> None:
        if self.horizon <= 0:
            raise ValueError("horizon must be a positive integer")
        if self.kind is LabelKind.TRIPLE_BARRIER and self.volatility_lookback < 1:
            raise ValueError("volatility_lookback must be >= 1")
        if self.profit_multiple <= 0.0 or self.loss_multiple <= 0.0:
            raise ValueError("barrier multiples must be positive")
        if self.profit_distance < 0.0 or self.loss_distance < 0.0:
            raise ValueError("barrier distances cannot be negative")
        if not self.name:
            object.__setattr__(self, "name", self.kind.value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "horizon": self.horizon,
            "volatility_lookback": self.volatility_lookback,
            "profit_multiple": self.profit_multiple,
            "loss_multiple": self.loss_multiple,
            "profit_distance": self.profit_distance,
            "loss_distance": self.loss_distance,
            "name": self.name,
        }


@dataclass(frozen=True, slots=True)
class LabeledSample:
    """One training sample: point-in-time features + forward-looking label.

    Parameters
    ----------
    decision_time: datetime
        The point-in-time at which this sample's features were knowable.
        Features must use only records with ``available_at <= decision_time``.
    label: float
        The outcome: +1/-1 for a directional label, 0 for neutral/vertical,
        or 1/0 for a meta-label bet.
    label_start: datetime
        Earliest source timestamp the label was computed from (exclusive of
        the decision time).
    label_end: datetime
        Latest source timestamp the label was computed from.
    features: Mapping[str, Any]
        The feature vector, computed from data available at ``decision_time``.
    label_definition: LabelDefinition
        The exact definition used (recorded so results are reproducible).
    sample_index: int
        Position of this sample in the source dataset.
    metadata: Mapping[str, Any]
        Optional extras (symbol, model id, etc.).
    """

    decision_time: datetime
    label: float
    label_start: datetime
    label_end: datetime
    features: Mapping[str, Any]
    label_definition: LabelDefinition
    sample_index: int = 0
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dataset_record(self, dataset_id: str) -> DatasetRecord:
        """Encode this sample as a NORMALIZED dataset record.

        The record's ``source_timestamp`` is the sample's decision time and its
        ``available_at`` is identical, so the labeled dataset composes with the
        versioned store (P1-001) and the point-in-time query. The label and its
        explicit window are stored in the payload.
        """
        return DatasetRecord(
            dataset_id=dataset_id,
            source_timestamp=self.decision_time,
            available_at=self.decision_time,
            payload={
                "features": dict(self.features),
                "label": self.label,
                "label_start": self.label_start.isoformat(timespec="milliseconds"),
                "label_end": self.label_end.isoformat(timespec="milliseconds"),
                "label_definition": self.label_definition.as_dict(),
                "sample_index": self.sample_index,
                **dict(self.metadata),
            },
            kind=DatasetKind.NORMALIZED,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision_time": self.decision_time.isoformat(timespec="milliseconds"),
            "label": self.label,
            "label_start": self.label_start.isoformat(timespec="milliseconds"),
            "label_end": self.label_end.isoformat(timespec="milliseconds"),
            "features": dict(self.features),
            "label_definition": self.label_definition.as_dict(),
            "sample_index": self.sample_index,
            "metadata": dict(self.metadata),
        }
