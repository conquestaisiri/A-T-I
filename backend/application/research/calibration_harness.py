# backend/application/research/calibration_harness.py
"""Live-vs-paper execution calibration harness (task P5-004, Tier-1 #10).

The critique's Tier-1 #10 demands a *systematic* live-vs-paper comparison:
the same orders, executed on a real venue and simulated by the paper fill
model, must agree to within a known tolerance — otherwise the simulator is
mis-calibrated and every research verdict that leans on it is suspect.

What this harness does
----------------------
- **Aligns by order id**: each live :class:`ExecutionReport` is paired with
  its paper twin; a missing twin on either side is recorded as execution
  failure evidence, never silently dropped.
- **Measures per-order deltas**: live slippage vs paper slippage (both
  arrival-based, `ExecutionReport.slippage_bps`), fee deltas, and the
  resulting bias in bps.
- **Classifies the bias**: paper overstates / understates / balanced, against
  an operator-set threshold — so a systematic fill-model error becomes a
  number, not a feeling.
- **Produces the recalibration multiplier**: ``mean_live_slippage /
  mean_paper_slippage``, the single factor to apply to the paper cost model
  so the simulator matches observed execution (the fill-model recalibration
  loop). A paper side with zero slippage produces no multiplier (guard).
- **Writes the report into the strategy passport** (``live_evidence``), so
  live-vs-paper drift becomes part of the auditable strategy lifecycle and
  feeds the rollback requirements (execution failure triggers). The write
  happens through ``EvidenceEngine.record_calibration``; this harness stays
  a pure comparison over domain records: it imports only domain contracts,
  has no I/O, and replays deterministically from the ledger or gateway
  captures.
"""

from __future__ import annotations

import enum
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from backend.domain.execution.execution_report import ExecutionReport


@dataclass(frozen=True, slots=True)
class OrderComparison:
    """One order, live vs paper, with its execution deltas."""

    order_id: str
    symbol: str
    quantity: float
    arrival_price: float
    live_fill_price: float
    paper_fill_price: float
    live_slippage_bps: float
    paper_slippage_bps: float
    delta_bps: float  # live - paper
    live_fee: float
    paper_fee: float
    fee_delta: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "quantity": self.quantity,
            "arrival_price": self.arrival_price,
            "live_fill_price": self.live_fill_price,
            "paper_fill_price": self.paper_fill_price,
            "live_slippage_bps": round(self.live_slippage_bps, 6),
            "paper_slippage_bps": round(self.paper_slippage_bps, 6),
            "delta_bps": round(self.delta_bps, 6),
            "live_fee": round(self.live_fee, 6),
            "paper_fee": round(self.paper_fee, 6),
            "fee_delta": round(self.fee_delta, 6),
        }


class BiasClassification(enum.StrEnum):
    """What the aggregate delta says about the paper fill model."""

    PAPER_UNDERSTATES = "paper_understates_costs"
    PAPER_OVERSTATES = "paper_overstates_costs"
    BALANCED = "balanced"


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    """The systematic live-vs-paper comparison scorecard.

    Attributes
    ----------
    symbol: str
        Symbol compared.
    n_orders: int
        Orders matched on both sides.
    missing_live: int
        Paper orders with no live twin (simulated fills that never reached a
        venue — fabricated executions).
    missing_paper: int
        Live orders with no paper twin (fill model failed to represent an
        executed order).
    mean_live_slippage_bps, mean_paper_slippage_bps: float
        Mean arrival slippage per side (bps).
    mean_delta_bps, median_delta_bps: float
        Mean/median of (live - paper) slippage per order.
    sign_consistency_rate: float
        Fraction of matched orders where live and paper slippage share a
        sign — how often the model gets the direction of cost right.
    bias: BiasClassification
        Classification of the mean delta against ``bias_threshold_bps``.
    bias_threshold_bps: float
        The tolerance used for the classification.
    cost_multiplier: float | None
        Mean live slippage / mean paper slippage — the factor to apply to
        the paper cost model; None when the paper side shows no slippage.
    window_start, window_end: str
        ISO-8601 range of the matched orders' execution timestamps.
    """

    symbol: str
    n_orders: int
    missing_live: int
    missing_paper: int
    mean_live_slippage_bps: float
    mean_paper_slippage_bps: float
    mean_delta_bps: float
    median_delta_bps: float
    sign_consistency_rate: float
    bias: BiasClassification
    bias_threshold_bps: float
    cost_multiplier: float | None
    window_start: str
    window_end: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "n_orders": self.n_orders,
            "missing_live": self.missing_live,
            "missing_paper": self.missing_paper,
            "mean_live_slippage_bps": round(self.mean_live_slippage_bps, 6),
            "mean_paper_slippage_bps": round(self.mean_paper_slippage_bps, 6),
            "mean_delta_bps": round(self.mean_delta_bps, 6),
            "median_delta_bps": round(self.median_delta_bps, 6),
            "sign_consistency_rate": round(self.sign_consistency_rate, 6),
            "bias": self.bias.value,
            "bias_threshold_bps": self.bias_threshold_bps,
            "cost_multiplier": (
                round(self.cost_multiplier, 6) if self.cost_multiplier is not None else None
            ),
            "window_start": self.window_start,
            "window_end": self.window_end,
        }


class CalibrationHarness:
    """Compare live and paper execution reports order by order.

    Parameters
    ----------
    bias_threshold_bps: float
        Tolerance for bias classification: a mean delta within +/- this
        tolerance is "balanced".
    """

    def __init__(self, bias_threshold_bps: float = 1.0) -> None:
        if bias_threshold_bps < 0.0:
            raise ValueError("bias_threshold_bps cannot be negative")
        self._bias_threshold_bps = bias_threshold_bps

    def compare(
        self,
        live: Sequence[ExecutionReport],
        paper: Sequence[ExecutionReport],
    ) -> CalibrationReport:
        """Compare two report sequences matched by order id.

        Both sequences must be non-empty; reports on either side whose twin
        is missing are counted, not dropped. Slippage uses the arrival-based
        ``ExecutionReport.slippage_bps`` (absolute, per house semantics).
        """
        if not live or not paper:
            raise ValueError("calibration requires live and paper execution reports")
        live_by_id = {r.order_id: r for r in live}
        paper_by_id = {r.order_id: r for r in paper}
        symbols = {r.symbol for r in live} | {r.symbol for r in paper}
        if len(symbols) != 1:
            raise ValueError(f"calibration requires one symbol, got {sorted(symbols)}")

        comparisons: list[OrderComparison] = []
        missing_live = 0
        missing_paper = 0
        for order_id, live_report in live_by_id.items():
            paper_report = paper_by_id.get(order_id)
            if paper_report is None:
                missing_paper += 1
                continue
            comparisons.append(_compare(live_report, paper_report))
        missing_live = sum(1 for order_id in paper_by_id if order_id not in live_by_id)

        if not comparisons:
            raise ValueError("no matched live/paper order pairs to compare")

        live_slips = [c.live_slippage_bps for c in comparisons]
        paper_slips = [c.paper_slippage_bps for c in comparisons]
        deltas = [c.delta_bps for c in comparisons]
        mean_live = statistics.fmean(live_slips)
        mean_paper = statistics.fmean(paper_slips)
        mean_delta = statistics.fmean(deltas)
        median_delta = statistics.median(deltas)
        consistent = sum(
            1
            for c in comparisons
            if (c.live_slippage_bps > 0 and c.paper_slippage_bps > 0)
            or (c.live_slippage_bps == 0 and c.paper_slippage_bps == 0)
            or (c.live_slippage_bps < 0 and c.paper_slippage_bps < 0)
        )
        sign_consistency = consistent / len(comparisons)
        bias = _classify_bias(mean_delta, self._bias_threshold_bps)
        multiplier = mean_live / mean_paper if mean_paper > 0.0 else None
        executed_at = [
            t for order_id in live_by_id if (t := live_by_id[order_id].executed_at) is not None
        ]
        window_start = min(executed_at).isoformat(timespec="seconds") if executed_at else ""
        window_end = max(executed_at).isoformat(timespec="seconds") if executed_at else ""

        return CalibrationReport(
            symbol=sorted(symbols)[0],
            n_orders=len(comparisons),
            missing_live=missing_live,
            missing_paper=missing_paper,
            mean_live_slippage_bps=mean_live,
            mean_paper_slippage_bps=mean_paper,
            mean_delta_bps=mean_delta,
            median_delta_bps=median_delta,
            sign_consistency_rate=sign_consistency,
            bias=bias,
            bias_threshold_bps=self._bias_threshold_bps,
            cost_multiplier=multiplier,
            window_start=window_start,
            window_end=window_end,
        )

    def recalibrated_impact_bps(
        self,
        report: CalibrationReport,
        base_impact_bps: float,
    ) -> float:
        """The impact parameter the paper model should use next.

        ``base_impact_bps * cost_multiplier`` when a multiplier exists
        (paper slippage was observed); otherwise the base is returned
        unchanged (nothing to recalibrate against).
        """
        if base_impact_bps < 0.0:
            raise ValueError("base_impact_bps cannot be negative")
        if report.cost_multiplier is None:
            return base_impact_bps
        return base_impact_bps * report.cost_multiplier


def _compare(live: ExecutionReport, paper: ExecutionReport) -> OrderComparison:
    if live.arrival_price is None or paper.arrival_price is None:
        raise ValueError(
            f"order {live.order_id!r}: calibration requires arrival prices on "
            "both live and paper reports"
        )
    live_slip = _slippage(live)
    paper_slip = _slippage(paper)
    return OrderComparison(
        order_id=live.order_id,
        symbol=live.symbol,
        quantity=live.quantity,
        arrival_price=live.arrival_price,
        live_fill_price=live.average_fill_price,
        paper_fill_price=paper.average_fill_price,
        live_slippage_bps=live_slip,
        paper_slippage_bps=paper_slip,
        delta_bps=live_slip - paper_slip,
        live_fee=live.fee or 0.0,
        paper_fee=paper.fee or 0.0,
        fee_delta=(live.fee or 0.0) - (paper.fee or 0.0),
    )


def _slippage(report: ExecutionReport) -> float:
    value = report.slippage_bps
    if value is None:
        raise ValueError(f"order {report.order_id!r}: slippage_bps could not be computed")
    return float(value)


def _classify_bias(mean_delta_bps: float, threshold: float) -> BiasClassification:
    if mean_delta_bps > threshold:
        return BiasClassification.PAPER_UNDERSTATES
    if mean_delta_bps < -threshold:
        return BiasClassification.PAPER_OVERSTATES
    return BiasClassification.BALANCED
