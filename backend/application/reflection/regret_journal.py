# backend/application/reflection/regret_journal.py
"""Nightly regret journal — reviews losing trades and writes lessons to memory."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

logger = logging.getLogger(__name__)


def nightly_review(
    ledger_repo: Any,
    memory_store: Any,
    lookback_days: int = 7,
) -> dict[str, Any]:
    """Scan recent closed trades, summarize losers, write a regret episode."""
    try:
        # ledger_repo is SqliteLedgerRepository — it has no direct query for
        # losers, so we scan recent trades via the DB connection if available.
        # Fallback: try to list via ledger_repo if it exposes a method.
        trades: list[Any] = []
        if hasattr(ledger_repo, "_db"):
            db = ledger_repo._db
            query = (
                "SELECT payload FROM trade_ledger WHERE status='closed' "
                "ORDER BY closed_at DESC LIMIT 50"
            )
            rows = db.connection.execute(query).fetchall()
            import json

            for r in rows:
                try:
                    t = json.loads(r["payload"])
                    # Filter to last lookback_days
                    closed_at = t.get("closed_at")
                    if closed_at:
                        when = datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                        if when < datetime.now(UTC) - timedelta(days=lookback_days):
                            continue
                    trades.append(t)
                except Exception:
                    continue
        losers = [t for t in trades if (t.get("realized_pnl") or 0) < 0]
        winners = [t for t in trades if (t.get("realized_pnl") or 0) > 0]
        total = len(trades)
        if total == 0:
            return {"total": 0, "message": "No closed trades in lookback period."}
        win_rate = len(winners) / total if total else 0
        avg_win = sum(t.get("realized_pnl", 0) for t in winners) / len(winners) if winners else 0
        avg_loss = sum(t.get("realized_pnl", 0) for t in losers) / len(losers) if losers else 0
        expectancy = (win_rate * avg_win + (1 - win_rate) * avg_loss) if total else 0

        # Find worst loser
        worst = min(losers, key=lambda x: x.get("realized_pnl", 0)) if losers else None
        summary = (
            f"Weekly review: {total} trades, {win_rate:.0%} win, avg win ${avg_win:.2f}, "
            f"avg loss ${avg_loss:.2f}, expectancy ${expectancy:.4f}."
        )
        if worst:
            sym = worst.get("symbol")
            side = worst.get("side")
            pnl = worst.get("realized_pnl")
            summary += f" Worst: {sym} {side} {pnl:.2f}."
        # Write as a memory episode if memory_store available
        if memory_store is not None and hasattr(memory_store, "save"):
            try:
                from backend.domain.memory.episode import MemoryEpisode, MemoryOutcome

                ep = MemoryEpisode(
                    episode_id=f"regret-{datetime.now(UTC).isoformat()}",
                    correlation_id="regret-journal",
                    symbol="PORTFOLIO",
                    created_at=datetime.now(UTC),
                    proposal_id="regret",
                    action_type="review",
                    confidence=0.5,
                    outcome=MemoryOutcome.FLAT,
                    realized_pnl=expectancy,
                    summary=summary,
                )
                memory_store.save(ep)
            except Exception:
                pass
        return {
            "total": total,
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": win_rate,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "expectancy": expectancy,
            "summary": summary,
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Regret journal failed")
        return {"error": str(exc)}
