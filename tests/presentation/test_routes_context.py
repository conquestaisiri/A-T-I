"""Tests for the observability API routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.infrastructure.config.settings import settings
from backend.infrastructure.sqlite.context_repository import SqliteContextRepository
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.observation_repository import SqliteObservationRepository
from backend.presentation.api.routes_context import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def make_trade(symbol: str, trade_id: int, ts: datetime) -> ObservationEvent:
    return ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts,
        payload={
            "symbol": symbol,
            "trade_id": trade_id,
            "price": 100.0,
            "quantity": 1.0,
        },
    )


def make_context(symbol: str, ts: datetime) -> MarketContext:
    events = (make_trade(symbol, 1, ts),)
    snapshot = ContextSnapshot.from_events(events)
    feature = ContextFeature(name="trend", value="up", computation_timestamp=ts, execution_time=0.1)
    return MarketContext(snapshot=snapshot, features=(("trend", feature),), created_at=ts)


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database = Database(tmp_path / "api.db")
    observation_repo = SqliteObservationRepository(database)
    context_repo = SqliteContextRepository(database)

    app = FastAPI()
    app.include_router(router)
    app.state.observation_repository = observation_repo
    app.state.context_repository = context_repo

    with TestClient(app) as test_client:
        yield test_client

    database.close()


class TestObservabilityAPI:
    def test_context_latest_returns_404_when_empty(self, client):
        response = client.get("/v1/context/latest?symbol=btcusdt")
        assert response.status_code == 404

    def test_context_latest_returns_saved_context(self, client):
        client.app.state.context_repository.save(
            make_context("btcusdt", datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
        )

        response = client.get("/v1/context/latest?symbol=btcusdt")
        assert response.status_code == 200
        payload = response.json()
        assert payload["snapshot"]["events"][0]["payload"]["symbol"] == "btcusdt"
        assert payload["features"]["trend"]["value"] == "up"

    def test_context_latest_lowercases_symbol(self, client):
        client.app.state.context_repository.save(
            make_context("btcusdt", datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
        )

        response = client.get("/v1/context/latest?symbol=BTCUSDT")
        assert response.status_code == 200
        assert response.json()["features"]["trend"]["value"] == "up"

    def test_context_latest_rejects_empty_symbol(self, client):
        response = client.get("/v1/context/latest?symbol=")
        assert response.status_code == 422

    def test_context_history_returns_saved_contexts_oldest_first(self, client):
        repo = client.app.state.context_repository
        base = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        repo.save(make_context("btcusdt", base))
        repo.save(make_context("btcusdt", base))

        response = client.get("/v1/context/history?symbol=btcusdt&limit=5")
        assert response.status_code == 200
        payload = response.json()
        assert payload["symbol"] == "btcusdt"
        assert len(payload["contexts"]) == 2

    def test_events_recent_returns_saved_events(self, client):
        repo = client.app.state.observation_repository
        repo.save(make_trade("btcusdt", 1, datetime(2026, 1, 15, 12, 0, tzinfo=UTC)))
        repo.save(make_trade("btcusdt", 2, datetime(2026, 1, 15, 12, 1, tzinfo=UTC)))

        response = client.get("/v1/events/recent?symbol=btcusdt")
        assert response.status_code == 200
        payload = response.json()
        assert [e["payload"]["trade_id"] for e in payload["events"]] == [1, 2]

    def test_events_recent_rejects_invalid_limit(self, client):
        response = client.get("/v1/events/recent?symbol=btcusdt&limit=0")
        assert response.status_code == 422

    def test_events_recent_bounds_excessive_limit(self, client):
        response = client.get("/v1/events/recent?symbol=btcusdt&limit=1000000")
        assert response.status_code == 200


class TestObservabilityAPIAuth:
    def test_requires_api_key_when_configured(self, client, monkeypatch):
        client.app.state.context_repository.save(
            make_context("btcusdt", datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
        )
        monkeypatch.setattr(settings, "api_key", "secret-key")
        try:
            response = client.get("/v1/context/latest?symbol=btcusdt")
            assert response.status_code == 401

            response = client.get(
                "/v1/context/latest?symbol=btcusdt",
                headers={"X-API-Key": "wrong"},
            )
            assert response.status_code == 403

            response = client.get(
                "/v1/context/latest?symbol=btcusdt",
                headers={"X-API-Key": "secret-key"},
            )
            assert response.status_code == 200
        finally:
            monkeypatch.setattr(settings, "api_key", None)

    def test_events_recent_requires_api_key_when_configured(self, client, monkeypatch):
        monkeypatch.setattr(settings, "api_key", "secret-key")
        try:
            response = client.get("/v1/events/recent?symbol=btcusdt")
            assert response.status_code == 401

            response = client.get(
                "/v1/events/recent?symbol=btcusdt",
                headers={"X-API-Key": "secret-key"},
            )
            assert response.status_code == 200
        finally:
            monkeypatch.setattr(settings, "api_key", None)
