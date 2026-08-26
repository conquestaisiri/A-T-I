"""Tests for out-of-sample decision-pipeline evaluation (evidence priority 1).

The harness must guarantee:

1. **Out-of-sample honesty**: test windows always come strictly after their
   training prefix (past-to-future only, never the reverse).
2. **Fresh state per fold**: every fold runs a fresh pipeline/simulator/equity,
   so no fold leaks into another.
3. **Shared cost ruler**: the pipeline pays execution costs (fees + impact)
   derived from the same cost model the baselines are scored under, so it is
   never graded as a fee-free special case.
4. **Honest pooling**: means/medians, positive-fold rate and
   beats-buy-and-hold rate; nothing is claimed on in-sample numbers.
5. **Determinism**: identical inputs produce an identical report.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.application.interfaces.ai_reasoner import AIReasoner
from backend.application.research.baseline_evaluation import (
    EvaluationCosts,
    MomentumBaseline,
)
from backend.application.research.decision_pipeline_evaluator import (
    DecisionPipelineEvaluator,
)
from backend.application.simulation.paper_fill_engine import PaperFeeConfig
from backend.application.validation.purged_cv import PurgedKFold, WalkForwardCV
from backend.domain.context.market_context import MarketContext
from backend.domain.decision.proposal import DecisionProposal, RiskContext
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.oos_evaluation import PooledEvidence


def make_market(
    seed: int = 7,
    n: int = 280,
    start_price: float = 100.0,
    symbol: str = "btcusdt",
    drift: float = 0.0003,
    vol: float = 0.004,
) -> list[ObservationEvent]:
    """Seeded deterministic synthetic trade series (reproducible market)."""
    import random

    rng = random.Random(seed)
    price = start_price
    events: list[ObservationEvent] = []
    t = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        price *= 1.0 + rng.gauss(drift, vol)
        events.append(
            ObservationEvent(
                source_id="synthetic",
                source_name="Synthetic",
                event_type=ObservationEventType.TRADE,
                timestamp=t,
                payload={
                    "symbol": symbol,
                    "trade_id": i,
                    "price": round(price, 4),
                    "quantity": 1.0,
                },
            )
        )
        t += timedelta(seconds=5)
    return events


def default_evaluator(**kwargs) -> DecisionPipelineEvaluator:
    kwargs.setdefault("cv", WalkForwardCV(train_size=80, test_size=20))
    return DecisionPipelineEvaluator(**kwargs)


class TestValidation:
    def test_rejects_empty_events(self):
        with pytest.raises(ValueError):
            default_evaluator().evaluate([])

    def test_rejects_symbol_mismatch(self):
        events = make_market()
        events[50] = ObservationEvent(
            source_id="synthetic",
            source_name="Synthetic",
            event_type=ObservationEventType.TRADE,
            timestamp=events[50].timestamp,
            payload={**events[50].payload, "symbol": "ethusdt"},
        )
        with pytest.raises(ValueError, match="symbol"):
            default_evaluator().evaluate(events)

    def test_rejects_missing_or_non_positive_price(self):
        events = make_market()
        events[50] = ObservationEvent(
            source_id="synthetic",
            source_name="Synthetic",
            event_type=ObservationEventType.TRADE,
            timestamp=events[50].timestamp,
            payload={**events[50].payload, "price": 0.0},
        )
        with pytest.raises(ValueError, match="price"):
            default_evaluator().evaluate(events)

    def test_rejects_chronology_regression(self):
        events = make_market()
        events = list(events)
        events[30], events[31] = events[31], events[30]
        with pytest.raises(ValueError, match="chronological"):
            default_evaluator().evaluate(events)

    def test_rejects_non_walk_forward_splitter(self):
        with pytest.raises(ValueError, match="WalkForwardCV"):
            DecisionPipelineEvaluator(cv=PurgedKFold(n_splits=3))  # type: ignore[arg-type]

    def test_rejects_invalid_starting_equity(self):
        with pytest.raises(ValueError, match="starting_equity"):
            DecisionPipelineEvaluator(starting_equity=0.0)

    def test_rejects_when_no_folds_producible(self):
        events = make_market(n=10)
        with pytest.raises(ValueError, match="no folds"):
            default_evaluator().evaluate(events)


class TestOutOfSampleStructure:
    def test_folds_are_strictly_past_to_future(self):
        report = default_evaluator().evaluate(make_market())
        for fold in report.folds:
            assert fold.train_range[1] <= fold.test_range[0]

    def test_test_windows_do_not_overlap(self):
        report = default_evaluator().evaluate(make_market())
        covered: set[int] = set()
        for fold in report.folds:
            test = set(range(fold.test_range[0], fold.test_range[1]))
            assert covered.isdisjoint(test)
            covered |= test

    def test_fold_count_from_walk_forward(self):
        # 280 bars, train 80 expanding, test 20, step = test_size -> 10 folds.
        report = default_evaluator().evaluate(make_market(n=280))
        assert len(report.folds) == 10

    def test_every_fold_includes_costed_buy_and_hold_reference(self):
        report = default_evaluator().evaluate(make_market())
        for fold in report.folds:
            names = {b.name for b in fold.baselines}
            assert "buy_and_hold" in names
            assert "always_flat" not in names  # flat is excluded by design
            assert "momentum" in names

    def test_baselines_sorted_by_excess_descending(self):
        report = default_evaluator().evaluate(make_market())
        for fold in report.folds:
            excess = [b.excess_return_pct for b in fold.baselines]
            assert excess == sorted(excess, reverse=True)

    def test_custom_baselines_used(self):
        evaluator = default_evaluator(baselines=[MomentumBaseline(lookback=5)])
        report = evaluator.evaluate(make_market())
        names = {b.name for b in report.folds[0].baselines}
        assert "momentum" in names
        assert "ma_crossover" not in names


class TestFreshnessAndIsolation:
    def test_fresh_equity_per_fold(self):
        report = default_evaluator().evaluate(make_market())
        for fold in report.folds:
            assert fold.report.starting_equity == 100_000.0

    def test_pipeline_pays_execution_costs(self):
        # Default evaluator derives a taker fee + impact from realistic costs.
        report = default_evaluator().evaluate(make_market(seed=3))
        assert report.pooled.total_fees > 0.0
        assert report.pooled.total_slippage_bps >= 0.0

    def test_fee_free_config_charges_no_fees(self):
        evaluator = default_evaluator(
            pipeline_fee_config=PaperFeeConfig(taker_fee_rate=0.0, impact_bps=0.0)
        )
        report = evaluator.evaluate(make_market(seed=3))
        assert report.pooled.total_fees == 0.0

    def test_fee_config_derived_from_costs(self):
        evaluator = default_evaluator(costs=EvaluationCosts.free())
        report = evaluator.evaluate(make_market(seed=3))
        assert report.pooled.total_fees == 0.0


class TestReasonerSeam:
    def test_reasoner_factory_called_with_train_data(self):
        calls: list[tuple[int, int]] = []

        def factory(train_steps, train_prices):
            calls.append((len(train_steps), len(train_prices)))
            return StubReasoner()

        report = default_evaluator(reasoner_factory=factory).evaluate(make_market())
        # The name probe calls the factory once with empty inputs.
        train_calls = [c for c in calls if c != (0, 0)]
        assert len(train_calls) == len(report.folds)
        for n_steps, n_prices in train_calls:
            assert n_steps == n_prices
            assert n_steps > 0

    def test_pooled_reports_reasoner_name(self):
        report = default_evaluator(reasoner_factory=lambda steps, prices: StubReasoner()).evaluate(
            make_market()
        )
        assert report.pooled.reasoner == "StubReasoner"

    def test_default_reasoner_is_rule_based_solver(self):
        report = default_evaluator().evaluate(make_market())
        assert report.pooled.reasoner == "RuleBasedSolver"


class TestPooledEvidence:
    def test_pooled_counts_are_sums_across_folds(self):
        report = default_evaluator().evaluate(make_market())
        p = report.pooled
        assert p.total_trades == sum(f.report.trades_closed for f in report.folds)
        assert p.total_wins == sum(f.report.wins for f in report.folds)
        assert p.total_losses == sum(f.report.losses for f in report.folds)
        assert p.total_test_bars == sum(f.report.steps for f in report.folds)

    def test_pooled_mean_is_fold_mean(self):
        report = default_evaluator().evaluate(make_market())
        returns = [f.report.returns_pct for f in report.folds]
        assert report.pooled.mean_return_pct == round(sum(returns) / len(returns), 6)

    def test_pooled_rates_in_unit_interval(self):
        report = default_evaluator().evaluate(make_market())
        assert 0.0 <= report.pooled.positive_fold_rate <= 1.0
        assert 0.0 <= report.pooled.beats_buy_and_hold_rate <= 1.0

    def test_pooled_win_rate_profit_factor_expectancy(self):
        report = default_evaluator().evaluate(make_market())
        p = report.pooled
        closed = p.total_wins + p.total_losses
        assert p.win_rate == (p.total_wins / closed if closed else 0.0)
        if p.gross_loss > 0:
            assert p.profit_factor == p.gross_profit / p.gross_loss
        if p.total_trades > 0:
            assert p.net_expectancy == (p.gross_profit - p.gross_loss) / p.total_trades

    def test_pooled_is_pooled_evidence_type(self):
        report = default_evaluator().evaluate(make_market())
        assert isinstance(report.pooled, PooledEvidence)


class TestDeflatedSharpe:
    def test_pooled_reports_deflated_sharpe_with_enough_folds(self):
        report = default_evaluator().evaluate(make_market())
        assert isinstance(report.pooled.deflated_sharpe, float)
        assert 0.0 <= report.pooled.deflated_sharpe <= 1.0

    def test_deflated_sharpe_matches_domain_computation(self):
        report = default_evaluator(n_trials=7).evaluate(make_market())
        fold_returns = [f.report.returns_pct for f in report.folds]
        from backend.domain.research.pbo import compute_deflated_sharpe

        dsr = compute_deflated_sharpe(fold_returns, n_trials=7)
        assert report.pooled.deflated_sharpe == pytest.approx(dsr.dsr)

    def test_more_trials_deflates_more(self):
        events = make_market(seed=3)
        single = default_evaluator(n_trials=1).evaluate(events).pooled.deflated_sharpe
        many = default_evaluator(n_trials=50).evaluate(events).pooled.deflated_sharpe
        assert single is not None and many is not None
        assert many <= single

    def test_no_deflation_with_single_trial(self):
        report = default_evaluator(n_trials=1).evaluate(make_market())
        fold_returns = [f.report.returns_pct for f in report.folds]
        from backend.domain.research.pbo import compute_deflated_sharpe

        assert report.pooled.deflated_sharpe == pytest.approx(
            compute_deflated_sharpe(fold_returns, n_trials=1).dsr
        )

    def test_deflated_sharpe_none_when_few_folds(self):
        evaluator = default_evaluator(cv=WalkForwardCV(train_size=250, test_size=20))
        report = evaluator.evaluate(make_market())
        assert len(report.folds) < 4
        assert report.pooled.deflated_sharpe is None

    def test_rejects_invalid_n_trials(self):
        with pytest.raises(ValueError, match="n_trials"):
            DecisionPipelineEvaluator(n_trials=0)


class TestVariantComparison:
    def test_requires_at_least_two_variants(self):
        with pytest.raises(ValueError, match="at least two"):
            default_evaluator().evaluate_variants(
                make_market(),
                {"solo": lambda steps, prices: StubReasoner()},
            )

    def test_pbo_in_unit_interval_with_structure(self):
        variants = {
            "stub_a": lambda steps, prices: StubReasoner(),
            "stub_b": lambda steps, prices: StubReasoner(),
        }
        report = default_evaluator().evaluate_variants(make_market(), variants)
        assert 0.0 <= report.pbo.pbo <= 1.0
        assert report.pbo.n_trials == 2
        assert report.pbo.n_observations == len(report.variants[0].fold_returns)
        assert report.pbo.n_splits >= 1
        assert report.pbo.n_selected >= 1

    def test_all_variants_share_identical_folds(self):
        variants = {
            "stub": lambda steps, prices: StubReasoner(),
            "stub2": lambda steps, prices: StubReasoner(),
        }
        report = default_evaluator().evaluate_variants(make_market(), variants)
        test_ranges = [
            tuple((f.test_range[0], f.test_range[1]) for f in v.report.folds)
            for v in report.variants
        ]
        assert test_ranges[0] == test_ranges[1]

    def test_variant_fold_returns_aligned_with_report(self):
        variants = {
            "stub": lambda steps, prices: StubReasoner(),
            "stub2": lambda steps, prices: StubReasoner(),
        }
        report = default_evaluator().evaluate_variants(make_market(), variants)
        for variant in report.variants:
            assert variant.fold_returns == tuple(f.report.returns_pct for f in variant.report.folds)

    def test_variants_report_as_dict(self):
        variants = {
            "stub": lambda steps, prices: StubReasoner(),
            "stub2": lambda steps, prices: StubReasoner(),
        }
        report = default_evaluator().evaluate_variants(make_market(), variants)
        data = report.as_dict()
        assert set(data) == {"symbol", "variants", "pbo"}
        raw_variants = data["variants"]
        assert isinstance(raw_variants, list)
        variants_data = [v for v in raw_variants if isinstance(v, dict)]
        assert [v["name"] for v in variants_data] == ["stub", "stub2"]
        pbo_data = data["pbo"]
        assert isinstance(pbo_data, dict)
        assert set(pbo_data) == {
            "pbo",
            "mean_logit",
            "n_trials",
            "n_observations",
            "n_splits",
            "n_selected",
            "metric",
            "seed",
        }

    def test_variants_are_deterministic(self):
        variants = {
            "stub": lambda steps, prices: StubReasoner(),
            "stub2": lambda steps, prices: StubReasoner(),
        }
        events = make_market(seed=11)
        a = default_evaluator().evaluate_variants(events, variants)
        b = default_evaluator().evaluate_variants(events, variants)
        assert a.as_dict() == b.as_dict()

    def test_superior_variant_survives_out_of_sample(self):
        # One variant always outperforms: its fold returns dominate, so
        # in-sample selection must survive out-of-sample (PBO ~ 0).
        class StrongReasoner(AIReasoner):
            def reason(self, context, risk_context):
                from backend.domain.decision.proposal import (
                    DecisionProposal,
                    EvidenceItem,
                    Hypothesis,
                    ProposedAction,
                    ProposedActionType,
                )

                return DecisionProposal(
                    proposal_id=f"strong-{context.created_at.isoformat()}",
                    correlation_id=context.snapshot.symbol,
                    created_at=context.created_at,
                    symbol=context.snapshot.symbol,
                    hypothesis=Hypothesis(
                        statement="strong",
                        supporting_evidence=(
                            EvidenceItem(source="strong", summary="s", value=1.0),
                        ),
                        opposing_evidence=(),
                    ),
                    confidence=0.9,
                    uncertainty="low",
                    actions=(
                        ProposedAction(
                            action_type=ProposedActionType.ENTER_LONG,
                            size_fraction=0.5,
                            order=1,
                            rationale="strong",
                        ),
                    ),
                    risk_context=risk_context,
                    alternatives=(),
                    rationale="strong",
                )

        variants = {
            "strong": lambda steps, prices: StrongReasoner(),
            "stub": lambda steps, prices: StubReasoner(),
        }
        report = default_evaluator().evaluate_variants(make_market(seed=5), variants)
        assert report.pbo.pbo < 0.3


class TestDeterminism:
    def test_identical_inputs_produce_identical_report(self):
        a = default_evaluator().evaluate(make_market(seed=11))
        b = default_evaluator().evaluate(make_market(seed=11))
        assert a.as_dict() == b.as_dict()

    def test_different_seeds_produce_different_report(self):
        a = default_evaluator().evaluate(make_market(seed=11))
        b = default_evaluator().evaluate(make_market(seed=99))
        assert a.as_dict() != b.as_dict()


class TestSerialization:
    def test_fold_as_dict_round_trips_structure(self):
        report = default_evaluator().evaluate(make_market())
        data = report.folds[0].as_dict()
        assert set(data) == {"fold", "train_range", "test_range", "report", "baselines"}
        assert isinstance(data["report"], dict)
        assert data["report"]["win_rate"] == report.folds[0].report.win_rate

    def test_full_report_as_dict(self):
        report = default_evaluator().evaluate(make_market())
        data = report.as_dict()
        assert set(data) == {"symbol", "costs", "cv_spec", "folds", "pooled"}
        assert data["symbol"] == "btcusdt"
        pooled = data["pooled"]
        assert isinstance(pooled, dict)
        assert pooled["n_folds"] == len(report.folds)

    def test_pooled_as_dict(self):
        report = default_evaluator().evaluate(make_market())
        data = report.pooled.as_dict()
        assert data["profit_factor"] == report.pooled.profit_factor
        assert data["net_expectancy"] == report.pooled.net_expectancy
        cost_model = data["cost_model"]
        assert isinstance(cost_model, dict)
        assert cost_model["taker_fee_pct"] == pytest.approx(0.0004)


class StubReasoner(AIReasoner):
    """Deterministic reasoner that always stands aside (no trades)."""

    def reason(self, context: MarketContext, risk_context: RiskContext) -> DecisionProposal:
        from backend.domain.decision.proposal import (
            EvidenceItem,
            Hypothesis,
            ProposedAction,
            ProposedActionType,
        )

        return DecisionProposal(
            proposal_id=f"stub-{context.created_at.isoformat()}",
            correlation_id=context.snapshot.symbol,
            created_at=context.created_at,
            symbol=context.snapshot.symbol,
            hypothesis=Hypothesis(
                statement="stub",
                supporting_evidence=(EvidenceItem(source="stub", summary="s", value=0.0),),
                opposing_evidence=(),
            ),
            confidence=0.5,
            uncertainty="none",
            actions=(
                ProposedAction(
                    action_type=ProposedActionType.STAND_ASIDE,
                    size_fraction=0.10,
                    order=1,
                    rationale="stub",
                ),
            ),
            risk_context=risk_context,
            alternatives=(),
            rationale="stub",
        )
