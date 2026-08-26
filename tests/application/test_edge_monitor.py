"""Unit tests for the strategy edge-monitoring service (task T2-15-1)."""

from __future__ import annotations

import pytest
from backend.application.research.edge_monitor import (
    EdgeMonitorService,
    build_edge_monitor_service,
    environment_for_status,
)
from backend.application.validation.adwin import AdwinConfig
from backend.domain.research.edge_monitor import (
    EdgeDemotionTrigger,
    EdgeMonitorConfig,
    EdgeMonitorState,
    EdgeVerdict,
)
from backend.domain.research.promotion import ModelEnvironment

HEALTHY_STREAM: tuple[float, ...] = (1.0,) * 500


def _state(service: EdgeMonitorService, passport_id: str) -> EdgeMonitorState:
    state = service.state(passport_id)
    assert state is not None
    return state


def _service(
    *,
    min_observations: int = 30,
    decay_mean_pct: float = 0.0,
    cooldown_observations: int = 0,
    adwin_delta: float = 0.002,
) -> EdgeMonitorService:
    return EdgeMonitorService(
        EdgeMonitorConfig(
            adwin=AdwinConfig(delta=adwin_delta, min_window=10),
            min_observations=min_observations,
            decay_mean_pct=decay_mean_pct,
            cooldown_observations=cooldown_observations,
        )
    )


def _record_all(service: EdgeMonitorService, passport_id: str, values: tuple[float, ...]) -> None:
    for value in values:
        service.record(passport_id, value)


class TestEdgeMonitorConfig:
    def test_rejects_zero_min_observations(self) -> None:
        with pytest.raises(ValueError):
            EdgeMonitorConfig(min_observations=0)

    def test_rejects_negative_cooldown(self) -> None:
        with pytest.raises(ValueError):
            EdgeMonitorConfig(cooldown_observations=-1)

    def test_defaults_are_sane(self) -> None:
        config = EdgeMonitorConfig()
        assert config.min_observations == 30
        assert config.decay_mean_pct == 0.0
        assert config.cooldown_observations == 0


class TestEdgeMonitorServiceVerdicts:
    def test_insufficient_below_min_observations(self) -> None:
        service = _service(min_observations=50)
        state = service.record("p1", -10.0)
        assert state.verdict is EdgeVerdict.INSUFFICIENT
        state = service.record("p1", -10.0)
        assert state.verdict is EdgeVerdict.INSUFFICIENT
        assert state.observations == 2

    def test_insufficient_even_with_extreme_loss(self) -> None:
        service = _service()
        for _ in range(29):
            service.record("p1", -10.0)
        assert _state(service, "p1").verdict is EdgeVerdict.INSUFFICIENT

    def test_stable_positive_stream_is_healthy(self) -> None:
        service = _service()
        _record_all(service, "p1", HEALTHY_STREAM)
        state = service.state("p1")
        assert state is not None
        assert state.verdict is EdgeVerdict.HEALTHY
        assert state.mean == pytest.approx(1.0)
        assert state.cuts == 0
        assert state.drifted is False

    def test_step_change_up_is_watching_not_decayed(self) -> None:
        service = _service()
        _record_all(service, "p1", (0.0,) * 500)
        saw_cut = False
        for _ in range(500):
            state = service.record("p1", 5.0)
            if state.drifted:
                saw_cut = True
                assert state.verdict is EdgeVerdict.WATCHING  # change, edge intact
            if state.verdict is EdgeVerdict.HEALTHY and state.mean > 1.0:
                break
        assert saw_cut is True
        assert _state(service, "p1").verdict is EdgeVerdict.HEALTHY  # stable again

    def test_step_change_down_is_decayed(self) -> None:
        service = _service()
        _record_all(service, "p1", (1.0,) * 500)
        saw_decay = False
        for _ in range(500):
            state = service.record("p1", -1.0)
            if state.verdict is EdgeVerdict.DECAYED:
                saw_decay = True
                assert state.drifted is True
                assert state.mean < 0.0
                break
        assert saw_decay is True

    def test_window_mean_below_threshold_without_cut_is_watching(self) -> None:
        service = _service(decay_mean_pct=1.0)
        _record_all(service, "p1", (0.5,) * 500)
        state = _state(service, "p1")
        assert state.verdict is EdgeVerdict.WATCHING  # edge paying, but below threshold
        assert state.cuts == 0

    def test_threshold_exactly_zero_is_not_decayed(self) -> None:
        service = _service()
        _record_all(service, "p1", (0.0,) * 500)
        state = _state(service, "p1")
        assert state.verdict is EdgeVerdict.HEALTHY  # mean == threshold counts as paying

    def test_unknown_passport_has_no_state(self) -> None:
        service = _service()
        assert service.state("never-seen") is None

    def test_nan_observation_is_skipped_by_detector(self) -> None:
        service = _service()
        _record_all(service, "p1", (0.5,) * 200)
        state = service.record("p1", float("nan"))
        assert state.verdict is EdgeVerdict.HEALTHY  # window unchanged, still positive
        assert state.observations == 201

    def test_non_numeric_return_is_rejected(self) -> None:
        service = _service()
        with pytest.raises(TypeError):
            service.record("p1", "one percent")  # type: ignore[arg-type]

    def test_reset_forgets_a_passport(self) -> None:
        service = _service()
        _record_all(service, "p1", HEALTHY_STREAM)
        assert service.state("p1") is not None
        service.reset("p1")
        assert service.state("p1") is None
        state = service.record("p1", 1.0)
        assert state.observations == 1  # counter restarted

    def test_observations_are_monotonic_per_passport(self) -> None:
        service = _service()
        service.record("p1", 1.0)
        service.record("p1", 1.0)
        service.record("p2", 1.0)
        assert service.record("p1", 1.0).observations == 3
        assert _state(service, "p2").observations == 1


class TestCooldown:
    def test_cooldown_suppresses_repeated_decay_trigger(self) -> None:
        quiet = _service(cooldown_observations=200)
        loud = _service(cooldown_observations=0)
        _record_all(quiet, "p1", (1.0,) * 200)
        _record_all(loud, "p1", (1.0,) * 200)
        _record_all(quiet, "p1", (-1.0,) * 200)
        _record_all(loud, "p1", (-1.0,) * 200)
        first = _state(quiet, "p1")
        assert first.verdict is EdgeVerdict.DECAYED
        first_decayed_at = first.last_decayed_at
        assert first_decayed_at is not None
        assert quiet.demotion_trigger("p1", status="paper").triggered is True

        # A second regime change to -2.0 re-cuts; the cooldown holds the
        # advisory while the no-cooldown twin re-fires immediately.
        for _ in range(200):
            state = quiet.record("p1", -2.0)
            loud.record("p1", -2.0)
            if state.drifted:
                break
        else:
            pytest.fail("expected a second ADWIN cut on the -2.0 stream")
        assert _state(quiet, "p1").verdict is EdgeVerdict.DECAYED  # decay persists
        assert _state(quiet, "p1").last_decayed_at != first_decayed_at  # new event
        assert quiet.demotion_trigger("p1", status="paper").triggered is False  # cooldown
        assert loud.demotion_trigger("p1", status="paper").triggered is True

    def test_cooldown_rearms_after_window(self) -> None:
        service = _service(cooldown_observations=5)
        _record_all(service, "p1", (1.0,) * 200)
        _record_all(service, "p1", (-1.0,) * 200)
        _record_all(service, "p1", (-2.0,) * 300)
        assert _state(service, "p1").verdict is EdgeVerdict.DECAYED  # long after cooldown
        assert service.demotion_trigger("p1", status="paper").triggered is True

    def test_decayed_persists_until_recovery(self) -> None:
        service = _service()
        _record_all(service, "p1", (1.0,) * 500)
        _record_all(service, "p1", (-1.0,) * 500)
        assert _state(service, "p1").verdict is EdgeVerdict.DECAYED
        _record_all(service, "p1", (1.0,) * 500)
        state = _state(service, "p1")
        assert state.verdict is EdgeVerdict.HEALTHY  # recovered, edge paying again
        assert state.last_decayed_at is not None  # history preserved


class TestDemotionTrigger:
    def _decayed_service(self) -> EdgeMonitorService:
        service = _service()
        _record_all(service, "p1", (1.0,) * 500)
        _record_all(service, "p1", (-1.0,) * 500)
        assert _state(service, "p1").verdict is EdgeVerdict.DECAYED
        return service

    def test_no_trigger_without_decay(self) -> None:
        service = _service()
        _record_all(service, "p1", HEALTHY_STREAM)
        trigger = service.demotion_trigger("p1", status="paper")
        assert trigger.triggered is False
        assert trigger.recommended_environment is None

    def test_no_trigger_when_insufficient(self) -> None:
        service = _service()
        service.record("p1", 1.0)
        trigger = service.demotion_trigger("p1", status="paper")
        assert trigger.triggered is False

    def test_no_trigger_for_unknown_passport(self) -> None:
        service = _service()
        trigger = service.demotion_trigger("nope", status="paper")
        assert trigger.triggered is False

    def test_trigger_recommends_previous_environment(self) -> None:
        service = self._decayed_service()
        trigger = service.demotion_trigger("p1", status="paper")
        assert trigger.triggered is True
        assert trigger.recommended_environment == "validation"
        assert "decay" in trigger.reason.lower()

    def test_trigger_maps_live_to_canary(self) -> None:
        service = self._decayed_service()
        trigger = service.demotion_trigger("p1", status="live")
        assert trigger.triggered is True
        assert trigger.recommended_environment == "canary"

    def test_trigger_without_status_has_no_recommendation(self) -> None:
        service = self._decayed_service()
        trigger = service.demotion_trigger("p1")
        assert trigger.triggered is True
        assert trigger.recommended_environment is None

    def test_trigger_unknown_status_has_no_recommendation(self) -> None:
        service = self._decayed_service()
        trigger = service.demotion_trigger("p1", status="retired")
        assert trigger.triggered is True
        assert trigger.recommended_environment is None


class TestSerialisation:
    def test_state_as_dict(self) -> None:
        service = _service()
        _record_all(service, "p1", (0.5,) * 60)
        payload = _state(service, "p1").as_dict()
        assert payload["passport_id"] == "p1"
        assert payload["observations"] == 60
        assert payload["verdict"] == "healthy"
        assert payload["drifted"] is False
        assert payload["last_cut_at"] is None
        assert payload["mean"] == pytest.approx(0.5)

    def test_trigger_as_dict(self) -> None:
        trigger = EdgeDemotionTrigger(
            passport_id="p1",
            triggered=True,
            reason="decay",
            recommended_environment="validation",
        )
        payload = trigger.as_dict()
        assert payload == {
            "passport_id": "p1",
            "triggered": True,
            "reason": "decay",
            "recommended_environment": "validation",
        }


class TestEnvironmentMapping:
    def test_maps_known_statuses(self) -> None:
        assert environment_for_status("research") is ModelEnvironment.RESEARCH
        assert environment_for_status("candidate") is ModelEnvironment.VALIDATION
        assert environment_for_status("paper") is ModelEnvironment.PAPER
        assert environment_for_status("canary") is ModelEnvironment.CANARY
        assert environment_for_status("live") is ModelEnvironment.PRODUCTION

    def test_none_and_unknown_statuses_map_to_none(self) -> None:
        assert environment_for_status(None) is None
        assert environment_for_status("retired") is None
        assert environment_for_status("bogus") is None


class TestBootstrap:
    def test_build_seam_returns_service(self) -> None:
        service = build_edge_monitor_service()
        assert isinstance(service, EdgeMonitorService)
