# backend/application/research/strategy_population.py
"""Strategy population registry service (task T2-12-1).

Read-side service over the passport store: the store (P5-003) is the seed
and the single source of truth; this service projects it into the operator
registry (every passport as a member row) and, when enough real candidates
exist, a competition ladder ranked by pooled mean excess return.

Design rules
------------
- **Projection only.** No new persistence: ``registry()`` reads
  ``PassportStore.all_passports()`` and derives every row from the
  passport's own evidence payload. A second copy of evidence cannot drift
  because there is no second copy.
- **Real candidates.** A passport counts toward the ladder only when its
  evidence carries a pooled block with at least one fold (an actual OOS
  run). REJECTed candidates still compete — they were real evaluations and
  stay visible, ranked by the same honest rule.
- **Gated ladder.** Below ``min_ladder_candidates`` (default 3) the ladder
  is None with an explicit reason; a two-strategy "competition" would be a
  fabricated population view, so none is reported.
- **Advisory.** The ladder ranks; it never changes verdicts or statuses
  (promotion stays behind ``verdict_for_evidence``), and it is library-only:
  nothing here is wired into the live path.
"""

from __future__ import annotations

from backend.application.interfaces.passport_store import PassportStore
from backend.domain.research.passport import EvidenceVerdict, PassportStatus
from backend.domain.research.strategy_population import (
    CompetitionLadder,
    CompositionView,
    DatasetSlice,
    LadderEntry,
    PopulationComposition,
    StrategyPopulation,
    member_from_passport,
)

_DEFAULT_MIN_LADDER_CANDIDATES = 3
_DEFAULT_MIN_COMPOSITION_CANDIDATES = 5


class StrategyPopulationService:
    """Build the population registry and competition ladder from passports.

    Parameters
    ----------
    store: PassportStore
        The passport ledger to project (the population seed).
    min_ladder_candidates: int
        Minimum number of real (evaluated) candidates before the ladder is
        reported. Default 3, per the T2-12-1 backlog: population-level
        views come only after 3+ real candidates exist.
    """

    def __init__(
        self,
        store: PassportStore,
        *,
        min_ladder_candidates: int = _DEFAULT_MIN_LADDER_CANDIDATES,
        min_composition_candidates: int = _DEFAULT_MIN_COMPOSITION_CANDIDATES,
    ) -> None:
        if min_ladder_candidates < 1:
            raise ValueError("min_ladder_candidates must be >= 1")
        if min_composition_candidates < 1:
            raise ValueError("min_composition_candidates must be >= 1")
        self._store = store
        self._min_ladder_candidates = min_ladder_candidates
        self._min_composition_candidates = min_composition_candidates

    def registry(self) -> StrategyPopulation:
        """Project every passport into the registry (issue order, oldest first)."""
        passports = self._store.all_passports()
        members = tuple(
            sorted(
                (member_from_passport(p) for p in passports),
                key=lambda m: (m.created_at, m.passport_id),
            )
        )
        real = [m for m in members if m.n_folds > 0]
        if len(real) < self._min_ladder_candidates:
            return StrategyPopulation(
                members=members,
                ladder=None,
                ladder_unavailable_reason=(
                    f"competition ladder requires at least "
                    f"{self._min_ladder_candidates} real candidates with "
                    f"evaluated evidence; found {len(real)}"
                ),
                min_ladder_candidates=self._min_ladder_candidates,
            )
        ranked = sorted(
            real,
            key=lambda m: (
                -_rank_float(m.mean_excess_return_pct),
                -_rank_float(m.deflated_sharpe),
                m.passport_id,
            ),
        )
        entries = tuple(
            LadderEntry(rank=index + 1, member=member) for index, member in enumerate(ranked)
        )
        return StrategyPopulation(
            members=members,
            ladder=CompetitionLadder(entries=entries),
            ladder_unavailable_reason="",
            min_ladder_candidates=self._min_ladder_candidates,
        )

    def ladder(self) -> CompetitionLadder | None:
        """The competition ladder, or None when fewer candidates exist."""
        return self.registry().ladder

    def composition(self) -> PopulationComposition:
        """Population-level composition view, gated like the ladder.

        The view — members per lifecycle environment, per verdict, per
        dataset slice — is reported only once ``min_composition_candidates``
        real candidates exist; below that it is None with the reason. Same
        honesty rule as the ladder: a two-strategy "population breakdown"
        would be fabricated, so none is reported. Advisory only, derived
        from the same member projection as the ladder.
        """
        members = self.registry().members
        real = [m for m in members if m.n_folds > 0]
        if len(real) < self._min_composition_candidates:
            return PopulationComposition(
                view=None,
                unavailable_reason=(
                    f"composition view requires at least "
                    f"{self._min_composition_candidates} real candidates with "
                    f"evaluated evidence; found {len(real)}"
                ),
                min_composition_candidates=self._min_composition_candidates,
            )

        environment_counts = {
            status.value: sum(1 for m in members if m.status is status) for status in PassportStatus
        }
        verdict_counts = {
            verdict.value: sum(1 for m in members if m.verdict is verdict)
            for verdict in EvidenceVerdict
        }
        slices: dict[tuple[str, int], int] = {}
        for m in members:
            key = (m.dataset_id, m.dataset_version)
            slices[key] = slices.get(key, 0) + 1
        dataset_breakdown = tuple(
            DatasetSlice(dataset_id=dataset_id, dataset_version=version, n=count)
            for (dataset_id, version), count in sorted(slices.items())
        )
        return PopulationComposition(
            view=CompositionView(
                total=len(members),
                environment_counts=environment_counts,
                verdict_counts=verdict_counts,
                dataset_breakdown=dataset_breakdown,
            ),
            unavailable_reason="",
            min_composition_candidates=self._min_composition_candidates,
        )


def _rank_float(value: float | None) -> float:
    """Sort key for ladder ranking: None ranks below every number."""
    return value if value is not None else float("-inf")
