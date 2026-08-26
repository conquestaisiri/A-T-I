# backend/application/reflection/reflection_service.py
"""Reflection: turn closed trade outcomes into bounded episodic memory.

Constitution Document 05: *"Reflection should update this memory."* This
service is the memory writer. For each symbol it reads recent ledger records,
joins the original proposal (for confidence and action type), derives the
realised outcome from PnL, and records a :class:`MemoryEpisode`. Recording is
idempotent by ``ep-<trade_id>``, so re-running reflection is always safe.

The service is deliberately out-of-band: it reads the durable ledger and
proposals, never observes live prices, and never mutates risk parameters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.application.interfaces.ledger_repository import LedgerRepository
from backend.application.interfaces.memory_store import MemoryStore
from backend.application.interfaces.proposal_repository import ProposalRepository
from backend.application.risk.circuit_breaker_risk_gate import KellyEdgeEstimate
from backend.domain.decision.proposal import DecisionProposal
from backend.domain.execution.order import OrderSide
from backend.domain.execution.trade_record import TradeRecord, TradeStatus
from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReflectionStats:
    """Summary of one reflection pass.

    Attributes
    ----------
    trades_scanned: int
        Closed trades examined in the pass.
    episodes_recorded: int
        Episodes actually written (new or re-confirmed).
    wins: int
        Episodes whose outcome is a win.
    losses: int
        Episodes whose outcome is a loss.
    flats: int
        Episodes whose outcome is flat.
    """

    trades_scanned: int
    episodes_recorded: int
    wins: int
    losses: int
    flats: int


class ReflectionService:
    """Write episodic memory from closed trade outcomes."""

    def __init__(
        self,
        ledger: LedgerRepository,
        proposals: ProposalRepository,
        memory: MemoryStore,
    ) -> None:
        self._ledger = ledger
        self._proposals = proposals
        self._memory = memory

    def reflect(self, symbol: str, limit: int = 50) -> ReflectionStats:
        """Record episodes for closed trades of ``symbol``.

        Only closed trades are reflected (outcomes, not intentions). Open
        trades and stand-aside proposals produce no episode — the ledger is
        the single source of what actually happened.
        """
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        trades = self._ledger.find_recent(symbol, limit=limit)
        wins = losses = flats = 0
        recorded = 0
        for trade in trades:
            if trade.status is not TradeStatus.CLOSED:
                continue
            episode = self._episode_for(trade)
            self._memory.record(episode)
            recorded += 1
            if episode.outcome is MemoryOutcome.WIN:
                wins += 1
            elif episode.outcome is MemoryOutcome.LOSS:
                losses += 1
            else:
                flats += 1
        stats = ReflectionStats(
            trades_scanned=len(trades),
            episodes_recorded=recorded,
            wins=wins,
            losses=losses,
            flats=flats,
        )
        logger.info(
            "Reflection %s: scanned=%d recorded=%d wins=%d losses=%d flats=%d",
            symbol,
            stats.trades_scanned,
            stats.episodes_recorded,
            stats.wins,
            stats.losses,
            stats.flats,
        )
        return stats

    def estimate_edge(self, symbol: str, limit: int = 200) -> KellyEdgeEstimate | None:
        """Derive a fractional-Kelly edge estimate from closed-trade episodes.

        The edge estimator is the *learning* feed into the risk gate (gap G3).
        Per Constitution §5 it may never alter risk parameters without operator
        approval, so this is only wired by the decision pipeline when the
        operator has explicitly enabled ``kelly_from_memory`` — default off.

        Computes ``win_rate``, ``avg_win``, ``avg_loss`` and ``trade_count``
        from the symbol's stored WIN/LOSS episodes; ``FLAT`` and open episodes
        contribute nothing. Returns None when fewer than the gate's minimum
        evidence threshold of closed trades exist.
        """
        if limit <= 0:
            raise ValueError("limit must be a positive integer")
        episodes = self._memory.recall(symbol, limit=limit)
        outcomes: list[float] = [ep.realized_pnl for ep in episodes if ep.realized_pnl is not None]
        wins = [pnl for pnl in outcomes if pnl > 0.0]
        losses = [pnl for pnl in outcomes if pnl < 0.0]
        count = len(wins) + len(losses)
        if count < 10:  # gate requires trade_count >= 10 for edge, >= 20 for Kelly
            return None
        win_rate = (len(wins) / count) if count else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses)) / len(losses) if losses else 0.0
        return KellyEdgeEstimate(
            symbol=symbol,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            trade_count=count,
            confidence=min(1.0, count / 50.0),
        )

    def _episode_for(self, trade: TradeRecord) -> MemoryEpisode:
        proposal = self._proposal_for(trade)
        outcome = _outcome_for(trade.realized_pnl)
        return MemoryEpisode(
            episode_id=f"ep-{trade.trade_id}",
            correlation_id=trade.correlation_id or "",
            symbol=trade.symbol,
            created_at=trade.opened_at,
            proposal_id=trade.proposal_id or "",
            action_type=_action_type(trade, proposal),
            confidence=_confidence(proposal),
            outcome=outcome,
            realized_pnl=trade.realized_pnl,
            summary=_summary(trade, outcome),
        )

    def _proposal_for(self, trade: TradeRecord) -> DecisionProposal | None:
        if not trade.proposal_id:
            return None
        try:
            return self._proposals.find_by_id(trade.proposal_id)
        except Exception:  # noqa: BLE001 - missing proposal must not break reflection
            logger.warning("Proposal %s missing for trade %s", trade.proposal_id, trade.trade_id)
            return None


def _action_type(trade: TradeRecord, proposal: DecisionProposal | None) -> str:
    if proposal is not None and proposal.primary_action is not None:
        return proposal.primary_action.action_type.value
    return "enter_long" if trade.side is OrderSide.BUY else "enter_short"


def _confidence(proposal: DecisionProposal | None) -> float:
    return proposal.confidence if proposal is not None else 0.5


def _outcome_for(realized_pnl: float | None) -> MemoryOutcome:
    if realized_pnl is None:
        return MemoryOutcome.OPEN
    if realized_pnl > 0:
        return MemoryOutcome.WIN
    if realized_pnl < 0:
        return MemoryOutcome.LOSS
    return MemoryOutcome.FLAT


def _summary(trade: TradeRecord, outcome: MemoryOutcome) -> str:
    direction = "long" if trade.side is OrderSide.BUY else "short"
    pnl = f"{trade.realized_pnl:+.2f}" if trade.realized_pnl is not None else "n/a"
    return f"{trade.symbol} {direction} {outcome.value} (pnl {pnl})"
