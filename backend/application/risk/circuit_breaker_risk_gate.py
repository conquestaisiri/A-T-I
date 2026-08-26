# backend/application/risk/circuit_breaker_risk_gate.py
"""Deterministic risk gate with multi-layer circuit breakers (playbook §2).

The gate protects capital and never predicts markets. It holds veto authority:
REJECTED stops the proposal outright, REDUCED caps its size. Exits, scaling
out, and risk reductions are always allowed — safety actions are never vetoed.

Fractional Kelly sizing (ADR TBD): dynamically caps position size by
Kelly fraction × safety_factor, using edge estimated from historical outcomes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from math import sqrt

from backend.application.execution.market_impact import (
    ImpactObservation,
    SquareRootImpactCalibrator,
)
from backend.application.interfaces.risk_feed import RiskFeed
from backend.application.interfaces.risk_gate import RiskGate
from backend.application.risk.vpin import VpinState, VpinTracker
from backend.domain.decision.proposal import (
    DecisionProposal,
    ProposedAction,
    ProposedActionType,
    RiskContext,
)
from backend.domain.risk.risk_decision import RiskDecision, RiskVerdict


@dataclass(frozen=True, slots=True)
class RiskGateConfig:
    """Limits enforced by the circuit breaker gate.

    The six risk budgets follow the blueprint: 2% per trade, 1% per symbol,
    3% portfolio, 6% daily, 10% monthly, 20% drawdown. ``max_total_loss_pct``
    is retained as an emergency total-capital cap (Constitution: the portfolio
    must never be destroyed). ``max_fraction_of_risk_budget`` is the 60% rule:
    a new trade's estimated maximum loss may never consume more than 60% of the
    least-available risk budget, leaving headroom on every limit.

    All fractions are in [0, 1].
    """

    # Six risk budgets (blueprint defaults) ---------------------------------
    max_risk_per_trade_pct: float = 0.02
    max_risk_per_symbol_pct: float = 0.01
    max_portfolio_risk_pct: float = 0.03
    max_daily_loss_pct: float = 0.06
    max_monthly_loss_pct: float = 0.10
    max_drawdown_pct: float = 0.20
    # Emergency total-capital cap (never defaults to more than 50%)
    max_total_loss_pct: float = 0.50

    # 60% rule + bracket invariant ------------------------------------------
    max_fraction_of_risk_budget: float = 0.60
    require_exit_bracket_on_entry: bool = True

    # Legacy sizing limits --------------------------------------------------
    max_position_size_fraction: float = 0.20
    max_open_exposure_pct: float = 0.60

    # Fractional Kelly sizing --------------------------------------------------
    kelly_safety_factor: float = 0.5  # half-Kelly; 0 disables Kelly
    min_edge_threshold: float = 0.01  # minimum edge to apply Kelly (1%)
    max_kelly_fraction: float = 0.25  # cap Kelly fraction at 25%

    # Toxicity veto (VPIN) -----------------------------------------------------
    veto_on_toxicity: bool = True
    min_toxicity_evidence_buckets: int = 8

    # Square-root impact veto (calibrated from own fills, integration #26) -----
    veto_on_excess_impact: bool = True
    max_impact_to_reward_ratio: float = 0.25
    min_impact_evidence: int = 30

    # Reconciliation veto (P0-012 / spec §9.5) ----------------------------------
    # Any symbol reported inconsistent (venue vs internal position mismatch)
    # blocks new risk until reconciliation passes again.
    block_on_reconciliation_mismatch: bool = True

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            # Only fractions (floats) are range-checked; booleans are not.
            if isinstance(value, float) and not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class KellyEdgeEstimate:
    """Historical edge estimate for a symbol/strategy."""

    symbol: str
    win_rate: float
    avg_win: float
    avg_loss: float
    trade_count: int
    confidence: float  # 0-1, based on trade_count

    @property
    def edge(self) -> float:
        """Expected return per unit risk: E[R] / σ"""
        if self.trade_count < 10:
            return 0.0  # insufficient data
        win_rate = self.win_rate
        loss_rate = 1.0 - win_rate
        expected_return = win_rate * self.avg_win - loss_rate * self.avg_loss
        # Approximate volatility from win/loss distribution
        variance = (
            win_rate * (self.avg_win - expected_return) ** 2
            + loss_rate * (self.avg_loss - expected_return) ** 2
        )
        if variance <= 0:
            return 0.0
        return expected_return / sqrt(variance)

    @property
    def kelly_fraction(self) -> float:
        """Kelly fraction for binary outcomes: f* = (p*b - q) / b.

        Where p = win_rate, q = loss_rate, b = avg_win/avg_loss.
        This gives the optimal fraction of capital to risk.
        """
        if self.trade_count < 20:
            return 0.0  # insufficient data
        if self.avg_win <= 0 or self.avg_loss <= 0:
            return 0.0
        p = self.win_rate
        q = 1.0 - p
        b = self.avg_win / self.avg_loss  # odds
        if b <= 0:
            return 0.0
        f_star = (p * b - q) / b
        return max(0.0, min(f_star, 1.0))  # clamp to [0, 1]


_SAFE_ACTIONS = frozenset(
    {
        ProposedActionType.EXIT,
        ProposedActionType.SCALE_OUT,
        ProposedActionType.REDUCE_RISK,
        ProposedActionType.STAND_ASIDE,
    }
)


class CircuitBreakerRiskGate(RiskGate, RiskFeed):
    """Enforces the playbook's loss limits and sizing on every proposal.

    Implements :class:`~backend.application.interfaces.risk_feed.RiskFeed` so
    the same instance the simulator evaluates with is the instance the ingest
    and decision paths feed — one source of risk state (gap G3 wiring).
    """

    def __init__(self, config: RiskGateConfig | None = None) -> None:
        self._config = config or RiskGateConfig()
        self._now: Callable[[], datetime] = lambda: datetime.now(UTC)
        # In-memory edge cache; in production this comes from episodic memory
        self._edge_cache: dict[str, KellyEdgeEstimate] = {}
        # Per-symbol VPIN toxicity trackers fed by the execution/deploy stream.
        self._toxicity: dict[str, VpinTracker] = {}
        # Impact calibrator + per-symbol market stats for the pre-trade veto.
        self._impact = SquareRootImpactCalibrator(
            min_observations=max(1, config.min_impact_evidence) if config else 30
        )
        self._market_stats: dict[str, tuple[float, float, float]] = {}
        # Symbols with an outstanding venue-vs-internal position mismatch.
        self._reconciliation_mismatches: set[str] = set()

    @property
    def config(self) -> RiskGateConfig:
        """Currently enforced limits (read-only view for the operator surface)."""
        return self._config

    def update_config(self, **overrides: float | bool) -> RiskGateConfig:
        """Atomically replace the active limits (operator runtime tuning).

        Only known ``RiskGateConfig`` fields are accepted; the new values are
        validated by the frozen dataclass constructor before being swapped in,
        so an invalid request never leaves a half-applied state.
        """
        valid = set(RiskGateConfig.__dataclass_fields__)
        unknown = set(overrides) - valid
        if unknown:
            raise ValueError(f"unknown risk config fields: {sorted(unknown)}")
        current = {name: getattr(self._config, name) for name in valid}
        current.update(overrides)
        new_config = RiskGateConfig(**current)
        self._config = new_config
        return new_config

    def set_market_stats(
        self,
        symbol: str,
        *,
        avg_daily_volume: float,
        volatility_bps: float,
        half_spread_bps: float,
    ) -> None:
        """Register per-symbol market stats used by the impact veto."""
        if avg_daily_volume <= 0.0:
            raise ValueError("avg_daily_volume must be positive")
        if volatility_bps < 0.0 or half_spread_bps < 0.0:
            raise ValueError("volatility and half-spread must be non-negative")
        self._market_stats[symbol] = (avg_daily_volume, volatility_bps, half_spread_bps)

    def market_stats_registered(self, symbol: str) -> bool:
        """True when market stats are registered for ``symbol``.

        The decision path uses this guard before feeding fills into the impact
        calibrator: without operator-supplied venue stats there is nothing to
        calibrate against and ``record_impact_fill`` would raise.
        """
        return symbol in self._market_stats

    def record_impact_fill(
        self,
        symbol: str,
        *,
        quantity: float,
        realized_slippage_bps: float,
    ) -> None:
        """Feed one realized fill into the impact calibrator (integration #26).

        Requires market stats to have been registered first via
        :meth:`set_market_stats`.
        """
        stats = self._market_stats.get(symbol)
        if stats is None:
            raise ValueError(f"No market stats registered for {symbol}")
        adv, volatility_bps, half_spread_bps = stats
        self._impact.observe(
            symbol,
            ImpactObservation(
                quantity=quantity,
                adv=adv,
                volatility_bps=volatility_bps,
                realized_slippage_bps=realized_slippage_bps,
                half_spread_bps=half_spread_bps,
            ),
        )

    def record_toxicity_flow(self, symbol: str, signed_flow: float) -> None:
        """Feed signed order flow for ``symbol`` into its VPIN estimator."""
        tracker = self._toxicity.get(symbol)
        if tracker is None:
            tracker = VpinTracker()
            self._toxicity[symbol] = tracker
        tracker.record(signed_flow)

    def set_reconciliation_state(self, symbol: str, consistent: bool) -> None:
        """Feed venue-vs-internal reconciliation health for ``symbol``.

        ``consistent=False`` records an outstanding position mismatch that
        blocks new risk gate-wide until a later reconciliation reports the
        symbol consistent again (P0-012 / spec §9.5).
        """
        if consistent:
            self._reconciliation_mismatches.discard(symbol)
        else:
            self._reconciliation_mismatches.add(symbol)

    def reconciliation_mismatches(self) -> frozenset[str]:
        """Symbols currently reported inconsistent with venue truth."""
        return frozenset(self._reconciliation_mismatches)

    def toxicity(self, symbol: str) -> VpinState | None:
        """Current VPIN toxicity state for ``symbol`` (None if never fed)."""
        tracker = self._toxicity.get(symbol)
        return tracker.state() if tracker is not None else None

    def evaluate(self, proposal: DecisionProposal, mark_price: float | None = None) -> RiskDecision:
        ctx = proposal.risk_context
        primary = proposal.primary_action
        timestamp = self._now()

        if primary is None:
            return RiskDecision(
                verdict=RiskVerdict.APPROVED,
                reason="No actions to gate; proposal is informational.",
                approved_size_fraction=None,
                evaluated_at=timestamp,
            )

        # STAND_ASIDE does nothing — always allow
        if primary.action_type is ProposedActionType.STAND_ASIDE:
            return RiskDecision(
                verdict=RiskVerdict.APPROVED,
                reason="Stand-aside is always allowed.",
                approved_size_fraction=None,
                evaluated_at=timestamp,
            )

        # Safety actions (EXIT, SCALE_OUT, REDUCE_RISK) reduce risk — allow
        # even when breakers are tripped.
        if primary.action_type in (
            ProposedActionType.EXIT,
            ProposedActionType.SCALE_OUT,
            ProposedActionType.REDUCE_RISK,
        ):
            return RiskDecision(
                verdict=RiskVerdict.APPROVED,
                reason=f"Safety action {primary.action_type.value} is always allowed.",
                approved_size_fraction=None,
                evaluated_at=timestamp,
            )

        # From here on the action increases risk; the full policy applies.

        # 0. Reconciliation veto (P0-012 / spec §9.5): per-symbol block on
        #    outstanding venue-vs-internal position mismatch. Only the
        #    mismatched symbol is halted; other symbols may continue. This
        #    prevents a single forged reconcile (see routes_reconciliation) from
        #    DoS-ing the entire gate.
        if (
            self._config.block_on_reconciliation_mismatch
            and proposal.symbol in self._reconciliation_mismatches
        ):
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=(
                    f"Reconciliation veto: position mismatch on {proposal.symbol} blocks "
                    "new risk until reconciliation passes."
                ),
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )

        # 1. Toxicity veto (VPIN): never add risk into a book the estimator
        #    flags as toxic, once enough bucket evidence has accumulated.
        toxicity = self.toxicity(proposal.symbol)
        if (
            self._config.veto_on_toxicity
            and toxicity is not None
            and toxicity.toxic
            and toxicity.buckets >= self._config.min_toxicity_evidence_buckets
        ):
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=(
                    f"Toxicity veto: VPIN {toxicity.vpin:.3f} (baseline "
                    f"{toxicity.toxicity_quartile:.2f}x) flags {proposal.symbol} "
                    "as a toxic book."
                ),
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )

        # 2. Impact veto (square-root law, integration #26): reject when the
        #    expected cost of crossing the book for the requested size consumes
        #    too large a share of the trade's expected reward. Only applies when
        #    a calibrated impact model and a mark price are available.
        impact_block = self._impact_veto(proposal, primary, mark_price)
        if impact_block is not None:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=impact_block,
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )

        # 3. Mandatory protective bracket (Constitution/blueprint invariant):
        #    a position is never opened without a stop-loss and take-profit.
        plan = proposal.pre_trade_plan
        if self._config.require_exit_bracket_on_entry and (plan is None or not plan.has_bracket):
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=(
                    "Protective OCO bracket (stop-loss + take-profit) is mandatory "
                    "for risk-increasing actions."
                ),
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )

        # 4. Circuit breakers (six budgets at their current loss levels).
        block = self._circuit_breaker_violation(ctx)
        if block is not None:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=block,
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )

        requested = primary.size_fraction

        # 5. Sizing requires a measurable loss per unit size. The plan must
        #    declare a stop distance (or, failing that, a committed risk-per-
        #    trade) so the per-trade/symbol/portfolio budgets can be enforced.
        stop_distance = plan.stop_distance_pct if plan is not None else None
        if stop_distance is None:
            stop_distance = plan.risk_per_trade_pct if plan is not None else None
        if stop_distance is None or stop_distance <= 0.0:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=(
                    "Pre-trade plan must define a stop-loss distance or a "
                    "risk-per-trade fraction for budget-based sizing."
                ),
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )

        caps: list[tuple[float, str]] = []

        # Classic exposure and position caps.
        remaining_exposure = self._config.max_open_exposure_pct - ctx.open_exposure_pct
        if remaining_exposure <= 0.0:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason="Open exposure already at the configured maximum.",
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )
        caps.append((remaining_exposure, "exposure"))
        caps.append((self._config.max_position_size_fraction, "position"))

        # 6. Per-trade risk budget: size * stop_distance <= 2% equity at risk.
        caps.append((self._config.max_risk_per_trade_pct / stop_distance, "trade risk"))

        # 7. Per-symbol risk budget: new risk + already-used symbol risk <= 1%.
        symbol_remaining = self._config.max_risk_per_symbol_pct - ctx.symbol_risk_used_pct
        if symbol_remaining <= 0.0:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=(
                    f"Symbol risk budget ({self._config.max_risk_per_symbol_pct:.0%}) is exhausted."
                ),
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )
        caps.append((symbol_remaining / stop_distance, "symbol risk"))

        # 8. Portfolio risk budget: new risk + open portfolio risk <= 3%.
        portfolio_remaining = self._config.max_portfolio_risk_pct - ctx.portfolio_risk_used_pct
        if portfolio_remaining <= 0.0:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason=(
                    "Portfolio risk budget "
                    f"({self._config.max_portfolio_risk_pct:.0%}) is exhausted."
                ),
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )
        caps.append((portfolio_remaining / stop_distance, "portfolio risk"))

        # 9. The 60% rule: never spend more than 60% of the least-available
        #    remaining budget on a single new trade.
        daily_remaining = max(0.0, self._config.max_daily_loss_pct - ctx.daily_loss_pct)
        monthly_remaining = max(0.0, self._config.max_monthly_loss_pct - ctx.monthly_loss_pct)
        drawdown_remaining = max(0.0, self._config.max_drawdown_pct - ctx.drawdown_pct)
        total_remaining = max(0.0, self._config.max_total_loss_pct - ctx.total_loss_pct)
        available_budget = min(
            daily_remaining, monthly_remaining, drawdown_remaining, total_remaining
        )
        if available_budget <= 0.0:
            return RiskDecision(
                verdict=RiskVerdict.REJECTED,
                reason="No risk budget remains; trading halted until a limit resets.",
                approved_size_fraction=0.0,
                evaluated_at=timestamp,
            )
        caps.append(
            (
                (self._config.max_fraction_of_risk_budget * available_budget) / stop_distance,
                "risk budget",
            )
        )

        # 10. Fractional Kelly cap (when edge data exists).
        kelly_cap = self._kelly_cap(proposal.symbol, primary.action_type)
        if kelly_cap is not None:
            caps.append((kelly_cap, "kelly"))

        capped = min(limit for limit, _ in caps)
        capped = min(1.0, max(0.0, capped))

        if capped < requested:
            reasons = [name for limit, name in caps if limit < requested]
            return RiskDecision(
                verdict=RiskVerdict.REDUCED,
                reason="Size reduced to " + " / ".join(reasons),
                approved_size_fraction=capped,
                evaluated_at=timestamp,
            )
        return RiskDecision(
            verdict=RiskVerdict.APPROVED,
            reason="Within configured risk limits.",
            approved_size_fraction=requested,
            evaluated_at=timestamp,
        )

    def _impact_veto(
        self,
        proposal: DecisionProposal,
        primary: ProposedAction,
        mark_price: float | None,
    ) -> str | None:
        """Impact-biased veto for risk-increasing actions (integration #26).

        When the calibrated square-root model is available for the symbol and
        a mark price is provided, estimate the expected impact (in bps) of
        placing the requested size and reject when it consumes more than
        ``max_impact_to_reward_ratio`` of the trade's expected reward (the
        take-profit distance). Impact only approximately covers half-spread +
        square-root participation; the ratio compares crossable cost to the
        full intended reward.
        """
        if not self._config.veto_on_excess_impact:
            return None
        if mark_price is None or mark_price <= 0.0:
            return None

        plan = proposal.pre_trade_plan
        if plan is None or plan.take_profit.distance_pct is None:
            return None
        reward_bps = plan.take_profit.distance_pct * 10_000.0
        if reward_bps <= 0.0:
            return None

        stats = self._market_stats.get(proposal.symbol)
        if stats is None:
            return None
        adv, volatility_bps, half_spread_bps = stats

        # requested notional / mark price gives approximate base quantity.
        requested_notional = primary.size_fraction * proposal.risk_context.account_equity
        quantity = requested_notional / mark_price
        if quantity <= 0.0:
            return None

        impact_bps = self._impact.estimate_impact_bps(
            proposal.symbol,
            quantity=quantity,
            adv=adv,
            volatility_bps=volatility_bps,
            half_spread_bps=half_spread_bps,
        )
        if impact_bps is None:
            return None

        allowed_impact = self._config.max_impact_to_reward_ratio * reward_bps
        if impact_bps > allowed_impact:
            return (
                f"Impact veto: estimated {impact_bps:.2f} bps impact consumes "
                f"{impact_bps / reward_bps:.0%} of the {reward_bps:.0f} bps "
                f"reward target for {proposal.symbol}."
            )
        return None

    def _kelly_cap(self, symbol: str, action_type: ProposedActionType) -> float | None:
        """Return Kelly-based size cap, or None if Kelly is disabled/insufficient data."""
        if self._config.kelly_safety_factor <= 0.0:
            return None
        if action_type in _SAFE_ACTIONS:
            return None  # Kelly only for risk-increasing actions

        edge_est = self._edge_cache.get(symbol)
        if edge_est is None or edge_est.trade_count < 20:
            return None  # insufficient data for Kelly

        if edge_est.edge < self._config.min_edge_threshold:
            return None  # edge too small

        kelly_f = edge_est.kelly_fraction
        if kelly_f <= 0:
            return None

        return min(kelly_f * self._config.kelly_safety_factor, self._config.max_kelly_fraction)

    def update_edge_estimate(self, symbol: str, edge_est: KellyEdgeEstimate) -> None:
        """Update edge estimate from episodic memory / reflection."""
        self._edge_cache[symbol] = edge_est

    def _circuit_breaker_violation(self, ctx: RiskContext) -> str | None:
        if ctx.total_loss_pct >= self._config.max_total_loss_pct:
            return "Total loss halt: equity below the maximum total loss threshold."
        if ctx.monthly_loss_pct >= self._config.max_monthly_loss_pct:
            return "Monthly loss limit reached; trading halted until reset."
        if ctx.daily_loss_pct >= self._config.max_daily_loss_pct:
            return "Daily loss limit reached; trading halted until tomorrow."
        if ctx.drawdown_pct >= self._config.max_drawdown_pct:
            return "Maximum drawdown reached; new risk is not permitted."
        return None
