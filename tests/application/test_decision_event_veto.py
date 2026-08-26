# tests/application/test_decision_event_veto.py
"""The event-risk veto refuses risk into High-impact releases (safety, not alpha)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.domain.decision.proposal import (
    AlternativeConsidered,
    DecisionProposal,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)


@dataclass
class _GatingEvent:
    event_id: str = "abc"
    currency: str = "USD"
    title: str = "Core PCE Price Index m/m"


def _proposal(symbol: str) -> DecisionProposal:
    return DecisionProposal(
        proposal_id="p1",
        correlation_id="c1",
        created_at=datetime.now(UTC),
        symbol=symbol,
        hypothesis=Hypothesis(statement="test", supporting_evidence=(), opposing_evidence=()),
        confidence=0.5,
        uncertainty="nothing",
        actions=(
            ProposedAction(
                action_type=ProposedActionType.STAND_ASIDE,
                size_fraction=1.0,
                order=0,
                rationale="flat",
            ),
        ),
        risk_context=RiskContext(
            account_equity=10_000.0,
            open_exposure_pct=0.0,
            daily_loss_pct=0.0,
            monthly_loss_pct=0.0,
            total_loss_pct=0.0,
            drawdown_pct=0.0,
            position_count=0,
        ),
        alternatives=(AlternativeConsidered("none", "n/a"),),
        rationale="unit-test proposal",
    )


def _pipeline_with_calendar(calendar: Any) -> tuple[DecisionPipelineService, Any, Any]:
    repo = MagicMock()
    simulator = MagicMock()
    from backend.application.simulation.paper_trading_simulator import (
        SimulationResult,
        SimulationStep,
    )

    simulator.process.return_value = SimulationStep(
        proposal_id="sim-1",
        result=SimulationResult.NO_ACTION,
        risk_verdict="ok",
        report=None,
        position=None,
        record=None,
    )
    pipeline = DecisionPipelineService(
        reasoner=MagicMock(),
        proposal_repository=repo,
        simulator=simulator,
        macro_calendar=calendar,
        event_veto_pre_minutes=30,
        event_veto_post_minutes=15,
    )
    return pipeline, repo, simulator


def _context() -> Any:
    return SimpleNamespace(snapshot=SimpleNamespace(symbol="BTCUSDT"))


def test_high_impact_window_vetoes_before_reasoning() -> None:
    calendar = MagicMock()
    calendar.high_impact_within.return_value = _GatingEvent()
    pipeline, repo, _sim = _pipeline_with_calendar(calendar)

    step = pipeline.process(_context(), mark_price=100.0)

    assert step.result.value == "no_action"
    assert step.proposal_id == "event-veto-BTCUSDT"
    assert "event_veto" in step.risk_verdict
    # The reasoner was never consulted and nothing was persisted.
    pipeline._reasoner.reason.assert_not_called()  # noqa: SLF001
    repo.save.assert_not_called()


def test_no_gating_event_falls_through_to_reasoner() -> None:
    calendar = MagicMock()
    calendar.high_impact_within.return_value = None
    pipeline, repo, sim = _pipeline_with_calendar(calendar)
    reasoner = MagicMock()
    reasoner.reason.return_value = _proposal("BTCUSDT")
    pipeline._reasoner = reasoner  # noqa: SLF001

    context = _context()
    step = pipeline.process(context, mark_price=100.0)

    reasoner.reason.assert_called_once()
    sim.process.assert_called_once()
    assert repo.save.called
    assert step.result.value == "no_action"


def test_veto_disabled_when_calendar_absent() -> None:
    pipeline, repo, _sim = _pipeline_with_calendar(None)
    reasoner = MagicMock()
    reasoner.reason.return_value = _proposal("BTCUSDT")
    pipeline._reasoner = reasoner  # noqa: SLF001

    step = pipeline.process(_context(), mark_price=100.0)
    reasoner.reason.assert_called_once()
    assert repo.save.called
    assert step.risk_verdict != ""
