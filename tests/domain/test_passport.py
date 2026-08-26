"""Tests for the strategy passport contracts (P5-003a) and evidence verdict.

The verdict gates must be conservative and explicit: the evidence engine can
never promote past paper, must reject on any failed gate with the failing
number in the reason, and must observe (not decide) when evidence is
insufficient. Every rule is unit-tested here so the death system and
promotion ladder rest on proven logic.
"""

from __future__ import annotations

from datetime import UTC, datetime

from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
    verdict_for_evidence,
)
from backend.domain.research.pbo import PboResult


def good_evidence() -> PooledEvidence:
    return PooledEvidence(
        n_folds=8,
        total_test_bars=160,
        total_trades=40,
        total_wins=22,
        total_losses=18,
        total_fees=12.0,
        total_slippage_bps=3.5,
        gross_profit=250.0,
        gross_loss=180.0,
        mean_return_pct=1.2,
        median_return_pct=0.9,
        mean_excess_return_pct=0.7,
        positive_fold_rate=0.75,
        beats_buy_and_hold_rate=0.75,
        mean_max_drawdown_pct=-8.0,
        deflated_sharpe=1.1,
        reasoner="RuleBasedSolver",
        cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
    )


def pbo(prob: float) -> PboResult:
    return PboResult(
        pbo=prob,
        mean_logit=0.0,
        n_trials=50,
        n_observations=160,
        n_splits=100,
        n_selected=25,
        metric="mean",
        seed=42,
    )


class TestVerdictGates:
    def test_promotes_to_paper_when_all_gates_pass(self):
        verdict = verdict_for_evidence(good_evidence())
        assert verdict.verdict is EvidenceVerdict.PROMOTE_TO_PAPER
        assert verdict.reasons

    def test_rejects_negative_deflated_sharpe(self):
        evidence = good_evidence()
        evidence = _with(evidence, deflated_sharpe=-0.4)
        verdict = verdict_for_evidence(evidence)
        assert verdict.verdict is EvidenceVerdict.REJECT
        assert any("deflated Sharpe" in r for r in verdict.reasons)

    def test_rejects_zero_deflated_sharpe(self):
        verdict = verdict_for_evidence(_with(good_evidence(), deflated_sharpe=0.0))
        assert verdict.verdict is EvidenceVerdict.REJECT

    def test_rejects_high_pbo_before_any_other_gate(self):
        evidence = good_evidence()
        verdict = verdict_for_evidence(evidence, pbo=pbo(0.9))
        assert verdict.verdict is EvidenceVerdict.REJECT
        assert any("PBO" in r for r in verdict.reasons)

    def test_accepts_low_pbo(self):
        verdict = verdict_for_evidence(good_evidence(), pbo=pbo(0.2))
        assert verdict.verdict is EvidenceVerdict.PROMOTE_TO_PAPER

    def test_rejects_low_positive_fold_rate(self):
        verdict = verdict_for_evidence(_with(good_evidence(), positive_fold_rate=0.4))
        assert verdict.verdict is EvidenceVerdict.REJECT
        assert any("positive-fold" in r for r in verdict.reasons)

    def test_rejects_low_beats_buy_and_hold_rate(self):
        verdict = verdict_for_evidence(_with(good_evidence(), beats_buy_and_hold_rate=0.3))
        assert verdict.verdict is EvidenceVerdict.REJECT
        assert any("buy-and-hold" in r for r in verdict.reasons)

    def test_rejects_excessive_drawdown(self):
        verdict = verdict_for_evidence(_with(good_evidence(), mean_max_drawdown_pct=-40.0))
        assert verdict.verdict is EvidenceVerdict.REJECT
        assert any("drawdown" in r for r in verdict.reasons)

    def test_observes_when_deflated_sharpe_unavailable(self):
        verdict = verdict_for_evidence(_with(good_evidence(), deflated_sharpe=None))
        assert verdict.verdict is EvidenceVerdict.OBSERVE
        assert any("insufficient" in r for r in verdict.reasons)

    def test_rejects_when_only_weak_evidence_without_dsr(self):
        verdict = verdict_for_evidence(
            _with(good_evidence(), deflated_sharpe=None, positive_fold_rate=0.3)
        )
        # The DSR-missing gate fires first: never promote on missing evidence.
        assert verdict.verdict is EvidenceVerdict.OBSERVE

    def test_gates_are_configurable(self):
        evidence = _with(good_evidence(), positive_fold_rate=0.4)
        assert verdict_for_evidence(evidence).verdict is EvidenceVerdict.REJECT
        assert (
            verdict_for_evidence(evidence, min_positive_fold_rate=0.3).verdict
            is EvidenceVerdict.PROMOTE_TO_PAPER
        )


class TestPassportRecord:
    def test_round_trip(self):
        passport = StrategyPassport(
            passport_id="STRAT-000184",
            created_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
            hypothesis="OFI predicts 1-minute return in bullish regimes",
            dataset_id="btcusdt",
            dataset_version=1,
            features=("ofi", "spread", "volatility"),
            model="RuleBasedSolver",
            trial_count=1284,
            train_period=("2021-01-01", "2024-12-31"),
            validation_period=("2025-01-01", "2025-12-31"),
            test_period=("2026-01-01", "2026-06-30"),
            cost_model={"half_spread_pct": 0.0002},
            evidence={"pooled": good_evidence().as_dict()},
            verdict=PassportVerdict(EvidenceVerdict.PROMOTE_TO_PAPER, ("passed",)),
            status=PassportStatus.CANDIDATE,
            experiment_id="EXP-1",
        )
        restored = StrategyPassport.from_dict(passport.as_dict())
        assert restored == passport

    def test_defaults_are_conservative(self):
        passport = StrategyPassport(
            passport_id="S1",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            hypothesis="h",
            dataset_id="d",
            dataset_version=1,
            features=(),
            model="m",
            trial_count=1,
        )
        assert passport.status is PassportStatus.RESEARCH
        assert passport.verdict.verdict is EvidenceVerdict.OBSERVE
        assert passport.experiment_id is None

    def test_verdict_round_trip(self):
        verdict = PassportVerdict(EvidenceVerdict.REJECT, ("PBO 0.90 exceeds 0.50",))
        data = verdict.as_dict()
        restored = PassportVerdict(
            verdict=EvidenceVerdict(str(data["verdict"])),
            reasons=tuple(str(r) for r in data["reasons"]),
        )
        assert restored == verdict


def _with(evidence: PooledEvidence, **changes) -> PooledEvidence:
    import dataclasses

    return dataclasses.replace(evidence, **changes)
