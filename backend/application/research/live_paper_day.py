# backend/application/research/live_paper_day.py
"""Live paper day-function adapter (WS2.2).

The autonomous paper campaign runner (``PaperAutonomyRunner``) drives a
candidate through ``campaign_days`` by calling an injected day-function:
``day_fn(day)`` returns that day's :class:`PaperDayOutcome`. In a live
deployment the day-function is the *real* decision path — the same reasoner,
risk gate, supervisor and durability the operator drive route and market loop
use — serialised against them.

The adapter composes that live path into the campaign contract:

- **One decision per campaign day.** The live path models one campaign day as
  one decision at one mark price — the same granularity the paper-autonomy
  tests use, so a live campaign is measured exactly like an offline one.
- **Serialised under the operator lock.** The operator drive endpoint executes
  in a threadpool while the market loop runs on the event loop; both mutate the
  paper simulator. A campaign day must not interleave with either, so the whole
  day (snapshot, decide, snapshot) runs inside the shared ``operator_lock``.
- **Candidate-scoped account.** Each campaign builds its own
  :class:`PaperTradingSimulator` (via the builder), so a candidate's paper
  equity curve is measured in isolation from the live account.
- **No context means a flat day.** When the context source yields nothing for a
  day (market closed, feed not yet warmed), the day contributes a flat outcome
  and zero orders — measurement never fabricates returns.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol

from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.interfaces.order_gateway import OrderGateway
from backend.application.interfaces.proposal_repository import ProposalRepository
from backend.application.interfaces.risk_feed import RiskFeed
from backend.application.interfaces.risk_gate import RiskGate
from backend.application.interfaces.supervisor import Supervisor
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.reflection.reflection_service import ReflectionService
from backend.application.simulation.paper_trading_simulator import (
    PaperTradingSimulator,
    SimulationStep,
)
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import RiskContext
from backend.domain.execution.order import OrderStatus
from backend.domain.research.paper_campaign import PaperDayOutcome

# Where one campaign day's live input comes from.
# None means "no decision today" -> a flat day is recorded.
DayContextSource = Callable[[int], tuple[MarketContext, float] | None]


class PaperDecisionRunner(Protocol):
    """Minimal live-decision surface the day-fn needs.

    ``DecisionPipelineService`` satisfies this structurally; fakes in tests can
    implement it without the pipeline's full dependency graph.
    """

    def process(self, context: MarketContext, mark_price: float) -> SimulationStep: ...
    def risk_snapshot(self) -> RiskContext: ...


_FILLED_STATUSES = frozenset({OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED})


class LivePaperDecisionDayFn:
    """Adapt a live paper decision runner into the campaign's ``day_fn``.

    Parameters
    ----------
    runner: PaperDecisionRunner
        The candidate-scoped live decision path (see :func:`build_live_day_fn`).
    operator_lock: threading.Lock | None
        Shared serialisation lock against the operator drive and market loop.
        ``None`` disables locking (single consumer, e.g. tests/backtests).
    context_source: DayContextSource
        ``context_source(day)`` returns the (MarketContext, mark_price) to run
        for that campaign day, or ``None`` for a flat day.
    """

    def __init__(
        self,
        *,
        runner: PaperDecisionRunner,
        operator_lock: threading.Lock | None,
        context_source: DayContextSource,
    ) -> None:
        self._runner = runner
        self._operator_lock = operator_lock
        self._context_source = context_source

    def __call__(self, day: int) -> PaperDayOutcome:
        """Run one candidate-scoped decision day, serialised under the lock."""
        source = self._context_source(day)
        if source is None:
            return PaperDayOutcome(day=day)

        context, mark_price = source
        if self._operator_lock is not None:
            with self._operator_lock:
                return self._decide(day, context, mark_price)
        return self._decide(day, context, mark_price)

    def _decide(
        self,
        day: int,
        context: MarketContext,
        mark_price: float,
    ) -> PaperDayOutcome:
        """Snapshot, decide, and measure the day (caller holds the lock)."""
        before = self._runner.risk_snapshot().account_equity
        step = self._runner.process(context, mark_price)
        after = self._runner.risk_snapshot().account_equity
        return PaperDayOutcome(
            day=day,
            return_pct=self._return_pct(before, after),
            failed_orders=self._failed_orders(step),
            total_orders=self._total_orders(step),
        )

    @staticmethod
    def _total_orders(step: SimulationStep) -> int:
        return 1 if step.report is not None else 0

    @staticmethod
    def _failed_orders(step: SimulationStep) -> int:
        if step.report is None or step.report.status in _FILLED_STATUSES:
            return 0
        return 1

    @staticmethod
    def _return_pct(before: float, after: float) -> float:
        """Mark-to-market day return in percent, guarded against a flat baseline."""
        if before <= 0.0:
            return 0.0
        return round((after / before - 1.0) * 100.0, 6)


def build_live_day_fn(
    *,
    reasoner: AIReasoner,
    proposal_repository: ProposalRepository,
    risk_gate: RiskGate,
    order_gateway: OrderGateway,
    ledger: object,
    reflection: ReflectionService | None = None,
    supervisor: Supervisor | None = None,
    risk_feed: RiskFeed | None = None,
    starting_equity: float = 100_000.0,
    kelly_from_memory: bool = False,
    operator_lock: threading.Lock | None = None,
    context_source: DayContextSource,
) -> LivePaperDecisionDayFn:
    """Build a candidate-scoped live day-fn from shared live components.

    The only campaign-private object is the :class:`PaperTradingSimulator`
    (a fresh account so the candidate's equity curve is measured in isolation);
    everything else is shared with the live path, exactly as the composition
    root intends.
    """
    simulator = PaperTradingSimulator(
        risk_gate=risk_gate,
        order_gateway=order_gateway,
        ledger=ledger,  # type: ignore[arg-type]
        starting_equity=starting_equity,
    )
    pipeline = DecisionPipelineService(
        reasoner=reasoner,
        proposal_repository=proposal_repository,
        simulator=simulator,
        reflection=reflection,
        supervisor=supervisor,
        risk_feed=risk_feed,
        kelly_from_memory=kelly_from_memory,
    )
    return LivePaperDecisionDayFn(
        runner=pipeline,
        operator_lock=operator_lock,
        context_source=context_source,
    )
