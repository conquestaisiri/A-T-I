# tests/application/test_paper_autonomy.py
"""Tests for the long autonomous paper-campaign runner (task P4-004).

The runner is the sandbox apprenticeship for a candidate: it executes an
injected decision-loop day by day, measures drawdown / Sharpe / operational
health, applies the P4-001 stay limits (auto-retire), and earns canary
eligibility only through the promotion gate. Paper is the autonomous sandbox:
there is deliberately NO authorization flag — unlike the live-touch canary,
this path never moves money. ``eligible_for_canary`` is eligibility, not a
start.
"""

from __future__ import annotations

from typing import Any

from backend.application.research.paper_autonomy import (
    PaperAutonomyRunner,
    PaperCampaignConfig,
    run_paper_campaign,
)
from backend.domain.research.paper_campaign import (
    PaperCampaignAction,
    PaperCampaignResult,
    PaperDayAction,
    PaperDayOutcome,
)
from backend.domain.research.promotion import CandidateEvidence


def _validation_evidence(**overrides: Any) -> CandidateEvidence:
    """A candidate that passed validation but has never touched paper."""

    defaults: dict[str, Any] = {
        "candidate_id": "model-a",
        "validation_samples": 500,
        "validation_sharpe": 1.2,
    }
    defaults.update(overrides)
    return CandidateEvidence(**defaults)


def _good_day(day: int, expected: float = 0.05) -> PaperDayOutcome:
    """A profitable day with small variance (mean ~0.08%/day)."""

    ret = 0.10 if day % 2 else 0.06
    return PaperDayOutcome(
        day=day,
        return_pct=ret,
        expected_return_pct=expected,
        total_orders=10,
    )


class TestPaperIsTheAutonomousSandbox:
    def test_no_authorization_is_required_to_run(self) -> None:
        # Unlike the live-touch canary, a paper campaign needs no operator
        # authorization: it cannot move money by construction.
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=3)).run(
            "model-a", _validation_evidence(), _good_day
        )
        assert result.candidate_id == "model-a"

    def test_runs_the_full_window_when_clean(self) -> None:
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), _good_day
        )
        assert result.days_run == 30
        assert len(result.periods) == 30
        assert all(p.action is PaperDayAction.CONTINUE for p in result.periods)

    def test_convenience_function_wires_the_runner(self) -> None:
        result = run_paper_campaign(
            "model-a",
            _validation_evidence(),
            _good_day,
            config=PaperCampaignConfig(campaign_days=2),
        )
        assert isinstance(result, PaperCampaignResult)


class TestEarnedCanaryEligibility:
    def test_clean_long_clean_campaign_is_completed_advanced(self) -> None:
        # 30 clean days earns 30 paper days (>= 14) and a positive Sharpe
        # (>= 0.3), so the CANARY gate is granted.
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), _good_day
        )
        assert result.action is PaperCampaignAction.COMPLETED_ADVANCED
        assert result.eligible_for_canary is True
        assert result.sharpe > 0.0
        assert result.evidence.paper_days_deployed == 30

    def test_short_clean_campaign_holds(self) -> None:
        # 2 clean days does not meet paper_period_days_min=14 -> HOLD.
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=2)).run(
            "model-a", _validation_evidence(), _good_day
        )
        assert result.action is PaperCampaignAction.COMPLETED_HOLD
        assert result.eligible_for_canary is False
        assert result.days_run == 2
        assert "insufficient" in result.reason

    def test_prior_paper_days_accumulate_toward_the_gate(self) -> None:
        # Candidate already had 12 paper days; a 3-day run reaches 15 >= 14.
        evidence = _validation_evidence(paper_days_deployed=12, paper_sharpe=0.5)
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=3)).run(
            "model-a", evidence, _good_day
        )
        assert result.action is PaperCampaignAction.COMPLETED_ADVANCED
        assert result.evidence.paper_days_deployed == 15

    def test_clean_campaign_but_weak_validation_holds(self) -> None:
        evidence = _validation_evidence(validation_samples=5)
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", evidence, _good_day
        )
        assert result.action is PaperCampaignAction.COMPLETED_HOLD


class TestAutomaticRollback:
    def test_drawdown_breach_retires_early(self) -> None:
        def day_fn(day: int) -> PaperDayOutcome:
            return (
                _good_day(day)
                if day < 5
                else PaperDayOutcome(
                    day=day, return_pct=-25.0, expected_return_pct=0.05, total_orders=10
                )
            )

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), day_fn
        )
        assert result.action is PaperCampaignAction.RETIRED
        assert result.eligible_for_canary is False
        assert result.days_run == 5
        assert any(p.day == 5 and p.action is PaperDayAction.RETIRED for p in result.periods)
        assert any(p.action is PaperDayAction.CONTINUE for p in result.periods[:4])

    def test_retired_reason_records_the_breach(self) -> None:
        def day_fn(day: int) -> PaperDayOutcome:
            return PaperDayOutcome(
                day=day, return_pct=-25.0, expected_return_pct=0.05, total_orders=10
            )

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), day_fn
        )
        assert result.action is PaperCampaignAction.RETIRED
        assert "drawdown" in result.reason

    def test_underperformance_breach_retires(self) -> None:
        # Expected 0.10%/day but earning 0.0% -> underperformance climbs past
        # the 25bps stay limit early in the window.
        def day_fn(day: int) -> PaperDayOutcome:
            return PaperDayOutcome(day=day, return_pct=0.0, expected_return_pct=0.10)

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), day_fn
        )
        assert result.action is PaperCampaignAction.RETIRED
        assert "underperformance" in result.reason

    def test_operational_failure_breach_retires(self) -> None:
        def day_fn(day: int) -> PaperDayOutcome:
            return PaperDayOutcome(
                day=day,
                return_pct=0.05,
                expected_return_pct=0.05,
                failed_orders=2 if day == 3 else 0,
                total_orders=10,
            )

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), day_fn
        )
        assert result.action is PaperCampaignAction.RETIRED
        assert result.days_run == 3
        assert "failed orders" in result.reason

    def test_retired_candidate_loses_paper_evidence(self) -> None:
        # Rollback demotes the candidate: the failed paper deployment window
        # is cleared, validation evidence survives.
        def day_fn(day: int) -> PaperDayOutcome:
            return PaperDayOutcome(day=day, return_pct=-25.0, expected_return_pct=0.05)

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), day_fn
        )
        assert result.action is PaperCampaignAction.RETIRED
        assert result.evidence.paper_days_deployed is None
        assert result.evidence.paper_sharpe is None
        assert result.evidence.validation_samples == 500


class TestMeasurement:
    def test_zero_variance_returns_produce_zero_sharpe(self) -> None:
        def flat_day(day: int) -> PaperDayOutcome:
            return PaperDayOutcome(day=day, return_pct=0.1, expected_return_pct=0.05)

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=10)).run(
            "model-a", _validation_evidence(), flat_day
        )
        assert result.sharpe == 0.0

    def test_positive_variance_returns_produce_positive_sharpe(self) -> None:
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), _good_day
        )
        assert result.sharpe > 0.0

    def test_drawdown_tracks_running_peak(self) -> None:
        def day_fn(day: int) -> PaperDayOutcome:
            return (
                _good_day(day)
                if day < 8
                else PaperDayOutcome(
                    day=day, return_pct=-12.0, expected_return_pct=0.05, total_orders=10
                )
            )

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), day_fn
        )
        assert result.action is PaperCampaignAction.RETIRED
        assert result.drawdown_pct > 10.0
        assert result.days_run == 8


class TestDeterminismAndSerialization:
    def test_same_input_same_result(self) -> None:
        def run() -> dict[str, Any]:
            return (
                PaperAutonomyRunner(PaperCampaignConfig(campaign_days=7))
                .run("model-a", _validation_evidence(), _good_day)
                .as_dict()
            )

        assert run() == run()

    def test_result_as_dict(self) -> None:
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), _good_day
        )
        payload = result.as_dict()
        assert payload["candidate_id"] == "model-a"
        assert payload["action"] == "completed_advanced"
        assert payload["eligible_for_canary"] is True
        assert isinstance(payload["periods"], list)
        assert payload["periods"][0]["action"] == "continue"
        assert isinstance(payload["evidence"], dict)
        assert payload["evidence"]["paper_days_deployed"] == 30

    def test_period_as_dict(self) -> None:
        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=3)).run(
            "model-a", _validation_evidence(), _good_day
        )
        payload = result.periods[1].as_dict()
        assert payload["day"] == 2
        assert payload["action"] == "continue"
        assert payload["return_pct"] == 0.06

    def test_retired_result_as_dict(self) -> None:
        def day_fn(day: int) -> PaperDayOutcome:
            return PaperDayOutcome(day=day, return_pct=-25.0, expected_return_pct=0.05)

        result = PaperAutonomyRunner(PaperCampaignConfig(campaign_days=30)).run(
            "model-a", _validation_evidence(), day_fn
        )
        payload = result.as_dict()
        assert payload["action"] == "retired"
        assert payload["days_run"] == 1
        assert payload["eligible_for_canary"] is False
