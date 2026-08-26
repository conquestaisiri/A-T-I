# tests/application/test_event_reactions.py
"""Event -> forward-return dataset: honest anchors, honest missing horizons."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.application.research.event_reactions import compute_event_reactions
from backend.domain.macro.event import MacroEventData
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository


def _tick(repo: SqliteObservationRepository, symbol: str, when: datetime, price: float) -> None:
    repo.save(
        ObservationEvent(
            source_id="test",
            source_name="Test",
            event_type=ObservationEventType.TRADE,
            timestamp=when,
            payload={"symbol": symbol, "trade_id": int(when.timestamp()), "price": price},
        )
    )


def _event(at: datetime) -> MacroEventData:
    return MacroEventData(
        event_id="evt-1",
        currency="USD",
        title="Core PCE Price Index m/m",
        scheduled_at=at,
        impact="High",
        forecast=0.2,
        previous=0.1,
        actual=0.4,
    )


def test_forward_returns_measured_from_anchor(tmp_path) -> None:
    database = Database(tmp_path / "react.db")
    repo = SqliteObservationRepository(database)
    release = datetime.now(UTC) - timedelta(hours=2)
    _tick(repo, "BTCUSDT", release + timedelta(seconds=5), 100.0)
    _tick(repo, "BTCUSDT", release + timedelta(minutes=1), 100.5)
    _tick(repo, "BTCUSDT", release + timedelta(minutes=5), 101.0)
    _tick(repo, "BTCUSDT", release + timedelta(minutes=15), 102.0)

    report = compute_event_reactions(
        database,
        _event(release),
        "btcusdt",
        horizons_minutes=(1, 5, 15, 1440),
        as_of=datetime.now(UTC),
    )

    assert report["complete"] is False  # 1d horizon has no future data yet
    reactions = report["reactions"]
    assert reactions["1m"] is not None and abs(reactions["1m"] - 0.5) < 1e-6
    assert reactions["5m"] is not None and abs(reactions["5m"] - 1.0) < 1e-6
    assert reactions["15m"] is not None and abs(reactions["15m"] - 2.0) < 1e-6
    assert reactions["1440m"] is None
    anchor = report["anchor"]
    assert anchor is not None and anchor["price"] == 100.0
    database.close()


def test_future_event_reports_honest_empty_reaction(tmp_path) -> None:
    database = Database(tmp_path / "react2.db")
    repo = SqliteObservationRepository(database)
    release = datetime.now(UTC) + timedelta(hours=3)
    _tick(repo, "BTCUSDT", datetime.now(UTC), 100.0)  # pre-release noise only

    report = compute_event_reactions(database, _event(release), "BTCUSDT")

    assert report["anchor"] is None
    assert report["complete"] is False
    assert all(v is None for v in report["reactions"].values())
    database.close()


def test_anchor_beyond_grace_window_is_rejected(tmp_path) -> None:
    database = Database(tmp_path / "react3.db")
    repo = SqliteObservationRepository(database)
    release = datetime.now(UTC) - timedelta(hours=1)
    # First print arrives 10 minutes late: drifted, not a valid anchor.
    _tick(repo, "BTCUSDT", release + timedelta(minutes=10), 100.0)

    report = compute_event_reactions(database, _event(release), "BTCUSDT")

    assert report["anchor"] is None
    database.close()
