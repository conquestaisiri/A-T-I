"""Unit tests for the platform supervisor service."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.interfaces.supervisor import (
    SupervisorDecision,
    SupervisorStatus,
)
from backend.application.supervisor.supervisor_service import SupervisorService


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


class TestSupervisorService:
    def test_healthy_with_no_observations(self):
        decision: SupervisorDecision = SupervisorService().check(now=ts())
        assert decision.status is SupervisorStatus.HEALTHY
        assert decision.may_trade
        assert decision.stale_symbols == ()

    def test_fresh_observation_is_healthy(self):
        supervisor = SupervisorService(max_data_age_seconds=300.0)
        supervisor.record_observation("btcusdt", ts())
        decision = supervisor.check(now=ts() + timedelta(seconds=120))
        assert decision.status is SupervisorStatus.HEALTHY

    def test_stale_observation_degrades(self):
        supervisor = SupervisorService(max_data_age_seconds=300.0)
        supervisor.record_observation("btcusdt", ts())
        decision = supervisor.check(now=ts() + timedelta(seconds=301))
        assert decision.status is SupervisorStatus.DEGRADED
        assert not decision.may_trade
        assert decision.stale_symbols == ("btcusdt",)

    def test_kill_switch_halts_platform(self):
        supervisor = SupervisorService()
        supervisor.engage_kill_switch("manual review", now=ts())
        decision = supervisor.check(now=ts())
        assert decision.status is SupervisorStatus.HALTED
        assert not decision.may_trade
        assert supervisor.kill_switch_engaged

    def test_kill_switch_takes_precedence_over_stale(self):
        supervisor = SupervisorService(max_data_age_seconds=1.0)
        supervisor.record_observation("btcusdt", ts() - timedelta(days=1))
        supervisor.engage_kill_switch("manual review")
        decision = supervisor.check(now=ts())
        assert decision.status is SupervisorStatus.HALTED

    def test_release_kill_switch_restores_health(self):
        supervisor = SupervisorService()
        supervisor.engage_kill_switch("manual review")
        supervisor.release_kill_switch()
        assert not supervisor.kill_switch_engaged
        assert supervisor.check(now=ts()).status is SupervisorStatus.HEALTHY

    def test_requires_positive_max_age(self):
        with pytest.raises(ValueError):
            SupervisorService(max_data_age_seconds=0)

    def test_requires_non_empty_kill_reason(self):
        supervisor = SupervisorService()
        with pytest.raises(ValueError):
            supervisor.engage_kill_switch("")

    def test_requires_non_empty_symbol(self):
        supervisor = SupervisorService()
        with pytest.raises(ValueError):
            supervisor.record_observation("", ts())
