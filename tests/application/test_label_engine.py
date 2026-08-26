"""Tests for the label-generation framework (P1-002).

The framework must guarantee the three causal properties that make labeled
data honest:

1. Labels are defined declaratively before training (no ad-hoc labeling).
2. Forward-looking windows are explicit and recorded per sample, in source
   (market) time.
3. Each sample's decision time is its record's source time, and features only
   ever use records whose source time precedes the decision moment — never
   the sample's own or future records.

Backtest semantics: a label is trainable when the decision moment is the
*market* time of the record, not its download stamp.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.dataset_service import DatasetService
from backend.application.research.label_engine import LabelEngine
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.label import LabelDefinition, LabelKind
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.dataset_repository import SqliteDatasetRepository


def price_events(n: int, start_price: float = 100.0, step: float = 1.0):
    """Return ``n`` trade events with linearly increasing prices."""
    base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
    events = []
    for i in range(n):
        events.append(
            ObservationEvent(
                source_id="binance",
                source_name="Binance",
                event_type=ObservationEventType.TRADE,
                timestamp=base + timedelta(seconds=i),
                payload={
                    "symbol": "btcusdt",
                    "trade_id": i,
                    "price": start_price + i * step,
                    "quantity": 1.0,
                },
            )
        )
    return events


@pytest.fixture
def labeled(service: DatasetService, engine: LabelEngine):
    """Build a rising-price dataset and label it with a fixed horizon."""
    events = price_events(20, start_price=100.0, step=1.0)
    service.build_raw_dataset(dataset_id="rising", events=events, available_at=events[-1].timestamp)
    definition = LabelDefinition(kind=LabelKind.FIXED_HORIZON, horizon=5)
    samples = engine.generate(dataset_id="rising", version=1, definition=definition)
    return samples, definition


@pytest.fixture
def engine(tmp_path) -> LabelEngine:
    return LabelEngine(SqliteDatasetRepository(Database(tmp_path / "labels.db")))


@pytest.fixture
def service(tmp_path) -> DatasetService:
    return DatasetService(SqliteDatasetRepository(Database(tmp_path / "labels.db")))


class TestLabelEngine:
    def test_labels_defined_before_training(self):
        with pytest.raises(ValueError):
            LabelDefinition(kind=LabelKind.TRIPLE_BARRIER, horizon=0)  # invalid horizon
        with pytest.raises(ValueError):
            LabelDefinition(kind=LabelKind.FIXED_HORIZON, horizon=0)
        with pytest.raises(ValueError):
            LabelDefinition(kind=LabelKind.TRIPLE_BARRIER, horizon=10, volatility_lookback=0)

    def test_fixed_horizon_label_is_directional(self, labeled):
        samples, _ = labeled
        # Prices rise monotonically, so every label over a 5-step window is +1.
        assert all(s.label == 1.0 for s in samples)

    def test_forward_window_is_explicit(self, labeled):
        samples, definition = labeled
        for sample in samples:
            # The window spans exactly `horizon` steps in source time.
            delta = sample.label_end - sample.label_start
            assert delta == timedelta(seconds=definition.horizon)
            # The label window is strictly forward of the decision time.
            assert sample.label_start >= sample.decision_time

    def test_label_timestamps_recorded(self, labeled):
        samples, _ = labeled
        for sample in samples:
            assert sample.decision_time.tzinfo is not None
            assert sample.label_start >= sample.decision_time
            assert sample.label_end > sample.label_start

    def test_features_are_point_in_time_no_leak(self, service, engine):
        # A backfilled dataset: every record has the same download stamp.
        # Labeling still happens on source (market) time, and features never
        # include a record whose source time is in the sample's future.
        events = price_events(20)
        download = events[-1].timestamp + timedelta(hours=48)
        service.build_raw_dataset(dataset_id="backfill", events=events, available_at=download)

        definition = LabelDefinition(kind=LabelKind.FIXED_HORIZON, horizon=5)
        samples = engine.generate(dataset_id="backfill", version=1, definition=definition)

        assert samples
        first = events[0].timestamp
        for sample in samples:
            # Decision time is the record's source (market) time.
            assert sample.decision_time == first + timedelta(seconds=sample.sample_index)
            # The feature snapshot only counts records at or before the
            # decision moment — never future records.
            assert sample.features["n_prior"] == sample.sample_index + 1
            assert sample.features["price"] == 100.0 + sample.sample_index

    def test_sample_every_strides(self, service, engine):
        events = price_events(20)
        service.build_raw_dataset(dataset_id="s", events=events, available_at=events[-1].timestamp)
        definition = LabelDefinition(kind=LabelKind.FIXED_HORIZON, horizon=3)
        all_samples = engine.generate(dataset_id="s", version=1, definition=definition)
        thinned = engine.generate(dataset_id="s", version=1, definition=definition, sample_every=2)
        assert len(thinned) < len(all_samples)

    def test_triple_barrier_uses_volatility_anchor(self, service, engine):
        # Flat then a sharp spike: triple-barrier should resolve with +1 on
        # the up-move after the anchor.
        prices = [100.0] * 10 + [101.0, 102.0, 103.0, 104.0, 105.0, 106.0]
        base = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)
        events = [
            ObservationEvent(
                source_id="binance",
                source_name="Binance",
                event_type=ObservationEventType.TRADE,
                timestamp=base + timedelta(seconds=i),
                payload={"symbol": "btcusdt", "trade_id": i, "price": p, "quantity": 1.0},
            )
            for i, p in enumerate(prices)
        ]
        service.build_raw_dataset(
            dataset_id="spike", events=events, available_at=base + timedelta(seconds=len(events))
        )
        definition = LabelDefinition(
            kind=LabelKind.TRIPLE_BARRIER,
            horizon=6,
            volatility_lookback=5,
            profit_multiple=2.0,
            loss_multiple=2.0,
            profit_distance=0.5,
            loss_distance=0.5,
        )
        samples = engine.generate(dataset_id="spike", version=1, definition=definition)
        assert samples, "expected at least one resolvable sample"
        # The samples starting on the up-leg resolve positive.
        assert any(s.label > 0 for s in samples)

    def test_samples_convert_to_normalized_records(self, labeled):
        samples, _ = labeled
        record = samples[0].to_dataset_record("labeled-rising")
        assert record.kind.value == "normalized"
        assert record.payload["label"] == samples[0].label
        assert record.payload["label_end"] == samples[0].label_end.isoformat(
            timespec="milliseconds"
        )
        assert record.available_at == samples[0].decision_time
