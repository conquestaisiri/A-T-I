"""Tests for the evidence report writer (P5-003f) and bootstrap wiring."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from backend.application.context.bootstrap import build_evidence_engine
from backend.application.research.evidence_report import EvidenceReportWriter
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportLifecycleEvent,
    PassportStatus,
    PassportVerdict,
    StrategyPassport,
)


def passport() -> StrategyPassport:
    return StrategyPassport(
        passport_id="STRAT-1",
        created_at=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        hypothesis="h",
        dataset_id="btcusdt",
        dataset_version=1,
        features=("ofi",),
        model="RuleBasedSolver",
        trial_count=10,
        evidence={"pooled": {"n_folds": 8}},
        verdict=PassportVerdict(EvidenceVerdict.PROMOTE_TO_PAPER, ("passed",)),
        status=PassportStatus.CANDIDATE,
        last_review=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
    )


class TestEvidenceReportWriter:
    def test_writes_full_provenance_report(self, tmp_path):
        writer = EvidenceReportWriter(tmp_path / "reports")
        lifecycle = (
            PassportLifecycleEvent(
                passport_id="STRAT-1",
                event_type="status_change",
                occurred_at=datetime(2026, 8, 14, tzinfo=UTC),
                from_status=PassportStatus.CANDIDATE,
                to_status=PassportStatus.PAPER,
                reason="approved",
            ),
        )
        path = writer.write(
            passport(),
            lifecycle,
            generated_at=datetime(2026, 8, 15, tzinfo=UTC),
        )
        assert path.name == "STRAT-1.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["report_type"] == "strategy_passport"
        assert payload["passport"]["passport_id"] == "STRAT-1"
        assert payload["lifecycle"][0]["to_status"] == "paper"
        assert payload["passport"]["evidence"]["pooled"]["n_folds"] == 8

    def test_refuses_to_overwrite_existing_report(self, tmp_path):
        writer = EvidenceReportWriter(tmp_path / "reports")
        writer.write(passport())
        with pytest.raises(ValueError, match="append-only"):
            writer.write(passport())


class TestEvidenceEngineBootstrap:
    def test_build_evidence_engine_wires_store(self, tmp_path):
        engine = build_evidence_engine(tmp_path / "b.db")
        engine.issue_passport(
            passport_id="STRAT-B",
            hypothesis="h",
            dataset_id="btcusdt",
            dataset_version=1,
            features=("ofi",),
            model="RuleBasedSolver",
            trial_count=1,
            report=_report(),
            now=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        )
        stored = engine.passport("STRAT-B")
        assert stored is not None
        assert stored.verdict.verdict is EvidenceVerdict.OBSERVE


def _report():
    from backend.application.research.baseline_evaluation import EvaluationCosts
    from backend.application.research.decision_pipeline_evaluator import (
        OutOfSampleReport,
    )
    from backend.domain.research.oos_evaluation import PooledEvidence

    pooled = PooledEvidence(
        n_folds=3,
        total_test_bars=60,
        total_trades=0,
        total_wins=0,
        total_losses=0,
        total_fees=0.0,
        total_slippage_bps=0.0,
        gross_profit=0.0,
        gross_loss=0.0,
        mean_return_pct=0.5,
        median_return_pct=0.5,
        mean_excess_return_pct=0.2,
        positive_fold_rate=1.0,
        beats_buy_and_hold_rate=1.0,
        mean_max_drawdown_pct=-2.0,
        deflated_sharpe=None,
        reasoner="RuleBasedSolver",
        cost_model={"half_spread_pct": 0.0002, "taker_fee_pct": 0.0004},
    )
    return OutOfSampleReport(
        symbol="btcusdt",
        costs=EvaluationCosts(half_spread_pct=0.0002, taker_fee_pct=0.0004),
        cv_spec={"train_size": 80, "test_size": 20},
        folds=(),
        pooled=pooled,
    )
