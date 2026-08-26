"""Tests for historical alternative-data storage (P1-006).

The store must guarantee:

1. Sentiment, SEC/proxy events are stored by their publication (availability)
   timestamp — backtests query by cutoff.
2. Backtests can never use 'current' cache values: there is no read that
   returns data without a cutoff, and events published after a cutoff are
   structurally invisible before it.
3. Events are immutable and clock-consistent.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.research.alt_data_service import AltDataService
from backend.domain.research.alt_data import AltDataEvent, AltDataKind
from backend.infrastructure.sqlite.alt_data_repository import SqliteAltDataRepository
from backend.infrastructure.sqlite.database import Database

T0 = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def article(
    event_id: str,
    *,
    published_at,
    event_timestamp=None,
    symbol="BTC",
    kind=AltDataKind.SENTIMENT,
    score=0.5,
):
    return AltDataEvent(
        event_id=event_id,
        symbol=symbol,
        kind=kind,
        event_timestamp=event_timestamp or published_at,
        published_at=published_at,
        payload={"sentiment_score": score, "title": event_id},
    )


@pytest.fixture
def service(tmp_path) -> AltDataService:
    store = SqliteAltDataRepository(Database(tmp_path / "alt.db"))
    return AltDataService(store)


def franchise(service: AltDataService, on: datetime):
    """Store six events: two sentiment, two SEC items, two proxy items."""
    service.record(article("s1", published_at=on + timedelta(hours=1), score=0.3))
    service.record(article("s2", published_at=on + timedelta(hours=3), score=0.7))
    service.record(
        article(
            "ins1",
            published_at=on + timedelta(hours=2),
            kind=AltDataKind.SEC_INSIDER,
            score=-0.4,
        )
    )
    service.record(
        article(
            "ins2",
            published_at=on + timedelta(hours=5),
            kind=AltDataKind.SEC_INSIDER,
            score=-0.9,
        )
    )
    service.record(
        article("p1", published_at=on + timedelta(hours=4), kind=AltDataKind.PROXY, score=0.1)
    )
    service.record(
        article("p2", published_at=on + timedelta(hours=6), kind=AltDataKind.PROXY, score=-0.2)
    )


class TestPublicationClock:
    def test_snapshot_uses_published_at_cutoff(self, service):
        franchise(service, T0)
        # At T0 + 2h exactly, s1, ins1 are public; s2 (3h), p1(4h), etc. are not.
        state = service.state_at(T0 + timedelta(hours=2))
        ids = {e.event_id for e in state.events}
        assert ids == {"s1", "ins1"}

    def test_cutoff_is_inclusive(self, service):
        franchise(service, T0)
        state = service.state_at(T0 + timedelta(hours=3))
        assert {e.event_id for e in state.events} == {"s1", "ins1", "s2"}

    def test_before_any_publication_is_empty(self, service):
        franchise(service, T0)
        state = service.state_at(T0)
        assert state.events == ()

    def test_future_cutoff_sees_everything(self, service):
        franchise(service, T0)
        state = service.state_at(T0 + timedelta(hours=99))
        assert len(state.events) == 6


class TestNoCurrentValueRead:
    def test_surface_is_cutoff_only(self):
        # The service/port surface exposes only state_at(cutoff): no 'latest
        # now' accessor exists that a backtest could accidentally call.
        public = dir(AltDataService)
        for name in ("state_at", "record", "event_count"):
            assert name in public
        for forbidden in ("latest_now", "current", "latest"):
            assert forbidden not in public

    def test_article_after_cutoff_is_invisible(self, service):
        service.record(article("late", published_at=T0 + timedelta(days=1), score=1.0))
        state_before = service.state_at(T0)
        assert state_before.for_symbol("BTC") == []
        state_after = service.state_at(T0 + timedelta(days=1))
        assert [e.event_id for e in state_after.for_symbol("BTC")] == ["late"]

    def test_last_known_value_by_cutoff(self, service):
        franchise(service, T0)
        # At T0+3h the latest sentiment *known* is s2 (0.7), never s1.
        state = service.state_at(T0 + timedelta(hours=3))
        latest = state.latest[("BTC", AltDataKind.SENTIMENT)]
        assert latest.event_id == "s2"

    def test_latest_never_sees_future_event(self, service):
        franchise(service, T0)
        service.record(article("zorr", published_at=T0 + timedelta(hours=48), score=999.0))
        state = service.state_at(T0 + timedelta(hours=6))
        assert "zorr" not in {e.event_id for e in state.events}
        assert state.latest.get(("BTC", AltDataKind.SENTIMENT)).event_id == "s2"


class TestKindsAndSymbols:
    def test_filter_by_kind(self, service):
        franchise(service, T0)
        state = service.state_at(T0 + timedelta(hours=6), kind=AltDataKind.SEC_INSIDER)
        assert {e.event_id for e in state.events} == {"ins1", "ins2"}

    def test_filter_by_symbol(self, service):
        franchise(service, T0)
        service.record(article("eth1", published_at=T0 + timedelta(hours=1), symbol="ETH"))
        state = service.state_at(T0 + timedelta(hours=6), symbol="ETH")
        assert [e.event_id for e in state.events] == ["eth1"]
        state_btc = service.state_at(T0 + timedelta(hours=6), symbol="ETH")
        assert len(state_btc.events) == 1

    def test_symbols_normalised(self, service):
        service.record(article("low", published_at=T0, symbol="btc"))
        state = service.state_at(T0, symbol="BTC")
        assert [e.event_id for e in state.events] == ["low"]


class TestImmutabilityAndClock:
    def test_duplicate_event_id_rejected(self, service):
        service.record(article("dup", published_at=T0))
        with pytest.raises(ValueError):
            service.record(article("dup", published_at=T0, score=99.0))
        assert service.event_count() == 1

    def test_cannot_publish_before_event_time(self, service):
        with pytest.raises(ValueError):
            service.record(article("bad", published_at=T0, event_timestamp=T0 + timedelta(hours=9)))

    def test_dict_round_trip(self):
        event = article("rt", published_at=T0)
        assert AltDataEvent.from_dict(event.as_dict()) == event

    def test_event_count(self, service):
        franchise(service, T0)
        assert service.event_count() == 6
