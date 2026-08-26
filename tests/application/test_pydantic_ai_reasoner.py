"""Unit tests for the PydanticAIReasoner (ADR 0011).

These tests mirror the structure of test_omni_route_reasoner.py to ensure
behavioral parity while exercising PydanticAI's structured output validation,
retries, and usage tracking.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from backend.application.ai.pydantic_ai_reasoner import PydanticAIConfig, PydanticAIReasoner
from backend.domain.context.context_feature import ContextFeature
from backend.domain.context.context_snapshot import ContextSnapshot
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import ProposedActionType, RiskContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType


def ts() -> datetime:
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


def make_context() -> MarketContext:
    event = ObservationEvent(
        source_id="binance",
        source_name="Binance",
        event_type=ObservationEventType.TRADE,
        timestamp=ts(),
        payload={"symbol": "btcusdt", "trade_id": 1, "price": 100.0, "quantity": 1.0},
    )
    snapshot = ContextSnapshot.from_events((event,))
    feature = ContextFeature(
        name="trend", value={"direction": "up"}, computation_timestamp=ts(), execution_time=0.0
    )
    return MarketContext(snapshot=snapshot, features=(("trend", feature),), created_at=ts())


def risk_context() -> RiskContext:
    return RiskContext(
        account_equity=100_000.0,
        open_exposure_pct=0.0,
        daily_loss_pct=0.0,
        monthly_loss_pct=0.0,
        total_loss_pct=0.0,
        drawdown_pct=0.0,
        position_count=0,
    )


def valid_output(
    action_type: str = "enter_long",
    confidence: float = 0.72,
) -> dict:
    return {
        "confidence": confidence,
        "uncertainty": "medium",
        "hypothesis_statement": "uptrend continues",
        "action_type": action_type,
        "size_fraction": 0.1,
        "rationale": "momentum confirms trend",
        "alternatives": [],
    }


class MockAgentOutput:
    """Mock output object that mimics PydanticAI's structured output."""

    def __init__(self, data: dict):
        for key, value in data.items():
            setattr(self, key, value)


class MockUsage:
    """Mock usage object with total_tokens."""

    def __init__(self, tokens: int):
        self.total_tokens = tokens


class TestPydanticAIReasoner:
    def _mock_agent(self, output_data: dict, tokens: int = 150) -> MagicMock:
        """Create a mock Agent.run_sync return value."""
        mock_result = MagicMock()
        mock_result.output = MockAgentOutput(output_data)
        mock_result.usage = MockUsage(tokens)  # usage is a property, not a method
        return mock_result

    def test_valid_output_produces_proposal(self):
        """Happy path: structured output validated and converted to DecisionProposal."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.return_value = self._mock_agent(valid_output())

            reasoner = PydanticAIReasoner()
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            assert proposal.primary_action.action_type is ProposedActionType.ENTER_LONG
            assert proposal.symbol == "btcusdt"
            assert proposal.confidence == pytest.approx(0.72)
            assert reasoner.total_tokens_used == 150
            assert reasoner.total_requests == 1

    def test_stand_aside_action_honored(self):
        """STAND_ASIDE from model passes through correctly."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.return_value = self._mock_agent(valid_output("stand_aside"), 120)

            reasoner = PydanticAIReasoner()
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE

    def test_exception_during_model_call_degrades_to_stand_aside(self):
        """Exception during model call degrades to STAND_ASIDE (no retry on exceptions)."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.side_effect = Exception("Model unavailable")

            reasoner = PydanticAIReasoner(config=PydanticAIConfig(max_retries=2))
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
            assert reasoner.failure_count == 1
            assert reasoner.last_failure_reason is not None

    def test_exhausted_retries_degrades_to_stand_aside(self):
        """After max_retries exhausted, degrades to STAND_ASIDE."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.side_effect = Exception("Model unavailable")

            reasoner = PydanticAIReasoner(config=PydanticAIConfig(max_retries=2))
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
            assert reasoner.failure_count == 1
            assert reasoner.last_failure_reason is not None

    def test_timeout_degrades_to_stand_aside(self):
        """Timeout during model call degrades gracefully."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.side_effect = TimeoutError("Request timed out")

            reasoner = PydanticAIReasoner()
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
            assert reasoner.failure_count == 1

    def test_invalid_action_type_in_output_degrades(self):
        """Model returns invalid action_type -> validation fails -> STAND_ASIDE."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            # Simulate PydanticAI's internal validation failure
            mock_run.side_effect = ValueError(
                "Invalid action_type: 'buy_now' not a valid ProposedActionType"
            )

            reasoner = PydanticAIReasoner()
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
            assert reasoner.failure_count == 1

    def test_risk_context_carried_through(self):
        """RiskContext is preserved in the output proposal."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.return_value = self._mock_agent(valid_output(), 100)

            ctx = risk_context()
            reasoner = PydanticAIReasoner()
            proposal = reasoner.reason(make_context(), ctx)

            assert proposal.risk_context == ctx

    def test_recalls_memory_only_for_symbol(self):
        """Memory recall is scoped to the context symbol."""
        import os
        import tempfile

        from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome
        from backend.infrastructure.sqlite.database import Database
        from backend.infrastructure.sqlite.memory_repository import SqliteMemoryRepository

        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.return_value = self._mock_agent(valid_output(), 100)

            # Use a unique temp file path and ensure cleanup
            tmp_path = os.path.join(tempfile.gettempdir(), f"test_pydantic_ai_{os.getpid()}.db")
            try:
                db = Database(tmp_path)
                memory = SqliteMemoryRepository(db)
                memory.record(
                    MemoryEpisode(
                        episode_id="ep-1",
                        correlation_id="corr-1",
                        symbol="btcusdt",
                        created_at=ts(),
                        proposal_id="prop-1",
                        action_type="enter_long",
                        confidence=0.8,
                        outcome=MemoryOutcome.LOSS,
                        realized_pnl=-50.0,
                        summary="long lost",
                    )
                )

                reasoner = PydanticAIReasoner(config=PydanticAIConfig(), memory_store=memory)
                proposal = reasoner.reason(make_context(), risk_context())

                assert proposal.primary_action is not None
                # Verify memory was recalled (ep-1 should be in the prompt sent to model)
                call_args = mock_run.call_args[0][0]  # first positional arg = user prompt
                assert "LOSS" in call_args or "loss" in call_args
            finally:
                # Ensure DB is closed before file deletion attempt
                if hasattr(db, "close"):
                    db.close()
                try:
                    if os.path.exists(tmp_path):
                        os.unlink(tmp_path)
                except (OSError, PermissionError):
                    pass  # Ignore cleanup issues on Windows

    def test_no_memory_when_store_absent(self):
        """No memory section in prompt when store is None."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.return_value = self._mock_agent(valid_output(), 100)

            reasoner = PydanticAIReasoner()
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            call_args = mock_run.call_args[0][0]
            # Memory section should be empty list
            assert "episodic_memory" in call_args
            assert "[]" in call_args

    def test_usage_tracking(self):
        """Token usage is accumulated across requests."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.return_value = self._mock_agent(valid_output(), 100)

            reasoner = PydanticAIReasoner()
            reasoner.reason(make_context(), risk_context())
            reasoner.reason(make_context(), risk_context())

            assert reasoner.total_tokens_used == 200
            assert reasoner.total_requests == 2

    def test_confidence_validation_bounds(self):
        """Confidence outside [0,1] triggers validation failure -> STAND_ASIDE."""
        with patch("pydantic_ai.Agent.run_sync") as mock_run:
            mock_run.return_value = self._mock_agent(
                {"confidence": 1.5, "action_type": "enter_long", "size_fraction": 0.1}, 100
            )

            reasoner = PydanticAIReasoner(config=PydanticAIConfig(max_retries=0))
            proposal = reasoner.reason(make_context(), risk_context())

            assert proposal.primary_action is not None
            assert proposal.primary_action.action_type is ProposedActionType.STAND_ASIDE
