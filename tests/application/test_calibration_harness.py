"""Tests for the live-vs-paper calibration harness (P5-004).

The harness must pair live and paper execution reports by order id, measure
per-order slippage deltas (both arrival-based), classify the fill model's
bias against a tolerance, produce the recalibration multiplier, and refuse
to compare across symbols or with empty inputs. Missing twins are execution
failure evidence, counted not dropped.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.application.research.baseline_evaluation import EvaluationCosts
from backend.application.research.calibration_harness import (
    BiasClassification,
    CalibrationHarness,
)
from backend.application.research.decision_pipeline_evaluator import OutOfSampleReport
from backend.application.research.evidence_engine import EvidenceEngine
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderSide, OrderStatus
from backend.domain.research.oos_evaluation import PooledEvidence
from backend.domain.research.passport import PassportStatus
from backend.infrastructure.sqlite.database import Database
from backend.infrastructure.sqlite.passport_repository import SqlitePassportRepository

T0 = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


def engine(tmp_path) -> EvidenceEngine:
    return EvidenceEngine(SqlitePassportRepository(Database(tmp_path / "p.db")))


def report_with(pooled: PooledEvidence) -> OutOfSampleReport:
    return OutOfSampleReport(
        symbol="btcusdt",
        costs=EvaluationCosts(half_spread_pct=0.0002, taker_fee_pct=0.0004),
        cv_spec={"train_size": 80, "test_size": 20, "expanding": True},
        folds=(),
        pooled=pooled,
    )


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


def base_kwargs() -> dict[str, Any]:
    return dict(
        hypothesis="OFI predicts 1-minute return",
        dataset_id="btcusdt",
        dataset_version=1,
        features=("ofi", "spread"),
        model="RuleBasedSolver",
        trial_count=50,
        train_period=("2021-01-01", "2024-12-31"),
        validation_period=("2025-01-01", "2025-12-31"),
        test_period=("2026-01-01", "2026-06-30"),
        experiment_id="EXP-1",
    )


def live_report(
    order_id: str,
    fill_price: float,
    *,
    arrival_price: float = 100.0,
    fee: float = 0.5,
    executed_at: datetime = T0,
) -> ExecutionReport:
    return ExecutionReport(
        order_id=order_id,
        symbol="btcusdt",
        side=OrderSide.BUY,
        quantity=1.0,
        average_fill_price=fill_price,
        status=OrderStatus.FILLED,
        executed_at=executed_at,
        fee=fee,
        venue="binance",
        arrival_price=arrival_price,
    )


def paper_report(order_id: str, fill_price: float, *, fee: float = 0.4) -> ExecutionReport:
    return ExecutionReport(
        order_id=order_id,
        symbol="btcusdt",
        side=OrderSide.BUY,
        quantity=1.0,
        average_fill_price=fill_price,
        status=OrderStatus.FILLED,
        executed_at=T0,
        fee=fee,
        venue=None,
        arrival_price=100.0,
    )


def bps(fill_price: float) -> float:
    return (fill_price - 100.0) / 100.0 * 10_000


def test_perfect_calibration_is_balanced():
    report = CalibrationHarness(bias_threshold_bps=1.0).compare(
        live=[live_report("o1", 100.1), live_report("o2", 100.2)],
        paper=[paper_report("o1", 100.1), paper_report("o2", 100.2)],
    )
    assert report.symbol == "btcusdt"
    assert report.n_orders == 2
    assert report.missing_live == 0
    assert report.missing_paper == 0
    assert report.mean_delta_bps == pytest.approx(0.0)
    assert report.sign_consistency_rate == pytest.approx(1.0)
    assert report.bias is BiasClassification.BALANCED
    assert report.cost_multiplier == pytest.approx(1.0)
    assert report.window_start == "2026-08-13T10:00:00+00:00"


def test_paper_understates_costs():
    report = CalibrationHarness(bias_threshold_bps=1.0).compare(
        live=[live_report("o1", 100.1), live_report("o2", 100.2)],
        paper=[paper_report("o1", 100.05), paper_report("o2", 100.1)],
    )
    assert report.mean_live_slippage_bps == pytest.approx(bps(100.15))
    assert report.mean_paper_slippage_bps == pytest.approx(bps(100.075))
    assert report.mean_delta_bps == pytest.approx(7.5)
    assert report.bias is BiasClassification.PAPER_UNDERSTATES
    assert report.cost_multiplier == pytest.approx(2.0)


def test_paper_overstates_costs():
    report = CalibrationHarness(bias_threshold_bps=1.0).compare(
        live=[live_report("o1", 100.02)],
        paper=[paper_report("o1", 100.1)],
    )
    assert report.bias is BiasClassification.PAPER_OVERSTATES
    assert report.cost_multiplier == pytest.approx(0.2)


def test_within_tolerance_is_balanced():
    report = CalibrationHarness(bias_threshold_bps=1.0).compare(
        live=[live_report("o1", 100.1)],
        paper=[paper_report("o1", 100.095)],  # 10.0 bps vs 9.5 bps: delta 0.5
    )
    assert report.mean_delta_bps == pytest.approx(0.5)
    assert report.bias is BiasClassification.BALANCED


def test_sign_consistency_rate_mixed_signs():
    # live favourable (negative slippage) twice, paper positive: signs diverge.
    sell_low = ExecutionReport(
        order_id="o1",
        symbol="btcusdt",
        side=OrderSide.SELL,
        quantity=1.0,
        average_fill_price=100.05,
        status=OrderStatus.FILLED,
        executed_at=T0,
        arrival_price=100.0,
    )
    report = CalibrationHarness(bias_threshold_bps=1.0).compare(
        live=[live_report("o1", 100.05)],
        paper=[sell_low],
    )
    assert report.sign_consistency_rate == pytest.approx(0.0)


def test_missing_twins_are_counted_not_dropped():
    report = CalibrationHarness().compare(
        live=[
            live_report("o1", 100.1),
            live_report("o2", 100.2),
            live_report("o3", 100.3),
        ],
        paper=[paper_report("o1", 100.1), paper_report("oX", 100.1)],
    )
    assert report.n_orders == 1
    assert report.missing_paper == 2  # o2, o3 had no paper twin
    assert report.missing_live == 1  # oX had no live twin


def test_multi_symbol_comparison_rejected():
    foreign = ExecutionReport(
        order_id="o1",
        symbol="ethusdt",
        side=OrderSide.BUY,
        quantity=1.0,
        average_fill_price=100.1,
        status=OrderStatus.FILLED,
        executed_at=T0,
        arrival_price=100.0,
    )
    with pytest.raises(ValueError, match="one symbol"):
        CalibrationHarness().compare(live=[live_report("o1", 100.1)], paper=[foreign])


def test_empty_sequences_rejected():
    with pytest.raises(ValueError, match="live and paper"):
        CalibrationHarness().compare(live=[], paper=[paper_report("o1", 100.1)])
    with pytest.raises(ValueError, match="live and paper"):
        CalibrationHarness().compare(live=[live_report("o1", 100.1)], paper=[])


def test_no_matched_pairs_rejected():
    with pytest.raises(ValueError, match="no matched"):
        CalibrationHarness().compare(
            live=[live_report("o1", 100.1)],
            paper=[paper_report("oX", 100.1)],
        )


def test_missing_arrival_price_rejected():
    no_arrival = ExecutionReport(
        order_id="o1",
        symbol="btcusdt",
        side=OrderSide.BUY,
        quantity=1.0,
        average_fill_price=100.1,
        status=OrderStatus.FILLED,
        executed_at=T0,
    )
    with pytest.raises(ValueError, match="arrival price"):
        CalibrationHarness().compare(live=[live_report("o1", 100.1)], paper=[no_arrival])


def test_recalibration_scales_impact():
    report = CalibrationHarness().compare(
        live=[live_report("o1", 100.1), live_report("o2", 100.2)],
        paper=[paper_report("o1", 100.05), paper_report("o2", 100.1)],
    )
    harness = CalibrationHarness()
    assert harness.recalibrated_impact_bps(report, base_impact_bps=4.0) == pytest.approx(8.0)


def test_recalibration_without_paper_slippage_keeps_base():
    report = CalibrationHarness().compare(
        live=[live_report("o1", 100.1)],
        paper=[paper_report("o1", 100.0)],  # paper sees no slippage
    )
    assert report.cost_multiplier is None
    assert CalibrationHarness().recalibrated_impact_bps(report, base_impact_bps=4.0) == 4.0


def test_negative_base_impact_rejected():
    report = CalibrationHarness().compare(
        live=[live_report("o1", 100.1)],
        paper=[paper_report("o1", 100.05)],
    )
    with pytest.raises(ValueError, match="base_impact_bps"):
        CalibrationHarness().recalibrated_impact_bps(report, base_impact_bps=-1.0)


def test_negative_threshold_rejected():
    with pytest.raises(ValueError, match="bias_threshold_bps"):
        CalibrationHarness(bias_threshold_bps=-0.1)


def test_report_as_dict_round_trips_values():
    report = CalibrationHarness().compare(
        live=[live_report("o1", 100.1)],
        paper=[paper_report("o1", 100.095)],
    )
    data = report.as_dict()
    assert data["symbol"] == "btcusdt"
    assert data["n_orders"] == 1
    assert data["bias"] == "balanced"
    assert data["cost_multiplier"] == pytest.approx(10.0 / 9.5)


def test_calibration_attaches_to_passport(tmp_path):
    svc = engine(tmp_path)
    svc.issue_passport(
        passport_id="STRAT-000184",
        **base_kwargs(),
        report=report_with(good_evidence()),
        now=T0,
    )
    harness = CalibrationHarness()
    report = harness.compare(
        live=[live_report("o1", 100.1)],
        paper=[paper_report("o1", 100.095)],
    )
    updated = svc.record_calibration(
        "STRAT-000184",
        report=report.as_dict(),
        reason="first weekly live-vs-paper window",
        now=T0,
    )
    assert updated.live_evidence["calibration"]["n_orders"] == 1
    assert updated.live_evidence["calibration"]["bias"] == "balanced"
    assert updated.status is PassportStatus.CANDIDATE
    events = svc.lifecycle("STRAT-000184")
    assert events[-1].event_type == "calibration_update"
    assert events[-1].reason == "first weekly live-vs-paper window"


def test_calibration_requires_known_passport(tmp_path):
    svc = engine(tmp_path)
    report = CalibrationHarness().compare(
        live=[live_report("o1", 100.1)],
        paper=[paper_report("o1", 100.05)],
    )
    with pytest.raises(ValueError, match="unknown passport"):
        svc.record_calibration("STRAT-999", report=report.as_dict(), reason="x")
