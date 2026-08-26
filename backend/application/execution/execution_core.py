"""Execution Core - live execution engine with prop firm rule enforcement.

This is the live trading engine that replaces PaperTradingSimulator for production.
It coordinates: AI Proposal -> Risk Gate (with prop rules) -> MT5 Bridge -> Ledger.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.application.interfaces.order_gateway import OrderGateway
from backend.application.interfaces.risk_feed import RiskFeed
from backend.application.interfaces.risk_gate import RiskGate
from backend.application.interfaces.supervisor import Supervisor
from backend.application.risk.circuit_breaker_risk_gate import (
    CircuitBreakerRiskGate,
    RiskGateConfig,
)
from backend.domain.decision.proposal import (
    DecisionProposal,
    ProposedActionType,
)
from backend.domain.execution.execution_report import ExecutionReport
from backend.domain.execution.order import OrderRequest, OrderSide, OrderType, TimeInForce
from backend.domain.risk.risk_decision import RiskVerdict

logger = logging.getLogger(__name__)


class PropRuleEngine(Protocol):
    """Protocol for prop firm rule engines.

    Each prop firm (FundingPips, For Traders, etc.) implements this to
    enforce their specific rules before trade execution.
    """

    def check_pre_trade(
        self,
        proposal: DecisionProposal,
        account_info: Any,
        positions: list[Any],
    ) -> tuple[bool, str | None]:
        """Check if trade is allowed under prop firm rules.

        Returns:
            (allowed, rejection_reason)
        """
        ...

    def check_post_fill(
        self,
        report: ExecutionReport,
        account_info: Any,
        positions: list[Any],
    ) -> tuple[bool, str | None]:
        """Check if fill violates any rules (e.g., consistency, daily loss).

        Returns:
            (allowed, warning_reason)
        """
        ...

    def get_rules_summary(self) -> dict[str, Any]:
        """Return human-readable rules summary for UI/logging."""
        ...


@dataclass(frozen=True, slots=True)
class ExecutionConfig:
    """Configuration for live execution."""

    # Risk gate config
    risk_config: RiskGateConfig

    # Execution mode
    live_trading_authorized: bool = False
    paper_mode: bool = True  # True = paper, False = live MT5

    # Prop firm settings
    prop_firm: str | None = None  # "fundingpips", "fortraders", etc.
    prop_rules_enabled: bool = True

    # Execution limits
    max_slippage_pips: float = 5.0
    max_order_age_seconds: float = 30.0
    retry_attempts: int = 3
    retry_delay_seconds: float = 1.0

    # Monitoring
    enable_execution_attribution: bool = True
    log_all_orders: bool = True


class ExecutionCore:
    """Core execution engine for live trading.

    Flow:
        1. Receive DecisionProposal from AI
        2. Build OrderRequest from proposal
        3. Run through RiskGate (with prop rules)
        4. If approved, submit to OrderGateway (MT5 or Paper)
        5. Record ExecutionReport
        5. Feed fill back to RiskFeed (for impact/VPIN)
    """

    def __init__(
        self,
        config: ExecutionConfig,
        order_gateway: OrderGateway,
        risk_gate: RiskGate,
        risk_feed: RiskFeed | None = None,
        supervisor: Supervisor | None = None,
        prop_engine: Any | None = None,
        paper_fallback: Any | None = None,
    ) -> None:
        self._config = config
        self._gateway = order_gateway
        self._risk_gate = risk_gate
        self._risk_feed = risk_feed
        self._supervisor = supervisor
        self._prop_engine = prop_engine
        self._paper_fallback = paper_fallback

        # State
        self._last_proposal: DecisionProposal | None = None
        self._last_report: ExecutionReport | None = None

        logger.info(
            "ExecutionCore initialized: mode=%s prop=%s live_authorized=%s",
            "paper" if config.paper_mode else "live",
            config.prop_firm or "none",
            config.live_trading_authorized,
        )

    def process_proposal(
        self,
        proposal: DecisionProposal,
        mark_price: float,
        account_info: Any | None = None,
        positions: list[Any] | None = None,
    ) -> ExecutionReport:
        """Process an AI proposal through the full execution pipeline.

        Returns the ExecutionReport (filled or rejected).
        """
        self._last_proposal = proposal

        # 1. Supervisor check
        if self._supervisor:
            decision = self._supervisor.check()
            if not decision.may_trade:
                return self._rejected_report(proposal, f"Supervisor: {decision.reason}")

        # 2. Build order request from proposal (needs positions for exit side)
        order_request = self._build_order_request(proposal, mark_price, positions)
        if order_request is None:
            return self._rejected_report(proposal, "No actionable order in proposal")

        # 3. Risk Gate check (circuit breakers + Kelly + VPIN + Impact)
        risk_decision = self._risk_gate.evaluate(proposal, mark_price)
        if risk_decision.verdict is RiskVerdict.REJECTED:
            logger.warning("Risk gate REJECTED: %s", risk_decision.reason)
            return self._rejected_report(proposal, f"Risk gate: {risk_decision.reason}")

        # Apply risk gate sizing (REDUCED verdict): approved_size_fraction is an
        # absolute cap on equity fraction, not a multiplier of the requested qty.
        if risk_decision.verdict is RiskVerdict.REDUCED:
            order_request = self._apply_risk_reduction(
                order_request, risk_decision, proposal, mark_price
            )
            logger.info("Risk gate REDUCED order size: %s", risk_decision.reason)

        # 4. Prop firm rules check
        if self._config.prop_rules_enabled and self._prop_engine:
            allowed, reason = self._prop_engine.check_pre_trade(
                proposal, account_info, positions or []
            )
            if not allowed:
                logger.warning("Prop rules REJECTED: %s", reason)
                return self._rejected_report(proposal, f"Prop rules: {reason}")

        # 5. Live trading authorization check
        if not self._config.paper_mode and not self._config.live_trading_authorized:
            return self._rejected_report(
                proposal, "Live trading not authorized (set live_trading_authorized=true)"
            )

        # 6. Execute order
        if self._config.paper_mode:
            report = self._execute_paper(order_request, mark_price)
        else:
            report = self._execute_live(order_request)

        # 6b. Slippage guard: compare realized slippage against max_slippage_pips.
        # 1 pip ~= 10 bps for 5-decimal FX; for crypto 1 pip is often 1 bps,
        # so we use the conservative 10x conversion and log when exceeded.
        self._check_slippage(report)

        # 7. Post-fill prop rules check
        if (
            report.status is not None
            and report.status.value == "filled"
            and self._config.prop_rules_enabled
            and self._prop_engine
        ):
            allowed, reason = self._prop_engine.check_post_fill(
                report, account_info, positions or []
            )
            if not allowed:
                logger.warning("Prop rules POST-FILL violation: %s", reason)

        # 8. Feed fill to risk systems (impact calibrator expects non-negative bps)
        if report.status is not None and report.status.value == "filled" and self._risk_feed:
            slippage = report.slippage_bps
            realized_bps = abs(slippage) if slippage is not None else 0.0
            # ImpactObservation validates >=0; negative (price improvement) is
            # recorded as 0 cost rather than raising.
            realized_bps = max(0.0, realized_bps)
            try:
                self._risk_feed.record_impact_fill(
                    symbol=report.symbol,
                    quantity=report.quantity,
                    realized_slippage_bps=realized_bps,
                )
            except ValueError:
                logger.warning(
                    "Risk feed rejected impact fill for %s (slippage %.2f bps)",
                    report.symbol,
                    realized_bps,
                )

        self._last_report = report
        return report

    def _build_order_request(
        self,
        proposal: DecisionProposal,
        mark_price: float,
        positions: list[Any] | None = None,
    ) -> OrderRequest | None:
        """Convert proposal action to OrderRequest.

        Deterministic order_id: ``ati-{proposal_id}-{order:04d}-{action}``
        contains no timestamp or random component, so a replay of the same
        proposal produces the identical OrderRequest.
        """
        # Use the gate's primary action (lowest order) deterministically
        action = proposal.primary_action
        if action is None:
            return None
        if action.action_type in (
            ProposedActionType.STAND_ASIDE,
            ProposedActionType.REDUCE_RISK,
        ):
            return None

        symbol: str = proposal.symbol or "BTCUSDT"

        action_type = action.action_type
        # Determine side: enters are explicit, exits invert the existing position.
        if action_type is ProposedActionType.ENTER_LONG:
            side = OrderSide.BUY
        elif action_type is ProposedActionType.ENTER_SHORT:
            side = OrderSide.SELL
        elif action_type is ProposedActionType.SCALE_IN:
            # SCALE_IN adds to the existing direction; fallback to BUY when no
            # position is known. Guard against ambiguity by inspecting live
            # positions when supplied.
            side = self._scale_in_side(symbol, positions)
        elif action_type in (ProposedActionType.EXIT, ProposedActionType.SCALE_OUT):
            exit_side = self._exit_side(symbol, positions)
            side = exit_side if exit_side is not None else OrderSide.SELL
        else:
            return None

        # Determine quantity from proposal action size_fraction (fraction of equity)
        account_equity = proposal.risk_context.account_equity
        if mark_price <= 0:
            return None
        qty = action.size_fraction * account_equity / mark_price
        if qty <= 0:
            return None

        # Deterministic order_id: no clock, no random
        order_id = f"ati-{proposal.proposal_id}-{action.order:04d}-{action_type.value}"
        return OrderRequest(
            order_id=order_id,
            proposal_id=proposal.proposal_id,
            symbol=symbol,
            side=side,
            order_type=OrderType.MARKET,
            quantity=qty,
            limit_price=None,
            created_at=proposal.created_at,
            time_in_force=TimeInForce.GTC,
            post_only=False,
        )

    @staticmethod
    def _scale_in_side(symbol: str, positions: list[Any] | None) -> OrderSide:
        """Infer SCALE_IN side from live positions; default BUY when unknown."""
        if positions:
            for pos in positions:
                pos_symbol = getattr(pos, "symbol", None)
                pos_side = getattr(pos, "side", None)
                if pos_symbol == symbol and pos_side in (OrderSide.BUY, OrderSide.SELL):
                    return pos_side  # type: ignore[no-any-return]
                # dict-like position
                if isinstance(pos, dict) and pos.get("symbol") == symbol:
                    side_val = str(pos.get("side", "")).lower()
                    if side_val == "buy":
                        return OrderSide.BUY
                    if side_val == "sell":
                        return OrderSide.SELL
        return OrderSide.BUY

    @staticmethod
    def _exit_side(symbol: str, positions: list[Any] | None) -> OrderSide | None:
        """Infer exit side as the opposite of the open position, if known."""
        if not positions:
            return None
        for pos in positions:
            pos_symbol = getattr(pos, "symbol", None)
            pos_side = getattr(pos, "side", None)
            if pos_symbol == symbol and pos_side in (OrderSide.BUY, OrderSide.SELL):
                return OrderSide.SELL if pos_side is OrderSide.BUY else OrderSide.BUY
            if isinstance(pos, dict) and pos.get("symbol") == symbol:
                side_val = str(pos.get("side", "")).lower()
                if side_val == "buy":
                    return OrderSide.SELL
                if side_val == "sell":
                    return OrderSide.BUY
        return None

    def _apply_risk_reduction(
        self,
        order: OrderRequest,
        risk_decision: Any,
        proposal: DecisionProposal,
        mark_price: float,
    ) -> OrderRequest:
        """Apply risk gate size reduction using approved_size_fraction.

        ``approved_size_fraction`` is an absolute equity-fraction cap (the gate's
        ``capped = min(limit for ...)``), not a multiplier of the requested
        quantity. The correct reduced quantity is::

            approved_qty = approved_fraction * account_equity / mark_price

        capped to the original request. This preserves the gate's budgets
        (2% per-trade, 1% per-symbol, etc.) exactly; the previous
        ``quantity * approved`` mis-scaled by ``size_fraction``.
        """
        approved = getattr(risk_decision, "approved_size_fraction", None)
        if not isinstance(approved, (int, float)):
            return order
        if not 0 < float(approved) <= 1.0:
            return order
        if mark_price <= 0:
            return order
        approved_f = float(approved)
        account_equity = proposal.risk_context.account_equity
        # Absolute cap quantity implied by the gate
        approved_qty = approved_f * account_equity / mark_price
        # Never increase size; gate is a cap only
        reduced_qty = min(order.quantity, approved_qty)
        reduced_qty = max(float(reduced_qty), 0.0)
        if reduced_qty <= 0:
            return order
        return OrderRequest(
            order_id=order.order_id,
            proposal_id=order.proposal_id,
            symbol=order.symbol,
            side=order.side,
            order_type=order.order_type,
            quantity=reduced_qty,
            limit_price=order.limit_price,
            created_at=order.created_at,
            time_in_force=order.time_in_force,
            post_only=order.post_only,
        )

    def _check_slippage(self, report: ExecutionReport) -> None:
        """Log when realized slippage exceeds the configured pip budget.

        ``max_slippage_pips`` is compared against ``slippage_bps`` using the
        conservative FX conversion 1 pip = 10 bps (so 5 pips = 50 bps). For
        crypto venues where 1 pip ~= 1 bps the check is stricter than needed,
        which is safe: it warns earlier rather than later.
        """
        bps = report.slippage_bps
        if bps is None or report.arrival_price is None:
            return
        max_bps = self._config.max_slippage_pips * 10.0
        if abs(bps) > max_bps:
            logger.warning(
                "Slippage %.2f bps exceeds max %.2f bps (%.1f pips) on %s %s",
                bps,
                max_bps,
                self._config.max_slippage_pips,
                report.symbol,
                report.order_id,
            )

    def _execute_paper(self, order: OrderRequest, mark_price: float) -> ExecutionReport:
        """Execute via paper gateway with deterministic VWAP.

        When the gateway is a :class:`PaperFillEngine` the order is submitted
        through it so VWAP, cap/floor, FOK/IOC/GTC, fees and the synthetic
        2 bps spread (``set_mark_price``) are honoured. For other gateways
        the legacy synthetic fill at ``mark_price`` is preserved for backward
        compatibility. Arrival price is the mid at submission time.
        """
        # Prefer the real gateway path to keep replay == live path fees/slippage
        gateway = self._gateway
        # Detect PaperFillEngine by capability (set_book / submit)
        has_book = hasattr(gateway, "set_book") and hasattr(gateway, "submit")
        if has_book:
            try:
                # Ensure a book exists; synthetic 2 bps spread when none
                book_ready = True
                try:
                    _ = gateway.book  # type: ignore[attr-defined]
                except RuntimeError:
                    book_ready = False
                if not book_ready and mark_price > 0:
                    try:
                        gateway.set_mark_price(mark_price)  # type: ignore[attr-defined]
                    except Exception:
                        gateway.set_book(  # type: ignore[attr-defined]
                            type(gateway.book)(  # type: ignore[attr-defined]
                                best_bid=mark_price * 0.9999,
                                best_ask=mark_price * 1.0001,
                                bid_size=1e9,
                                ask_size=1e9,
                            )
                        )
                report = gateway.submit(order)
                # Ensure arrival_price is populated for slippage measurement
                if report.arrival_price is None:
                    # Fallback to mark mid
                    report = ExecutionReport(
                        order_id=report.order_id,
                        symbol=report.symbol,
                        side=report.side,
                        quantity=report.quantity,
                        average_fill_price=report.average_fill_price,
                        status=report.status,
                        executed_at=report.executed_at,
                        fee=report.fee,
                        funding_cost=report.funding_cost,
                        venue=report.venue,
                        is_maker=report.is_maker,
                        arrival_price=mark_price,
                        latency_ms=report.latency_ms,
                        remaining_quantity=report.remaining_quantity,
                        queue_position=report.queue_position,
                    )
                return report
            except Exception as exc:  # noqa: BLE001
                logger.warning("Paper gateway submit failed, falling back: %s", exc)

        # Fallback synthetic fill (legacy): fill at mark_price
        from backend.domain.execution.order import OrderStatus

        return ExecutionReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            average_fill_price=mark_price if mark_price > 0 else 0.0,
            status=OrderStatus.FILLED,
            executed_at=datetime.now(UTC),
            fee=0.0,
            funding_cost=None,
            venue="paper",
            is_maker=False,
            arrival_price=mark_price,
            latency_ms=1.0,
        )

    def _execute_live(self, order: OrderRequest) -> ExecutionReport:
        """Execute via live gateway."""
        report: ExecutionReport = self._gateway.submit(order)
        if self._config.log_all_orders:
            logger.info(
                "LIVE ORDER %s: %s %s %s @ %.5f",
                report.status.value,
                order.side.value,
                order.symbol,
                order.quantity,
                report.average_fill_price,
            )
        return report

    def _rejected_report(self, proposal: DecisionProposal, reason: str) -> ExecutionReport:
        from backend.domain.execution.order import OrderStatus

        primary = proposal.primary_action
        suffix = f"{primary.order:04d}" if primary is not None else "0000"
        return ExecutionReport(
            order_id=f"rejected-{proposal.proposal_id}-{suffix}",
            symbol=proposal.symbol,
            side=OrderSide.BUY,
            quantity=0.0,
            average_fill_price=0.0,
            status=OrderStatus.REJECTED,
            executed_at=datetime.now(UTC),
            fee=None,
            funding_cost=None,
            venue="none",
            is_maker=None,
            arrival_price=None,
            latency_ms=None,
        )

    def get_status(self) -> dict[str, Any]:
        return {
            "mode": "paper" if self._config.paper_mode else "live",
            "prop_firm": self._config.prop_firm,
            "prop_rules_enabled": self._config.prop_rules_enabled,
            "live_authorized": self._config.live_trading_authorized,
            "last_proposal": self._last_proposal.proposal_id if self._last_proposal else None,
            "last_report": self._last_report.order_id if self._last_report else None,
        }


def create_execution_core_from_env(
    order_gateway: Any,
    risk_feed: Any = None,
    supervisor: Any = None,
    prop_engine: Any = None,
    paper_fallback: Any = None,
) -> ExecutionCore:
    """Build ExecutionCore from environment variables."""
    import os

    config = ExecutionConfig(
        risk_config=RiskGateConfig(
            max_risk_per_trade_pct=float(os.getenv("RISK_PER_TRADE_PCT", "0.02")),
            max_risk_per_symbol_pct=float(os.getenv("RISK_PER_SYMBOL_PCT", "0.01")),
            max_portfolio_risk_pct=float(os.getenv("RISK_PORTFOLIO_PCT", "0.03")),
            max_daily_loss_pct=float(os.getenv("RISK_DAILY_LOSS_PCT", "0.06")),
            max_monthly_loss_pct=float(os.getenv("RISK_MONTHLY_LOSS_PCT", "0.10")),
            max_drawdown_pct=float(os.getenv("RISK_MAX_DRAWDOWN_PCT", "0.20")),
            veto_on_toxicity=os.getenv("RISK_VETO_TOXICITY", "true").lower() == "true",
            veto_on_excess_impact=os.getenv("RISK_VETO_IMPACT", "true").lower() == "true",
        ),
        live_trading_authorized=os.getenv("LIVE_TRADING_AUTHORIZED", "false").lower() == "true",
        paper_mode=os.getenv("PAPER_MODE", "true").lower() == "true",
        prop_firm=os.getenv("PROP_FIRM"),
        prop_rules_enabled=os.getenv("PROP_RULES_ENABLED", "true").lower() == "true",
        max_slippage_pips=float(os.getenv("MAX_SLIPPAGE_PIPS", "5.0")),
        max_order_age_seconds=float(os.getenv("MAX_ORDER_AGE_SEC", "30.0")),
    )

    risk_gate = CircuitBreakerRiskGate(config.risk_config)

    return ExecutionCore(
        config=config,
        order_gateway=order_gateway,
        risk_gate=risk_gate,
        risk_feed=risk_feed,
        supervisor=supervisor,
        prop_engine=prop_engine,
        paper_fallback=paper_fallback,
    )
