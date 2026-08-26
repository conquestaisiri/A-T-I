# backend/application/pipeline/decision_pipeline_service.py
"""Decision pipeline: context -> proposal -> risk gate -> simulator -> ledger.

The decision pipeline is the Phase 3 wiring. For each MarketContext it:
1. asks the AIReasoner for a DecisionProposal,
2. persists the proposal at-least-once,
3. runs the proposal through the risk gate and paper simulator at a mark price,
4. keeps PnL/equity state so the next step reasons against the fresh risk
   snapshot.

It is the single application-side owner of the decision order; it contains no
thresholds or strategy logic of its own (those live in the reasoner and the
risk gate).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.interfaces.macro_calendar import MacroCalendar
from backend.application.interfaces.proposal_repository import ProposalRepository
from backend.application.interfaces.risk_feed import RiskFeed
from backend.application.interfaces.supervisor import Supervisor
from backend.application.reflection.reflection_service import ReflectionService
from backend.application.simulation.paper_trading_simulator import (
    PaperTradingSimulator,
    SimulationResult,
    SimulationStep,
)
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import DecisionProposal, RiskContext

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DecisionPipelineService:
    """Run the deterministic decision path for one MarketContext.

    Parameters
    ----------
    reasoner: AIReasoner
        Produces a DecisionProposal for each context.
    proposal_repository: ProposalRepository
        Persists proposals at-least-once.
    simulator: PaperTradingSimulator
        Runs proposals through the risk gate and paper execution.
    reflection: ReflectionService | None
        When a proposal closes a trade, write its outcome to episodic memory.
        Optional; never blocks trading if reflection fails (the risk gate and
        seed_order remain the authority, learning is sandboxed per Document 05).
    supervisor: Supervisor | None
        Platform safety gate (kill switch + data staleness). When present, the
        pipeline refuses to produce or trade any proposal unless the supervisor
        is HEALTHY. Optional so backtests/campaigns stay fully deterministic;
        production wires a real supervisor.
    risk_feed: RiskFeed | None
        Live risk-signal feed into the shared gate (gap G3). After a fill and
        only when market stats are registered, feeds the realized fill into the
        impact veto's calibrator. Optional; backtests/campaigns stay inert.
    kelly_from_memory: bool
        When True (default False) and a position closes, derive a fractional-
        Kelly edge estimate from closed episodes and feed it to the risk gate
        via :meth:`RiskFeed.update_edge_estimate`. Per Constitution §5 learning
        may never alter risk parameters without operator approval, so this is
        opt-in only.
    """

    def __init__(
        self,
        reasoner: AIReasoner,
        proposal_repository: ProposalRepository,
        simulator: PaperTradingSimulator,
        reflection: ReflectionService | None = None,
        supervisor: Supervisor | None = None,
        risk_feed: RiskFeed | None = None,
        kelly_from_memory: bool = False,
        execution_policy: str = "always_market",
        macro_calendar: MacroCalendar | None = None,
        event_veto_pre_minutes: int = 30,
        event_veto_post_minutes: int = 15,
    ) -> None:
        self._reasoner = reasoner
        self._proposal_repository = proposal_repository
        self._simulator = simulator
        self._reflection = reflection
        self._supervisor = supervisor
        self._risk_feed = risk_feed
        self._kelly_from_memory = kelly_from_memory
        self._macro_calendar = macro_calendar
        self._event_veto_pre_minutes = max(0, int(event_veto_pre_minutes))
        self._event_veto_post_minutes = max(0, int(event_veto_post_minutes))
        from backend.application.execution.execution_policy import (
            build_execution_policy,
        )

        self._execution_policy = build_execution_policy(execution_policy)

    def process(self, context: MarketContext, mark_price: float) -> SimulationStep:
        """Produce, persist, and simulate a proposal for ``context``.

        If a supervisor is wired and it is not HEALTHY, no proposal is produced
        and the pipeline stands aside (refusing is always safe).
        """
        symbol = context.snapshot.symbol
        if self._supervisor is not None:
            decision = self._supervisor.check()
            if not decision.may_trade:
                logger.warning(
                    "Supervisor %s: %s (no proposal produced for %s)",
                    decision.status.value,
                    decision.reason,
                    symbol,
                )
                return SimulationStep(
                    proposal_id=f"supervisor-{symbol}",
                    result=SimulationResult.NO_ACTION,
                    risk_verdict=f"supervisor:{decision.status.value}",
                    report=None,
                    position=None,
                    record=None,
                )

        # Event-risk veto (risk-side safety, never alpha): refuse to open new
        # risk into a High-impact macro release. Mirrors the supervisor
        # refusal shape -- refusing is always safe, and the veto is logged.
        if self._macro_calendar is not None:
            gated = self._macro_calendar.high_impact_within(
                symbol,
                now=_utcnow(),
                pre_minutes=self._event_veto_pre_minutes,
                post_minutes=self._event_veto_post_minutes,
            )
            if gated is not None:
                logger.warning(
                    "Event veto: %s (%s %s) within +%d/-%dmin of release -- no proposal for %s",
                    gated.event_id,
                    gated.currency,
                    gated.title,
                    self._event_veto_pre_minutes,
                    self._event_veto_post_minutes,
                    symbol,
                )
                return SimulationStep(
                    proposal_id=f"event-veto-{symbol}",
                    result=SimulationResult.NO_ACTION,
                    risk_verdict=(f"event_veto:HIGH:{gated.currency}:{gated.title[:40]}"),
                    report=None,
                    position=None,
                    record=None,
                )

        risk = self._simulator.risk_snapshot(symbol=symbol)
        proposal = self._reasoner.reason(context, risk)
        self._persist_proposal(proposal)
        step = self._simulator.process(proposal, mark_price)
        self._feed_impact_fill(proposal.symbol, step)
        self._reflect_on_close(proposal.symbol, step)
        self._feed_kelly_edge(proposal.symbol, step)

        risk_after = self._simulator.risk_snapshot()
        logger.info(
            "Decision %s: verdict=%s result=%s equity=%.2f exposure_after=%.4f",
            proposal.proposal_id,
            step.risk_verdict,
            step.result.value,
            risk_after.account_equity,
            risk_after.open_exposure_pct,
        )
        return step

    def risk_snapshot(self) -> RiskContext:
        """Return the current portfolio risk snapshot (for operator surfaces)."""
        return self._simulator.risk_snapshot()

    def _feed_impact_fill(self, symbol: str, step: SimulationStep) -> None:
        """Feed a realized fill into the risk gate's impact veto (gap G3).

        The impact calibrator needs realized fills measured against their
        arrival price; ``report.slippage_bps`` is exactly that measure. Feeding
        is skipped when no fill occurred, arrival was unavailable, or the
        operator has not yet registered market stats for the symbol (the gate
        raises without them, and we never grow state from fabricated data). It
        never raises: a failed risk feed must never kill the decision path.
        """
        if self._risk_feed is None or step.report is None:
            return
        if not step.report.is_filled:
            return
        slippage_bps = step.report.slippage_bps
        if slippage_bps is None:
            return
        if not self._risk_feed.market_stats_registered(symbol):
            return
        try:
            self._risk_feed.record_impact_fill(
                symbol,
                quantity=step.report.quantity,
                realized_slippage_bps=slippage_bps,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Risk feed failed to record impact fill for %s", symbol)

    def _feed_kelly_edge(self, symbol: str, step: SimulationStep) -> None:
        """Optionally feed a fractional-Kelly edge estimate from closed episodes.

        Learning feed (gap G3). It is wired only when the operator explicitly
        enabled ``kelly_from_memory``; disabled by default per Constitution §5
        because learning must never alter risk parameters without approval.
        Episodic edge is derived after a close, when memory has just been
        updated, so the estimate reflects the outcome that produced it. Runs
        only for a closed step and only when reflection is wired at all.
        """
        if (
            self._risk_feed is None
            or self._reflection is None
            or not self._kelly_from_memory
            or step.result is not SimulationResult.CLOSED
        ):
            return
        try:
            edge = self._reflection.estimate_edge(symbol)
            if edge is not None:
                self._risk_feed.update_edge_estimate(symbol, edge)
        except Exception:  # noqa: BLE001
            logger.exception("Risk feed failed to update edge estimate for %s", symbol)

    def _reflect_on_close(self, symbol: str, step: SimulationStep) -> None:
        """Record a closed trade's outcome to episodic memory.

        Learning is sandboxed and out-of-band: a reflection failure is logged
        and swallowed so it never blocks or corrupts the trading path.
        """
        if self._reflection is None or step.result is not SimulationResult.CLOSED:
            return
        try:
            self._reflection.reflect(symbol)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Reflection failed after closing %s trade for %s: %s",
                step.result.value,
                symbol,
                exc,
            )

    def _persist_proposal(self, proposal: DecisionProposal) -> None:
        try:
            self._proposal_repository.save(proposal)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "Proposal repository save failed for %s: %s", proposal.proposal_id, exc
            )
            raise
