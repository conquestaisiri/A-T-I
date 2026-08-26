# backend/application/backtest/backtest_runner.py
"""Deterministic backtest replay through the live decision path.

The runner replays a sequence of :class:`ReplayStep` values through the exact
``DecisionPipelineService``/``PaperTradingSimulator`` used for live paper
trading. Because the simulator is replay-driven (ADR 0007), the same steps and
mark prices always produce the identical report — there is no separate
backtest engine, only the real pipeline driven by historical context.

This is deliberately reasoner-agnostic: the pipeline already owns whichever
AIReasoner was wired (rule solver or LLM), so a campaign compares them by
exchanging only the reasoner, never the replay harness.
"""

from __future__ import annotations

import logging

from backend.application.backtest.report import BacktestReport, ReplayStep
from backend.application.pipeline.decision_pipeline_service import DecisionPipelineService
from backend.application.simulation.paper_fill_engine import PaperFillEngine
from backend.application.simulation.paper_trading_simulator import (
    PaperTradingSimulator,
    SimulationResult,
)

logger = logging.getLogger(__name__)


class BacktestRunner:
    """Replay historical steps through a fresh decision pipeline.

    Parameters
    ----------
    pipeline: DecisionPipelineService
        The reasoner-bearing pipeline the replay runs proposals through.
    simulator: PaperTradingSimulator
        The simulator backing the pipeline; its equity is the campaign equity.
    fill_engine: PaperFillEngine
        The engine that prices fills; the runner sets its mark price from each
        step before executing (the live loop does the same externally).
    symbol: str
        Symbol being replayed (used for the report; steps must match it).
    """

    def __init__(
        self,
        pipeline: DecisionPipelineService,
        simulator: PaperTradingSimulator,
        fill_engine: PaperFillEngine,
        *,
        symbol: str,
    ) -> None:
        if not symbol:
            raise ValueError("symbol must be a non-empty string")
        self._pipeline = pipeline
        self._simulator = simulator
        self._fill_engine = fill_engine
        self._symbol = symbol

    def run(self, steps: list[ReplayStep]) -> BacktestReport:
        """Replay ``steps`` through the pipeline and return a report.

        The pipeline is left in its final state so callers can reflect on the
        resulting ledger afterwards; a new runner is required per campaign.
        """
        if not steps:
            raise ValueError("backtest requires at least one replay step")

        starting_equity = self._simulator.equity
        peak_equity = starting_equity
        max_drawdown_pct = 0.0
        equity_curve: list[float] = [starting_equity]

        trades_opened = 0
        trades_closed = 0
        wins = 0
        losses = 0
        flats = 0
        approved = 0
        rejected = 0
        total_fees = 0.0
        total_slippage_bps = 0.0
        gross_profit = 0.0
        gross_loss = 0.0

        for step in steps:
            if step.context.snapshot.symbol != self._symbol:
                raise ValueError(
                    f"step symbol {step.context.snapshot.symbol!r} does not match "
                    f"campaign symbol {self._symbol!r}"
                )
            self._fill_engine.set_mark_price(step.mark_price)
            result = self._pipeline.process(step.context, step.mark_price)

            if result.report is not None and result.report.is_filled:
                total_fees += result.report.fee or 0.0
                slippage = result.report.slippage_bps
                if slippage is not None:
                    total_slippage_bps += abs(slippage)

            if result.result is SimulationResult.REJECTED:
                rejected += 1
            elif result.result is not SimulationResult.NO_ACTION:
                approved += 1

            if result.result is SimulationResult.OPENED:
                trades_opened += 1
            elif result.result is SimulationResult.CLOSED:
                trades_closed += 1
                pnl = result.record.realized_pnl if result.record is not None else None
                if pnl is not None:
                    if pnl > 0:
                        wins += 1
                        gross_profit += pnl
                    elif pnl < 0:
                        losses += 1
                        gross_loss += abs(pnl)
                    else:
                        flats += 1

            equity = self._simulator.equity
            equity_curve.append(equity)
            peak_equity = max(peak_equity, equity)
            if peak_equity > 0:
                drawdown = (peak_equity - equity) / peak_equity
                max_drawdown_pct = max(max_drawdown_pct, drawdown)

        final_equity = self._simulator.equity
        total_pnl = final_equity - starting_equity
        returns_pct = total_pnl / starting_equity if starting_equity > 0 else 0.0

        report = BacktestReport(
            symbol=self._symbol,
            steps=len(steps),
            starting_equity=starting_equity,
            final_equity=final_equity,
            total_pnl=total_pnl,
            returns_pct=returns_pct,
            max_drawdown_pct=max_drawdown_pct,
            trades_opened=trades_opened,
            trades_closed=trades_closed,
            wins=wins,
            losses=losses,
            flats=flats,
            approved=approved,
            rejected=rejected,
            total_fees=total_fees,
            total_slippage_bps=total_slippage_bps,
            gross_profit=gross_profit,
            gross_loss=gross_loss,
            equity_curve=tuple(equity_curve),
        )
        logger.info(
            "Backtest %s: steps=%d equity=%.2f->%.2f pnl=%.2f opened=%d closed=%d "
            "wins=%d losses=%d drawdown=%.4f",
            self._symbol,
            report.steps,
            starting_equity,
            final_equity,
            total_pnl,
            trades_opened,
            trades_closed,
            wins,
            losses,
            max_drawdown_pct,
        )
        return report
