"""Reasoner ablation harness tests (P5-007).

The harness must (1) measure every variant against one baseline on identical
OOS folds, (2) refuse improvement claims without a Deflated Sharpe, (3) kill
clear losers, (4) report mixed evidence as inconclusive, and (5) never
promote anything itself.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.research.reasoner_ablation import (
    AblationReport,
    ContributionVerdict,
    ReasonerAblation,
    default_ablation_factories,
)
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.decision.proposal import (
    DecisionProposal,
    EvidenceItem,
    Hypothesis,
    ProposedAction,
    ProposedActionType,
)
from backend.domain.decision.trade_plan import PostTradePlan, bracket_plan
from backend.domain.observation.event import ObservationEvent, ObservationEventType

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def make_market(
    seed: int,
    n: int = 300,
    *,
    drift_up: float = 0.003,
    drift_down: float = -0.003,
    vol: float = 0.001,
) -> list[ObservationEvent]:
    """Seeded two-regime synthetic trade series (up leg, then down leg)."""
    rng = random.Random(seed)
    price = 100.0
    events: list[ObservationEvent] = []
    t = T0
    for i in range(n):
        drift = drift_up if i < n // 2 else drift_down
        price *= 1.0 + rng.gauss(drift, vol)
        events.append(
            ObservationEvent(
                source_id="synthetic",
                source_name="Synthetic",
                event_type=ObservationEventType.TRADE,
                timestamp=t,
                payload={
                    "symbol": "btcusdt",
                    "trade_id": i,
                    "price": round(price, 4),
                    "quantity": 1.0,
                },
            )
        )
        t += timedelta(seconds=5)
    return events


def fixed_bias_factory(
    action_type: ProposedActionType, size_fraction: float = 0.5
) -> Callable[[Sequence, Sequence[float]], AIReasoner]:
    """A plan-carrying always-on reasoner: full control over direction."""

    class FixedBias(AIReasoner):
        def __init__(self, replay_steps: Sequence, marks: Sequence[float]) -> None:
            pass

        def reason(self, context, risk_context) -> DecisionProposal:
            action = ProposedAction(
                action_type=action_type,
                size_fraction=size_fraction,
                order=1,
                rationale="fixed bias test cell",
            )
            plan = bracket_plan(0.02)
            return DecisionProposal(
                proposal_id=(
                    f"bias-{context.snapshot.symbol}-"
                    f"{context.created_at.isoformat(timespec='milliseconds')}"
                ),
                correlation_id=context.snapshot.symbol,
                created_at=context.created_at,
                symbol=context.snapshot.symbol,
                hypothesis=Hypothesis(
                    statement="fixed bias test cell",
                    supporting_evidence=(EvidenceItem(source="test", summary="test", value=1),),
                    opposing_evidence=(),
                ),
                confidence=0.9,
                uncertainty="test",
                actions=(action,),
                risk_context=risk_context,
                alternatives=(),
                rationale="test",
                pre_trade_plan=plan,
                post_trade_plan=PostTradePlan(),
            )

    def factory(train_steps: Sequence, train_prices: Sequence[float]) -> AIReasoner:
        return FixedBias(train_steps, train_prices)

    return factory


def ablation(**overrides: Any) -> ReasonerAblation:
    fields: dict[str, Any] = dict(
        cv=WalkForwardCV(train_size=50, test_size=15),
        starting_equity=100_000.0,
    )
    fields.update(overrides)
    return ReasonerAblation(**fields)


class TestReasonerAblationValidation:
    def test_baseline_not_in_family_rejected(self):
        with pytest.raises(ValueError, match="baseline"):
            ablation().run(
                make_market(seed=7),
                variants=default_ablation_factories(),
                baseline_name="missing",
            )

    def test_single_variant_rejected(self):
        with pytest.raises(ValueError, match="at least one variant"):
            ablation().run(
                make_market(seed=7),
                variants={"only": fixed_bias_factory(ProposedActionType.ENTER_LONG)},
                baseline_name="only",
            )

    def test_threshold_must_be_in_open_unit_interval(self):
        with pytest.raises(ValueError):
            ablation(paired_beat_threshold=0.0)
        with pytest.raises(ValueError):
            ablation(paired_beat_threshold=1.5)

    def test_empty_events_rejected(self):
        with pytest.raises(ValueError):
            ablation().run([], variants=default_ablation_factories(), baseline_name="rules_only")


class TestReasonerAblationVerdicts:
    def test_aligned_cell_improves_over_countertrend_baseline(self):
        family: Mapping[str, Callable] = {
            "countertrend": fixed_bias_factory(ProposedActionType.ENTER_SHORT),
            "aligned": fixed_bias_factory(ProposedActionType.ENTER_LONG),
        }
        report = ablation().run(
            make_market(seed=7, drift_down=0.003),
            variants=family,
            baseline_name="countertrend",
        )
        by_name = {v.name: v for v in report.variants}
        assert by_name["countertrend"].verdict is ContributionVerdict.INCONCLUSIVE
        assert by_name["countertrend"].delta_mean_excess_pct == 0.0
        assert by_name["countertrend"].paired_beat_rate == 0.5
        aligned = by_name["aligned"]
        assert aligned.verdict is ContributionVerdict.IMPROVES
        assert aligned.delta_mean_excess_pct > 0.0
        assert aligned.delta_positive_fold_rate >= 0.0
        assert aligned.paired_beat_rate >= 0.5
        assert aligned.delta_deflated_sharpe is not None
        assert aligned.delta_deflated_sharpe > 0.0
        assert report.keep == ("aligned",)

    def test_countertrend_cell_degrades_against_aligned_baseline(self):
        family: Mapping[str, Callable] = {
            "countertrend": fixed_bias_factory(ProposedActionType.ENTER_SHORT),
            "aligned": fixed_bias_factory(ProposedActionType.ENTER_LONG),
        }
        report = ablation().run(
            make_market(seed=7, drift_down=0.003),
            variants=family,
            baseline_name="aligned",
        )
        by_name = {v.name: v for v in report.variants}
        assert by_name["countertrend"].verdict is ContributionVerdict.DEGRADES
        assert by_name["countertrend"].delta_mean_excess_pct < 0.0
        assert by_name["countertrend"].paired_beat_rate < 0.5
        assert report.keep == ()

    def test_twin_cells_are_inconclusive(self):
        factory = fixed_bias_factory(ProposedActionType.ENTER_LONG)
        report = ablation().run(
            make_market(seed=7),
            variants={"a": factory, "b": factory},
            baseline_name="a",
        )
        by_name = {v.name: v for v in report.variants}
        twin = by_name["b"]
        assert twin.verdict is ContributionVerdict.INCONCLUSIVE
        assert twin.delta_mean_excess_pct == 0.0
        assert twin.paired_beat_rate == 0.0
        assert any("evidence is mixed" in reason for reason in twin.reasons)
        assert report.keep == ()

    def test_improvement_refused_when_baseline_dsr_unavailable(self):
        family: Mapping[str, Callable] = {
            "flat": fixed_bias_factory(ProposedActionType.STAND_ASIDE),
            "aligned": fixed_bias_factory(ProposedActionType.ENTER_LONG),
        }
        report = ablation().run(
            make_market(seed=7, drift_down=0.003),
            variants=family,
            baseline_name="flat",
        )
        aligned = next(v for v in report.variants if v.name == "aligned")
        assert aligned.delta_mean_excess_pct > 0.0
        assert aligned.delta_positive_fold_rate > 0.0
        assert aligned.paired_beat_rate >= 0.5
        assert aligned.delta_deflated_sharpe is None
        assert aligned.verdict is ContributionVerdict.INCONCLUSIVE
        assert any("deflated Sharpe unavailable" in reason for reason in aligned.reasons)
        assert report.keep == ()

    def test_default_family_runs_and_is_self_consistent(self):
        report = ablation().run(
            make_market(seed=7),
            variants=default_ablation_factories(),
            baseline_name="rules_only",
        )
        by_name = {v.name: v for v in report.variants}
        assert set(by_name) == {"rules_only", "quant_only"}
        baseline = by_name["rules_only"]
        assert baseline.verdict is ContributionVerdict.INCONCLUSIVE
        assert baseline.delta_mean_excess_pct == 0.0
        assert baseline.delta_positive_fold_rate == 0.0
        assert baseline.delta_deflated_sharpe == 0.0
        assert baseline.paired_beat_rate == 0.5
        assert baseline.reasons == ("baseline reference (not measured against itself)",)

        quant = by_name["quant_only"]
        b_pooled = baseline.report.pooled
        q_pooled = quant.report.pooled
        assert quant.delta_mean_excess_pct == pytest.approx(
            q_pooled.mean_excess_return_pct - b_pooled.mean_excess_return_pct
        )
        assert quant.delta_positive_fold_rate == pytest.approx(
            q_pooled.positive_fold_rate - b_pooled.positive_fold_rate
        )
        assert len(quant.fold_returns) == len(baseline.fold_returns) > 0
        assert report.symbol == "btcusdt"
        assert report.baseline_name == "rules_only"
        assert set(report.keep) <= {"rules_only", "quant_only"}
        assert 0.0 <= report.pbo.pbo <= 1.0

    def test_identical_inputs_produce_identical_reports(self):
        family: Mapping[str, Callable] = {
            "countertrend": fixed_bias_factory(ProposedActionType.ENTER_SHORT),
            "aligned": fixed_bias_factory(ProposedActionType.ENTER_LONG),
        }
        market = make_market(seed=11)
        first = ablation().run(market, variants=family, baseline_name="countertrend")
        second = ablation().run(market, variants=family, baseline_name="countertrend")
        assert first.as_dict() == second.as_dict()


class TestAblationReportShape:
    def test_report_as_dict(self):
        report = ablation().run(
            make_market(seed=7),
            variants=default_ablation_factories(),
            baseline_name="rules_only",
        )
        payload = report.as_dict()
        assert set(payload) == {
            "symbol",
            "cv_spec",
            "baseline_name",
            "variants",
            "pbo",
            "keep",
        }
        assert payload["baseline_name"] == "rules_only"
        assert len(payload["variants"]) == 2
        variant = payload["variants"][0]
        assert set(variant) == {
            "name",
            "fold_returns",
            "delta_mean_excess_pct",
            "delta_positive_fold_rate",
            "delta_deflated_sharpe",
            "paired_beat_rate",
            "verdict",
            "reasons",
        }
        assert isinstance(payload["pbo"]["pbo"], float)

    def test_report_type(self):
        report = ablation().run(
            make_market(seed=7),
            variants=default_ablation_factories(),
            baseline_name="rules_only",
        )
        assert isinstance(report, AblationReport)
