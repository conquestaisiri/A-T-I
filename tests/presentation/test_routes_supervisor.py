"""Tests for the platform supervisor API routes."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from backend.application.supervisor.supervisor_service import SupervisorService
from backend.infrastructure.sqlite.database import Database
from backend.presentation.api.routes_supervisor import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path) -> Iterator[TestClient]:
    database = Database(tmp_path / "supervisor-api.db")
    app = FastAPI()
    app.include_router(router)
    app.state.supervisor = SupervisorService()

    with TestClient(app) as test_client:
        yield test_client

    database.close()


class TestSupervisorAPI:
    def test_status_healthy_when_clean(self, client):
        response = client.get("/v1/supervisor/status")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "healthy"
        assert payload["kill_switch_engaged"] is False

    def test_status_halts_after_kill(self, client):
        response = client.post("/v1/supervisor/kill", json={"reason": "manual review"})
        assert response.status_code == 200
        assert response.json()["status"] == "halted"

        status = client.get("/v1/supervisor/status").json()
        assert status["status"] == "halted"
        assert status["kill_switch_engaged"] is True

    def test_kill_requires_non_empty_reason(self, client):
        response = client.post("/v1/supervisor/kill", json={"reason": " "})
        assert response.status_code == 422

    def test_release_restores_health(self, client):
        client.post("/v1/supervisor/kill", json={"reason": "manual review"})
        response = client.post("/v1/supervisor/release")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_supervisor_missing_is_503(self, tmp_path):
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            response = client.get("/v1/supervisor/status")
            assert response.status_code == 503
