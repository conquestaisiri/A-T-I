# backend/domain/research/measured_loop.py
"""Measured research feedback loop contracts (task T3-30-1).

The research loop (T2-1x) generates and weighs hypotheses; the autonomy
program (T3-30's sibling) drives one candidate through the ladder. This
module is the contract for the *measured* loop: many iterations, each
landing a passport update on the ledger, and a loop-quality measure built
from passport survival rates — the loop's output is judged by how many of
its passports are still alive, never by how many hypotheses it printed.

Honesty invariants
------------------
- **Each iteration is recorded, including the misses.** An iteration that
  produced no report or was refused by the passport ledger is a record with
  ``passport_id=None`` and the reason — a loop report that hid its failures
  would be a fabricated success rate.
- **Survival is read from the ledger at measurement time.** Loop quality is
  not the run's snapshot: if the death system (T3-26/28) retired a loop
  passport since it was issued, survival reflects that. The ledger is the
  truth.
- **No passports -> no survival rate, honestly.** ``survival_rate`` is None
  with the reason when the loop issued nothing — a zero-strategy "survival
  rate" would be fabricated.
- **Quality is a rate over the loop's own passports.** Only passports this
  loop issued count toward its survival rate (optionally the last
  ``window`` iterations); the rest of the ledger is not the loop's score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LoopIterationRecord:
    """One loop iteration: what was tried and what the ledger did with it.

    Attributes
    ----------
    iteration: int
        1-based iteration number.
    hypothesis_id: str
        The hypothesis that was evaluated.
    passport_id: str | None
        The passport the ledger issued (None when the iteration produced
        nothing).
    verdict: str | None
        The passport's evidence verdict id when one was issued.
    status: str | None
        The passport's lifecycle status when one was issued (RETIRED when
        the verdict rejected it on arrival).
    reason: str
        Why the iteration ended as it did (issued / not evaluable /
        refused / error).
    """

    iteration: int
    hypothesis_id: str
    passport_id: str | None
    verdict: str | None
    status: str | None
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "hypothesis_id": self.hypothesis_id,
            "passport_id": self.passport_id,
            "verdict": self.verdict,
            "status": self.status,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class LoopQuality:
    """Loop quality measured from passport survival on the ledger.

    Attributes
    ----------
    iterations_run: int
        Iterations this loop ran (misses included).
    passports_issued: int
        Iterations that landed a passport on the ledger.
    alive: int
        Issued passports currently not RETIRED (read at measurement time).
    dead: int
        Issued passports currently RETIRED (rejected on arrival, or
        retired by the death system since).
    survival_rate: float | None
        ``alive / (alive + dead)``; None when nothing was issued (honest).
    promoted: int
        Issued passports whose verdict is PROMOTE_TO_PAPER.
    observed: int
        Issued passports whose verdict is OBSERVE.
    rejected: int
        Issued passports whose verdict is REJECT (dead on arrival).
    window: int | None
        The survival window applied (None = the whole run).
    unavailable_reason: str
        Why survival_rate is None (empty when it is present).
    """

    iterations_run: int
    passports_issued: int
    alive: int
    dead: int
    survival_rate: float | None
    promoted: int
    observed: int
    rejected: int
    window: int | None = None
    unavailable_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "iterations_run": self.iterations_run,
            "passports_issued": self.passports_issued,
            "alive": self.alive,
            "dead": self.dead,
            "survival_rate": round(self.survival_rate, 6)
            if self.survival_rate is not None
            else None,
            "promoted": self.promoted,
            "observed": self.observed,
            "rejected": self.rejected,
            "window": self.window,
            "unavailable_reason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class LoopReport:
    """The measured loop's full output: every iteration plus the quality.

    Attributes
    ----------
    records: tuple[LoopIterationRecord, ...]
        Every iteration, misses included, in run order.
    quality: LoopQuality
        The survival-based loop-quality measure over the run's passports.
    """

    records: tuple[LoopIterationRecord, ...]
    quality: LoopQuality

    def as_dict(self) -> dict[str, Any]:
        return {
            "records": [record.as_dict() for record in self.records],
            "quality": self.quality.as_dict(),
        }


__all__ = ["LoopIterationRecord", "LoopQuality", "LoopReport"]
