"""Tests for the paper-campaign lifecycle state machine (WS2.1).

The machine is the single authority on legal campaign transitions. It must
hold: a campaign starts once (idempotently), moves forward only, reaches
exactly one terminal status, is absorbing once terminal, and that a
created-but-never-started campaign may still be cancelled.
"""

from __future__ import annotations

import pytest
from backend.domain.research.campaign_state import (
    cancel,
    finish,
    is_terminal,
    start,
    transition,
)
from backend.domain.research.records import CampaignStatus


class TestStartIdempotent:
    def test_pending_to_running(self) -> None:
        t = start(CampaignStatus.PENDING, occurred_at="2026-08-13T00:00:00.000+00:00")
        assert t is not None
        assert t.from_status is CampaignStatus.PENDING
        assert t.to_status is CampaignStatus.RUNNING

    def test_retry_on_running_is_noop(self) -> None:
        assert start(CampaignStatus.RUNNING, occurred_at="now") is None

    def test_terminal_cannot_start(self) -> None:
        for status in (
            CampaignStatus.COMPLETED,
            CampaignStatus.RETIRED,
            CampaignStatus.CANCELLED,
        ):
            with pytest.raises(ValueError, match="cannot start a terminal campaign"):
                start(status, occurred_at="now")


class TestForwardOnly:
    def test_run_to_completed(self) -> None:
        t = finish(
            CampaignStatus.RUNNING,
            CampaignStatus.COMPLETED,
            occurred_at="now",
            reason="clean window",
        )
        assert t.to_status is CampaignStatus.COMPLETED
        assert t.reason == "clean window"

    def test_run_to_retired(self) -> None:
        t = finish(
            CampaignStatus.RUNNING,
            CampaignStatus.RETIRED,
            occurred_at="now",
            reason="stay-limit breach",
        )
        assert t.to_status is CampaignStatus.RETIRED

    def test_run_to_cancelled(self) -> None:
        t = finish(
            CampaignStatus.RUNNING,
            CampaignStatus.CANCELLED,
            occurred_at="now",
            reason="operator request",
        )
        assert t.to_status is CampaignStatus.CANCELLED

    def test_backward_move_rejected(self) -> None:
        with pytest.raises(ValueError, match="illegal campaign transition"):
            transition(
                CampaignStatus.RUNNING,
                CampaignStatus.PENDING,
                occurred_at="now",
            )

    def test_jump_from_pending_to_terminal_rejected(self) -> None:
        with pytest.raises(ValueError, match="illegal campaign transition"):
            transition(
                CampaignStatus.PENDING,
                CampaignStatus.COMPLETED,
                occurred_at="now",
            )

    def test_noop_rejected(self) -> None:
        with pytest.raises(ValueError, match="no-op transition"):
            transition(
                CampaignStatus.RUNNING,
                CampaignStatus.RUNNING,
                occurred_at="now",
            )

    def test_finish_requires_running(self) -> None:
        with pytest.raises(ValueError, match="only RUNNING may finish"):
            finish(
                CampaignStatus.PENDING,
                CampaignStatus.COMPLETED,
                occurred_at="now",
                reason="never started",
            )

    def test_finish_requires_terminal_target(self) -> None:
        with pytest.raises(ValueError, match="not a terminal status"):
            finish(
                CampaignStatus.RUNNING,
                CampaignStatus.PENDING,
                occurred_at="now",
                reason="oops",
            )


class TestCancel:
    def test_pending_may_be_cancelled(self) -> None:
        t = cancel(CampaignStatus.PENDING, occurred_at="now", reason="no longer wanted")
        assert t.to_status is CampaignStatus.CANCELLED
        assert t.reason == "no longer wanted"

    def test_running_may_be_cancelled(self) -> None:
        t = cancel(CampaignStatus.RUNNING, occurred_at="now")
        assert t.to_status is CampaignStatus.CANCELLED

    def test_terminal_cannot_be_cancelled(self) -> None:
        for status in (
            CampaignStatus.COMPLETED,
            CampaignStatus.RETIRED,
            CampaignStatus.CANCELLED,
        ):
            with pytest.raises(ValueError, match="cannot cancel a terminal campaign"):
                cancel(status, occurred_at="now")


class TestTerminalAbsorbing:
    def test_terminal_rejects_all_moves(self) -> None:
        for terminal in (
            CampaignStatus.COMPLETED,
            CampaignStatus.RETIRED,
            CampaignStatus.CANCELLED,
        ):
            with pytest.raises(ValueError, match="illegal campaign transition"):
                transition(
                    terminal,
                    CampaignStatus.RUNNING,
                    occurred_at="now",
                )

    def test_is_terminal(self) -> None:
        assert is_terminal(CampaignStatus.COMPLETED)
        assert is_terminal(CampaignStatus.RETIRED)
        assert is_terminal(CampaignStatus.CANCELLED)
        assert not is_terminal(CampaignStatus.PENDING)
        assert not is_terminal(CampaignStatus.RUNNING)

    def test_transition_as_dict(self) -> None:
        t = finish(
            CampaignStatus.RUNNING,
            CampaignStatus.COMPLETED,
            occurred_at="2026-08-13T00:00:00.000+00:00",
            reason="clean window",
        )
        payload = t.as_dict()
        assert payload == {
            "from_status": "running",
            "to_status": "completed",
            "occurred_at": "2026-08-13T00:00:00.000+00:00",
            "reason": "clean window",
        }
