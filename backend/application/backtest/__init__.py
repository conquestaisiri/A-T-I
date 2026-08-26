# backend/application/backtest/__init__.py
"""Backtest layer: deterministic replay of the live decision path.

The backtest is not a second simulator. It replays historical market context
through the exact ``DecisionPipelineService``/``PaperTradingSimulator`` the
paper-trading loop uses (ADR 0007), producing a quantified report per campaign.
Exchanging only the reasoner lets the operator compare the rule solver against
the LLM reasoner, and re-running reflection on the resulting ledger populates
episodic memory at scale (ADR 0010).
"""

from .backtest_runner import BacktestRunner
from .report import BacktestReport, ReplayStep

__all__ = ["BacktestRunner", "BacktestReport", "ReplayStep"]
