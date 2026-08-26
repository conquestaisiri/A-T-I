"""Tests for the strategy population registry (T2-12-1).

The registry must:

1. Project every passport from the store into a member row, reading the
   evidence from the passport's own payload (never a second copy).
2. Gate the competition ladder at 3+ real candidates (real = pooled evidence
   with at least one fold), with an explicit reason below the gate.
3. Rank real candidates by pooled mean excess return (ties: Deflated Sharpe,
   then passport id) — deterministic and advisory only.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.application.research.strategy_population import (
    StrategyPopulationService,
)
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
)
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import (
    SqlitePassportRepository,
)


def store(tmp_path):
    return SqlitePassportRepository(Database(tmp_path / "p.db"))


def passport(
    passport_id: str,
    *,
    n_folds: int = 8,
    mean_excess: float | None = 0.5,
    dsr: float | None = 1.0,
    positive_rate: float = 0.7,
    beats_bh: float = 0.7,
    status: PassportStatus = PassportStatus.CANDIDATE,
    verdict: EvidenceVerdict = EvidenceVerdict.OBSERVE,
    created_at: datetime | None = None,
    regime_score: float | None = None,
) -> StrategyPassport:
    """A passport with a pooled evidence payload (or none when n_folds == 0)."""
    evidence = {}
    if n_folds > 0:
        evidence["pooled"] = {
            "n_folds": n_folds,
            "total_test_bars": n_folds * 20,
            "total_trades": 0,
            "total_wins": 0,
            "total_losses": 0,
            "total_fees": 0.0,
            "total_slippage_bps": 0.0,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "mean_return_pct": (mean_excess or 0.0) + 0.2,
            "median_return_pct": mean_excess,
            "mean_excess_return_pct": mean_excess,
            "positive_fold_rate": positive_rate,
            "beats_buy_and_hold_rate": beats_bh,
            "mean_max_drawdown_pct": -5.0,
            "deflated_sharpe": dsr,
            "reasoner": "RuleBasedSolver",
            "cost_model": {"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
        }
    if regime_score is not None:
        evidence["regime_evidence"] = {"robustness_score": regime_score}
    return StrategyPassport(
        passport_id=passport_id,
        created_at=created_at or datetime(2026, 8, 17, 12, 0, tzinfo=UTC),
        hypothesis=f"hypothesis of {passport_id}",
        dataset_id="btcusdt",
        dataset_version=1,
        features=("trend", "momentum"),
        model="RuleBasedSolver",
        trial_count=10,
        evidence=evidence,
        verdict=PassportVerdict(verdict),
        status=status,
    )


class TestRegistryProjection:
    def test_empty_store_projects_empty_registry(self, tmp_path):
        svc = StrategyPopulationService(store(tmp_path))
        registry = svc.registry()
        assert registry.members == ()
        assert registry.ladder is None
        assert "requires at least 3 real candidates" in registry.ladder_unavailable_reason

    def test_every_passport_becomes_a_member(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A"))
        s.save_passport(passport("B", n_folds=0, status=PassportStatus.RESEARCH))
        svc = StrategyPopulationService(s)
        registry = svc.registry()
        assert [m.passport_id for m in registry.members] == ["A", "B"]
        member = registry.members[0]
        assert member.n_folds == 8
        assert member.mean_excess_return_pct == 0.5
        assert member.deflated_sharpe == 1.0
        assert member.positive_fold_rate == 0.7
        assert member.beats_buy_and_hold_rate == 0.7
        assert member.verdict is EvidenceVerdict.OBSERVE
        assert member.status is PassportStatus.CANDIDATE

    def test_members_ordered_by_issue_then_id(self, tmp_path):
        s = store(tmp_path)
        earlier = datetime(2026, 1, 1, tzinfo=UTC)
        later = datetime(2026, 2, 1, tzinfo=UTC)
        s.save_passport(passport("Z", created_at=earlier))
        s.save_passport(passport("Y", created_at=later))
        s.save_passport(passport("X", created_at=earlier))
        registry = StrategyPopulationService(s).registry()
        assert [m.passport_id for m in registry.members] == ["X", "Z", "Y"]

    def test_regime_robustness_projected_when_present(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A", regime_score=0.75))
        member = StrategyPopulationService(s).registry().members[0]
        assert member.regime_robustness_score == 0.75

    def test_as_dict_round_trip(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A"))
        payload = StrategyPopulationService(s).registry().as_dict()
        assert len(payload["members"]) == 1
        assert payload["ladder"] is None
        assert payload["min_ladder_candidates"] == 3
        assert payload["members"][0]["passport_id"] == "A"
        assert payload["members"][0]["verdict"] == "observe"


class TestCompetitionLadder:
    def test_ladder_unavailable_below_gate(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A"))
        s.save_passport(passport("B"))
        registry = StrategyPopulationService(s).registry()
        assert registry.ladder is None
        assert "found 2" in registry.ladder_unavailable_reason

    def test_unreal_candidates_do_not_count_toward_gate(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A"))
        s.save_passport(passport("B", n_folds=0, status=PassportStatus.RESEARCH))
        s.save_passport(passport("C", n_folds=0, status=PassportStatus.RESEARCH))
        registry = StrategyPopulationService(s).registry()
        assert registry.ladder is None
        assert "found 1" in registry.ladder_unavailable_reason

    def test_ladder_ranks_by_mean_excess_desc(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("weak", mean_excess=0.1, dsr=0.5))
        s.save_passport(passport("mid", mean_excess=0.5, dsr=1.0))
        s.save_passport(passport("strong", mean_excess=1.5, dsr=2.0))
        registry = StrategyPopulationService(s).registry()
        ladder = registry.ladder
        assert ladder is not None
        assert [e.rank for e in ladder.entries] == [1, 2, 3]
        assert [e.member.passport_id for e in ladder.entries] == [
            "strong",
            "mid",
            "weak",
        ]

    def test_ladder_tie_break_by_deflated_sharpe(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("dsr_low", mean_excess=0.5, dsr=0.5))
        s.save_passport(passport("dsr_high", mean_excess=0.5, dsr=1.5))
        s.save_passport(passport("other", mean_excess=0.2, dsr=0.9))
        ladder = StrategyPopulationService(s).ladder()
        assert ladder is not None
        assert [e.member.passport_id for e in ladder.entries] == [
            "dsr_high",
            "dsr_low",
            "other",
        ]

    def test_ladder_tie_break_by_passport_id(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("b", mean_excess=0.5, dsr=1.0))
        s.save_passport(passport("a", mean_excess=0.5, dsr=1.0))
        s.save_passport(passport("c", mean_excess=0.4, dsr=1.0))
        ladder = StrategyPopulationService(s).ladder()
        assert ladder is not None
        assert [e.member.passport_id for e in ladder.entries] == ["a", "b", "c"]

    def test_rejected_candidates_stay_visible_in_ladder(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(
            passport("rejected", mean_excess=2.0, dsr=3.0, verdict=EvidenceVerdict.REJECT)
        )
        s.save_passport(passport("observe", mean_excess=0.5, dsr=1.0))
        s.save_passport(
            passport("promoted", mean_excess=1.0, dsr=1.5, verdict=EvidenceVerdict.PROMOTE_TO_PAPER)
        )
        ladder = StrategyPopulationService(s).ladder()
        assert ladder is not None
        assert [e.member.passport_id for e in ladder.entries] == [
            "rejected",
            "promoted",
            "observe",
        ]

    def test_none_evidence_ranks_last(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("no_dsr", mean_excess=0.5, dsr=None))
        s.save_passport(passport("no_excess", mean_excess=None, dsr=1.0))
        s.save_passport(passport("plain", mean_excess=0.5, dsr=1.0))
        ladder = StrategyPopulationService(s).ladder()
        assert ladder is not None
        assert [e.member.passport_id for e in ladder.entries] == [
            "plain",
            "no_dsr",
            "no_excess",
        ]

    def test_min_ladder_candidates_validation(self, tmp_path):
        with pytest.raises(ValueError, match="min_ladder_candidates"):
            StrategyPopulationService(store(tmp_path), min_ladder_candidates=0)

    def test_custom_gate(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A"))
        s.save_passport(passport("B"))
        svc = StrategyPopulationService(s, min_ladder_candidates=2)
        registry = svc.registry()
        assert registry.ladder is not None
        assert registry.min_ladder_candidates == 2

    def test_ladder_as_dict(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A", mean_excess=0.5))
        s.save_passport(passport("B", mean_excess=0.2))
        s.save_passport(passport("C", mean_excess=0.3))
        payload = StrategyPopulationService(s).registry().as_dict()
        assert payload["ladder"] is not None
        entries = payload["ladder"]["entries"]
        assert [e["rank"] for e in entries] == [1, 2, 3]
        assert [e["passport_id"] for e in entries] == ["A", "C", "B"]

    def test_deterministic_across_reads(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("A", mean_excess=0.5))
        s.save_passport(passport("B", mean_excess=0.2))
        s.save_passport(passport("C", mean_excess=0.3))
        svc = StrategyPopulationService(s)
        assert svc.registry().as_dict() == svc.registry().as_dict()


def passport_on(
    passport_id: str,
    *,
    dataset_id: str,
    dataset_version: int = 1,
    n_folds: int = 8,
    status: PassportStatus = PassportStatus.CANDIDATE,
    verdict: EvidenceVerdict = EvidenceVerdict.OBSERVE,
) -> StrategyPassport:
    """A passport evaluated on a specific dataset slice (for composition tests)."""
    p = passport(passport_id, n_folds=n_folds, status=status, verdict=verdict)
    return StrategyPassport(
        passport_id=p.passport_id,
        created_at=p.created_at,
        hypothesis=p.hypothesis,
        dataset_id=dataset_id,
        dataset_version=dataset_version,
        features=p.features,
        model=p.model,
        trial_count=p.trial_count,
        evidence=p.evidence,
        verdict=p.verdict,
        status=p.status,
    )


class TestPopulationComposition:
    def test_gated_below_five_real_candidates(self, tmp_path):
        s = store(tmp_path)
        for i in range(4):
            s.save_passport(passport(f"p{i}"))
        composition = StrategyPopulationService(s).composition()
        assert composition.view is None
        assert "requires at least 5 real candidates" in composition.unavailable_reason
        assert composition.min_composition_candidates == 5

    def test_unreal_passports_do_not_count_toward_the_gate(self, tmp_path):
        s = store(tmp_path)
        for i in range(5):
            s.save_passport(passport(f"unreal{i}", n_folds=0))
        composition = StrategyPopulationService(s).composition()
        assert composition.view is None

    def test_environment_breakdown(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("r1", n_folds=0, status=PassportStatus.RESEARCH))
        s.save_passport(passport("c1", status=PassportStatus.CANDIDATE))
        s.save_passport(passport("c2", status=PassportStatus.CANDIDATE))
        s.save_passport(
            passport("pp", status=PassportStatus.PAPER, verdict=EvidenceVerdict.PROMOTE_TO_PAPER)
        )
        s.save_passport(passport("lv", status=PassportStatus.LIVE))
        s.save_passport(passport("rt", status=PassportStatus.RETIRED))
        view = StrategyPopulationService(s).composition().view
        assert view is not None
        assert view.total == 6
        assert view.environment_counts["candidate"] == 2
        assert view.environment_counts["research"] == 1
        assert view.environment_counts["paper"] == 1
        assert view.environment_counts["live"] == 1
        assert view.environment_counts["retired"] == 1
        assert view.environment_counts["canary"] == 0

    def test_verdict_breakdown(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport("rej", verdict=EvidenceVerdict.REJECT))
        s.save_passport(passport("obs1", verdict=EvidenceVerdict.OBSERVE))
        s.save_passport(passport("obs2", verdict=EvidenceVerdict.OBSERVE))
        s.save_passport(passport("pr1", verdict=EvidenceVerdict.PROMOTE_TO_PAPER))
        s.save_passport(passport("pr2", verdict=EvidenceVerdict.PROMOTE_TO_PAPER))
        s.save_passport(passport("pr3", verdict=EvidenceVerdict.PROMOTE_TO_PAPER))
        view = StrategyPopulationService(s).composition().view
        assert view is not None
        assert view.verdict_counts == {
            "reject": 1,
            "observe": 2,
            "promote_to_paper": 3,
        }

    def test_dataset_breakdown_sorted(self, tmp_path):
        s = store(tmp_path)
        s.save_passport(passport_on("a", dataset_id="btcusdt", dataset_version=1))
        s.save_passport(passport_on("b", dataset_id="btcusdt", dataset_version=2))
        s.save_passport(passport_on("c", dataset_id="btcusdt", dataset_version=2))
        s.save_passport(passport_on("d", dataset_id="ethusdt", dataset_version=1))
        s.save_passport(passport_on("e", dataset_id="btcusdt", dataset_version=1))
        s.save_passport(passport_on("f", dataset_id="ethusdt", dataset_version=1))
        view = StrategyPopulationService(s).composition().view
        assert view is not None
        assert [
            (slice_.dataset_id, slice_.dataset_version, slice_.n)
            for slice_ in view.dataset_breakdown
        ] == [
            ("btcusdt", 1, 2),
            ("btcusdt", 2, 2),
            ("ethusdt", 1, 2),
        ]

    def test_composition_includes_unreal_members_in_counts(self, tmp_path):
        s = store(tmp_path)
        for i in range(5):
            s.save_passport(passport(f"p{i}"))
        s.save_passport(passport("unreal", n_folds=0, status=PassportStatus.RESEARCH))
        view = StrategyPopulationService(s).composition().view
        assert view is not None
        assert view.total == 6
        assert view.environment_counts["research"] == 1

    def test_composition_as_dict(self, tmp_path):
        s = store(tmp_path)
        for i in range(5):
            s.save_passport(passport(f"p{i}"))
        payload = StrategyPopulationService(s).composition().as_dict()
        assert payload["view"] is not None
        assert set(payload["view"]) == {
            "total",
            "environment_counts",
            "verdict_counts",
            "dataset_breakdown",
        }
        assert payload["min_composition_candidates"] == 5

    def test_custom_gate(self, tmp_path):
        s = store(tmp_path)
        for i in range(4):
            s.save_passport(passport(f"p{i}"))
        svc = StrategyPopulationService(s, min_composition_candidates=4)
        assert svc.composition().view is not None
        with pytest.raises(ValueError, match="min_composition_candidates"):
            StrategyPopulationService(s, min_composition_candidates=0)
