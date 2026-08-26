"""Cost-model sensitivity sweep tests (T1-9-1, P5-006).

The sweep must (1) re-run the real OOS evaluation under perturbed cost
rulers, (2) perturb the actual numbers (a sweep that changes nothing is a
no-op, not a test), (3) apply the same conservative verdict gates as the
evidence engine, and (4) report verdict stability honestly.
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.application.research.cost_sweep import (
    CostScenario,
    CostScenarioResult,
    CostSweep,
    CostSweepReport,
    default_scenarios,
)
from backend.application.research.decision_pipeline_evaluator import (
    ReasonerFactory,
)
from backend.application.validation.purged_cv import WalkForwardCV
from backend.domain.observation.event import ObservationEvent, ObservationEventType
from backend.domain.research.passport import (
    EvidenceVerdict,
    PassportVerdict,
)


def make_market(
    seed: int = 7,
    n: int = 300,
    start_price: float = 100.0,
    symbol: str = "btcusdt",
    drift: float = 0.0003,
    vol: float = 0.004,
) -> list[ObservationEvent]:
    """Seeded deterministic synthetic trade series (reproducible market)."""
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


def variant_factories() -> Mapping[str, ReasonerFactory]:
    """Two solver configurations as the PBO variant family."""
    from backend.application.backtest.report import ReplayStep
    from backend.application.decision.rule_based_solver import RuleBasedSolver, SolverConfig
    from backend.application.interfaces.ai_reasoner import AIReasoner

    def _factory(
        **overrides: Any,
    ) -> Callable[[Sequence[ReplayStep], Sequence[float]], AIReasoner]:
        def _build(train_steps: Sequence[ReplayStep], train_prices: Sequence[float]) -> AIReasoner:
            return RuleBasedSolver(SolverConfig(**overrides))

        return _build

    return {
        "default": _factory(),
        "conservative": _factory(
            momentum_entry_pct=0.10,
            risk_per_trade_pct=0.01,
            risk_reward_ratio=3.0,
        ),
    }


def sweep(
    **kwargs: Any,
) -> CostSweep:
    """A fast sweep configuration (small windows, shared cv)."""
    return CostSweep(
        cv=WalkForwardCV(train_size=50, test_size=15),
        **kwargs,
    )


class TestCostScenario:
    def test_defaults_are_baseline(self) -> None:
        scenario = CostScenario("baseline")
        assert scenario.half_spread_factor == 1.0
        assert scenario.taker_fee_factor == 1.0
        assert scenario.impact_factor == 1.0

    def test_zero_factor_rejected(self) -> None:
        with pytest.raises(ValueError):
            CostScenario("free", half_spread_factor=0.0)

    def test_negative_factor_rejected(self) -> None:
        with pytest.raises(ValueError):
            CostScenario("negative", taker_fee_factor=-1.0)

    def test_default_family_has_baseline_plus_six_extremes(self) -> None:
        family = default_scenarios()
        assert len(family) == 7
        assert family[0].name == "baseline"
        names = [s.name for s in family]
        assert "spread_0.5x" in names
        assert "spread_1.5x" in names
        assert "fee_1.5x" in names
        assert "impact_1.5x" in names


class TestCostScenarioResult:
    def test_promotable_requires_promote_to_paper(self) -> None:
        promoted = CostScenarioResult(
            scenario=CostScenario("a"),
            report=object(),  # type: ignore[arg-type]
            verdict=PassportVerdict(EvidenceVerdict.PROMOTE_TO_PAPER),
        )
        rejected = CostScenarioResult(
            scenario=CostScenario("b"),
            report=object(),  # type: ignore[arg-type]
            verdict=PassportVerdict(EvidenceVerdict.REJECT, ("gate failed",)),
        )
        assert promoted.promotable
        assert not rejected.promotable


class TestCostSweep:
    def test_default_sweep_reports_baseline_plus_six_scenarios(self) -> None:
        events = make_market(seed=3)
        report = sweep().sweep(events)
        assert isinstance(report, CostSweepReport)
        assert report.symbol == "btcusdt"
        assert report.baseline.scenario.name == "baseline"
        assert len(report.scenarios) == 7
        for result in report.scenarios:
            assert result.report.pooled.n_folds >= 4
            assert result.verdict.verdict in EvidenceVerdict
        assert isinstance(report.verdict_stable, bool)
        assert report.pbo is None
        assert report.pbo_applied is False

    def test_costs_actually_perturb_the_ruler(self) -> None:
        events = make_market(seed=3, drift=0.003, vol=0.001)
        report = sweep().sweep(events)
        by_name = {r.scenario.name: r for r in report.scenarios}
        slip = {name: by_name[name].report.pooled.total_slippage_bps for name in by_name}
        assert slip["spread_0.5x"] < slip["baseline"] < slip["spread_1.5x"]
        assert slip["impact_0.5x"] < slip["baseline"] < slip["impact_1.5x"]
        fees = {name: by_name[name].report.pooled.total_fees for name in by_name}
        assert fees["fee_0.5x"] < fees["baseline"] < fees["fee_1.5x"]
        assert fees["fee_1.5x"] == pytest.approx(3.0 * fees["fee_0.5x"], rel=1e-3)
        assert by_name["spread_1.5x"].report.pooled.cost_model["half_spread_pct"] == 0.0002 * 1.5
        assert by_name["spread_0.5x"].report.pooled.cost_model["half_spread_pct"] == 0.0002 * 0.5

    def test_custom_scenarios_used_verbatim(self) -> None:
        events = make_market(seed=3)
        report = sweep().sweep(
            events,
            scenarios=[
                CostScenario("baseline"),
                CostScenario("spread_1.5x", half_spread_factor=1.5),
            ],
        )
        assert [s.scenario.name for s in report.scenarios] == [
            "baseline",
            "spread_1.5x",
        ]
        assert report.baseline.scenario.name == "baseline"

    def test_empty_scenarios_rejected(self) -> None:
        events = make_market(seed=3)
        with pytest.raises(ValueError):
            sweep().sweep(events, scenarios=[])

    def test_pbo_computed_once_on_baseline_and_applied(self) -> None:
        events = make_market(seed=3, drift=0.002, vol=0.002)
        report = sweep(variant_factories=variant_factories()).sweep(events)
        assert report.pbo is not None
        assert report.pbo.pbo is not None
        assert report.pbo_applied is True

    def test_too_few_events_for_folds_rejected(self) -> None:
        with pytest.raises(ValueError):
            sweep().sweep(make_market(n=40))

    def test_deterministic_given_same_inputs(self) -> None:
        events = make_market(seed=11)
        a = sweep().sweep(events)
        b = sweep().sweep(events)
        assert a.as_dict() == b.as_dict()

    def test_report_as_dict_roundtrip_shape(self) -> None:
        events = make_market(seed=3)
        data = sweep().sweep(events).as_dict()
        assert set(data) == {
            "symbol",
            "cv_spec",
            "baseline",
            "scenarios",
            "pbo",
            "pbo_applied",
            "verdict_stable",
        }
        assert isinstance(data["scenarios"], list)
        assert data["baseline"]["scenario"]["name"] == "baseline"
