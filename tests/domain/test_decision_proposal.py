"""Unit tests for the Decision Proposal schema (Document 05)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.domain.decision.proposal import (
    AlternativeConsidered,
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)


def base_time() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def risk_context() -> RiskContext:
    return RiskContext(
        account_equity=100_000.0,
        open_exposure_pct=0.1,
        daily_loss_pct=0.0,
        monthly_loss_pct=0.0,
        total_loss_pct=0.0,
        drawdown_pct=0.0,
        position_count=1,
    )


def make_proposal(**overrides: Any) -> DecisionProposal:
    base: dict[str, Any] = dict(
        proposal_id="prop-1",
        correlation_id="corr-1",
        created_at=base_time(),
        symbol="btcusdt",
        hypothesis=Hypothesis(
            statement="trend continuation",
            supporting_evidence=(EvidenceItem(source="trend", summary="up", value=1.0),),
            opposing_evidence=(),
        ),
        confidence=0.8,
        uncertainty="volatility may spike on news",
        actions=(
            ProposedAction(
                action_type=ProposedActionType.ENTER_LONG,
                size_fraction=0.1,
                order=1,
                rationale="trend is up",
            ),
        ),
        risk_context=risk_context(),
        alternatives=(
            AlternativeConsidered(description="short", reason_rejected="no confirmation"),
        ),
        rationale="Trend is up with support from momentum.",
    )
    base.update(overrides)
    return DecisionProposal(**base)


class TestDecisionProposal:
    def test_created_is_immutable_and_serialisable(self):
        proposal = make_proposal()
        data = proposal.as_dict()

        assert data["proposal_id"] == "prop-1"
        assert data["confidence"] == 0.8
        assert data["hypothesis"]["statement"] == "trend continuation"
        assert data["actions"][0]["action_type"] == "enter_long"

    def test_roundtrip_preserves_all_fields(self):
        proposal = make_proposal()
        reloaded = DecisionProposal.from_dict(proposal.as_dict())
        assert reloaded == proposal

    def test_primary_action_returns_lowest_order(self):
        proposal = make_proposal(
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.STAND_ASIDE,
                    size_fraction=0.1,
                    order=2,
                    rationale="wait",
                ),
                ProposedAction(
                    action_type=ProposedActionType.ENTER_LONG,
                    size_fraction=0.1,
                    order=1,
                    rationale="go",
                ),
            )
        )
        assert proposal.primary_action is not None
        assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG

    def test_primary_action_none_when_empty(self):
        proposal = make_proposal(actions=())
        assert proposal.primary_action is None

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            make_proposal(confidence=1.5)

    def test_empty_symbol_rejected(self):
        with pytest.raises(ValueError):
            make_proposal(symbol="")

    def test_from_dict_rejects_malformed_actions(self):
        data = make_proposal().as_dict()
        data["actions"] = "not-a-list"
        with pytest.raises(ValueError):
            DecisionProposal.from_dict(data)


class TestProposedAction:
    def test_size_fraction_zero_rejected(self):
        with pytest.raises(ValueError):
            ProposedAction(
                action_type=ProposedActionType.ENTER_LONG,
                size_fraction=0.0,
                order=1,
                rationale="x",
            )

    def test_size_fraction_above_one_rejected(self):
        with pytest.raises(ValueError):
            ProposedAction(
                action_type=ProposedActionType.ENTER_LONG,
                size_fraction=1.5,
                order=1,
                rationale="x",
            )


class TestRiskContext:
    def test_roundtrip(self):
        ctx = risk_context()
        assert RiskContext.from_dict(ctx.as_dict()) == ctx

    def test_missing_new_fields_default_to_zero(self):
        data = risk_context().as_dict()
        del data["monthly_loss_pct"]
        del data["total_loss_pct"]
        reloaded = RiskContext.from_dict(data)
        assert reloaded.monthly_loss_pct == 0.0
        assert reloaded.total_loss_pct == 0.0
