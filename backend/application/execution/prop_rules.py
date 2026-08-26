"""FundingPips Prop Rule Engine.

Implements FundingPips-specific rules for evaluation and Master accounts.

Rules (from FundingPips documentation):
- Max Daily Loss: 5% (Flex), 4% (Classic), 3% (Rapid)
- Max Total Loss: 10% (Flex), 8% (Classic), 6% (Rapid)
- Profit Target: 10% (Flex), 8% (Classic), 5% (Rapid) - Phase 1; 5% Phase 2
- Max Daily Drawdown: included in max daily loss
- News Restrictions: No trading 5 min before/after high-impact news (Flex/Classic)
- Weekend Holding: Not allowed (Rapid), Allowed (Flex/Classic)
- Consistency Rule: Best day < 50% of total profit (Flex)
- Min Trading Days: 3 (Flex/Classic), 1 (Rapid)
- Max Leverage: 1:30 (Flex/Classic), 1:50 (Rapid)
- EA/Automated Trading: Allowed
- Copy Trading: Not allowed
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class FundingPipsModel(Enum):
    FLEX = "flex"
    CLASSIC = "classic"
    RAPID = "rapid"


@dataclass(frozen=True, slots=True)
class FundingPipsRules:
    """Rule parameters for a specific FundingPips model."""

    model: str
    max_daily_loss_pct: float
    max_total_loss_pct: float
    profit_target_pct_phase1: float
    profit_target_pct_phase2: float
    news_restriction_minutes: int
    weekend_holding_allowed: bool
    consistency_rule: bool  # best day < 50% of total profit
    min_trading_days: int
    max_leverage: int
    ea_allowed: bool
    copy_trading_allowed: bool


FUNDINGPIPS_RULES = {
    "flex": FundingPipsRules(
        model="flex",
        max_daily_loss_pct=0.05,
        max_total_loss_pct=0.10,
        profit_target_pct_phase1=0.10,
        profit_target_pct_phase2=0.05,
        news_restriction_minutes=5,
        weekend_holding_allowed=True,
        consistency_rule=True,
        min_trading_days=3,
        max_leverage=30,
        ea_allowed=True,
        copy_trading_allowed=False,
    ),
    "classic": FundingPipsRules(
        model="classic",
        max_daily_loss_pct=0.04,
        max_total_loss_pct=0.08,
        profit_target_pct_phase1=0.08,
        profit_target_pct_phase2=0.05,
        news_restriction_minutes=5,
        weekend_holding_allowed=True,
        consistency_rule=False,
        min_trading_days=3,
        max_leverage=30,
        ea_allowed=True,
        copy_trading_allowed=False,
    ),
    "rapid": FundingPipsRules(
        model="rapid",
        max_daily_loss_pct=0.03,
        max_total_loss_pct=0.06,
        profit_target_pct_phase1=0.05,
        profit_target_pct_phase2=0.05,
        news_restriction_minutes=5,
        weekend_holding_allowed=False,
        consistency_rule=False,
        min_trading_days=1,
        max_leverage=50,
        ea_allowed=True,
        copy_trading_allowed=False,
    ),
}


class FundingPipsEngine:
    """FundingPips rule engine implementation."""

    def __init__(
        self,
        model: str = "flex",
        account_type: str = "evaluation",  # "evaluation" or "master"
        starting_equity: float = 10000.0,
        high_impact_news_times: list[datetime] | None = None,
    ) -> None:
        if model not in FUNDINGPIPS_RULES:
            raise ValueError(
                f"Unknown model: {model}. Choose from: {list(FUNDINGPIPS_RULES.keys())}"
            )

        self._rules = FUNDINGPIPS_RULES[model]
        self._model = model
        self._account_type = account_type  # "evaluation" or "master"
        self._starting_equity = starting_equity
        self._high_impact_news = high_impact_news_times or []

        # State tracking
        self._daily_pnl = 0.0
        self._total_pnl = 0.0
        self._daily_trades: list[dict[str, Any]] = []
        self._all_trades: list[dict[str, Any]] = []
        self._trading_days: set[str] = set()
        self._best_day_pnl = 0.0
        self._current_day = datetime.now(UTC).date()
        self._phase = 1  # 1 or 2
        self._phase_target_met = False

        logger.info(
            "FundingPipsEngine initialized: model=%s type=%s equity=%.2f",
            model,
            account_type,
            starting_equity,
        )

    @property
    def rules(self) -> FundingPipsRules:
        return self._rules

    def check_pre_trade(
        self,
        proposal: Any,
        account_info: Any,
        positions: list[Any],
    ) -> tuple[bool, str | None]:
        """Check if trade is allowed under FundingPips rules."""

        # 1. EA/Automated trading check
        if not self._rules.ea_allowed:
            return False, "Automated trading (EA) not allowed for this model"

        # 2. Weekend holding check
        if not self._rules.weekend_holding_allowed:
            now = datetime.now(UTC)
            if now.weekday() >= 5 and positions:  # Saturday=5, Sunday=6
                return False, "Weekend holding not allowed for this model"

        # 3. News restriction check
        if self._rules.news_restriction_minutes > 0:
            now = datetime.now(UTC)
            for news_time in self._high_impact_news:
                diff = abs((now - news_time).total_seconds() / 60)
                if diff < self._rules.news_restriction_minutes:
                    return (
                        False,
                        f"High-impact news within {self._rules.news_restriction_minutes} minutes",
                    )

        # 4. Daily loss limit
        equity = getattr(account_info, "equity", 0) or getattr(account_info, "balance", 0)
        if equity > 0:
            daily_loss_pct = abs(min(0, self._daily_pnl)) / equity
            if daily_loss_pct >= self._rules.max_daily_loss_pct:
                limit = self._rules.max_daily_loss_pct
                msg = f"Daily loss limit reached: {daily_loss_pct:.2%} >= {limit:.2%}"
                return False, msg

        # 5. Total loss limit
        if equity > 0:
            total_loss_pct = abs(min(0, self._total_pnl)) / self._starting_equity
            if total_loss_pct >= self._rules.max_total_loss_pct:
                limit = self._rules.max_total_loss_pct
                msg = f"Total loss limit reached: {total_loss_pct:.2%} >= {limit:.2%}"
                return False, msg

        # 6. Leverage check (would need position sizing info)
        # This is a placeholder - actual leverage check needs position size

        return True, None

    def check_post_fill(
        self,
        report: Any,
        account_info: Any,
        positions: list[Any],
    ) -> tuple[bool, str | None]:
        """Check post-fill for rule violations."""

        # Update tracking
        _ = getattr(report, "quantity", 0) * getattr(report, "average_fill_price", 0)
        # This is simplified - real PnL would come from execution report

        # Update daily tracking
        today = datetime.now(UTC).date()
        if today != self._current_day:
            self._current_day = today
            self._daily_pnl = 0.0
            self._daily_trades = []

        # Track trading day
        self._trading_days.add(str(today))

        return True, None

    def get_rules_summary(self) -> dict[str, Any]:
        return {
            "model": self._model,
            "account_type": self._account_type,
            "starting_equity": self._starting_equity,
            "max_daily_loss_pct": self._rules.max_daily_loss_pct,
            "max_total_loss_pct": self._rules.max_total_loss_pct,
            "profit_target_pct_phase1": self._rules.profit_target_pct_phase1,
            "profit_target_pct_phase2": self._rules.profit_target_pct_phase2,
            "news_restriction_minutes": self._rules.news_restriction_minutes,
            "weekend_holding_allowed": self._rules.weekend_holding_allowed,
            "consistency_rule": self._rules.consistency_rule,
            "min_trading_days": self._rules.min_trading_days,
            "max_leverage": self._rules.max_leverage,
            "ea_allowed": self._rules.ea_allowed,
            "copy_trading_allowed": self._rules.copy_trading_allowed,
            "current_status": {
                "daily_pnl": self._daily_pnl,
                "total_pnl": self._total_pnl,
                "trading_days": len(self._trading_days),
                "phase": self._phase,
                "phase_target_met": self._phase_target_met,
            },
        }

    def record_fill(self, report: Any) -> None:
        """Record a fill for tracking PnL and rules."""
        # This would be called from ExecutionCore after a fill
        pass


class ForTradersEngine:
    """For Traders (ForTraders.com) rule engine.

    Pay After Pass model rules:
    - Entry fee + activation fee after pass
    - No time limit
    - Daily drawdown: 5%
    - Max drawdown: 10%
    - Profit target: 10% (Classic), varies by plan
    - No news restrictions (officially)
    - Weekend holding: Allowed
    - No consistency rule
    - Min trading days: 0
    - KYC only after passing
    """

    def __init__(
        self,
        plan: str = "classic",  # "classic", "strike", "fast"
        account_type: str = "evaluation",
        starting_equity: float = 10000.0,
    ) -> None:
        self._plan = plan
        self._account_type = account_type
        self._starting_equity = starting_equity

        # Plan-specific rules
        self._rules = {
            "classic": {
                "max_daily_loss_pct": 0.05,
                "max_total_loss_pct": 0.10,
                "profit_target_pct": 0.10,
            },
            "strike": {
                "max_daily_loss_pct": 0.04,
                "max_total_loss_pct": 0.08,
                "profit_target_pct": 0.08,
            },
            "fast": {
                "max_daily_loss_pct": 0.03,
                "max_total_loss_pct": 0.06,
                "profit_target_pct": 0.05,
            },
        }[plan]

        self._daily_pnl = 0.0
        self._total_pnl = 0.0
        self._current_day = datetime.now(UTC).date()

        logger.info(
            "ForTradersEngine initialized: plan=%s type=%s equity=%.2f",
            plan,
            account_type,
            starting_equity,
        )

    def check_pre_trade(
        self,
        proposal: Any,
        account_info: Any,
        positions: list[Any],
    ) -> tuple[bool, str | None]:

        equity = getattr(account_info, "equity", 0) or getattr(account_info, "balance", 0)

        # Daily loss limit
        if equity > 0:
            daily_loss_pct = abs(min(0, self._daily_pnl)) / equity
            if daily_loss_pct >= self._rules["max_daily_loss_pct"]:
                return False, f"Daily loss limit reached: {daily_loss_pct:.2%}"

        # Total loss limit
        if equity > 0:
            total_loss_pct = abs(min(0, self._total_pnl)) / self._starting_equity
            if total_loss_pct >= self._rules["max_total_loss_pct"]:
                return False, f"Total loss limit reached: {total_loss_pct:.2%}"

        return True, None

    def check_post_fill(
        self,
        report: Any,
        account_info: Any,
        positions: list[Any],
    ) -> tuple[bool, str | None]:
        return True, None

    def get_rules_summary(self) -> dict[str, Any]:
        return {
            "plan": self._plan,
            "account_type": self._account_type,
            "starting_equity": self._starting_equity,
            "rules": self._rules,
        }


def create_prop_engine(
    firm: str,
    model: str = "flex",
    account_type: str = "evaluation",
    starting_equity: float = 10000.0,
    **kwargs: Any,
) -> Any:
    """Factory function to create prop firm rule engine.

    Args:
        firm: "fundingpips" or "fortraders"
        model: Model/plan name
        account_type: "evaluation" or "master"
        starting_equity: Starting account equity
        **kwargs: Additional firm-specific parameters

    Returns:
        PropRuleEngine instance
    """
    firm = firm.lower()

    if firm == "fundingpips":
        return FundingPipsEngine(
            model=model,
            account_type=account_type,
            starting_equity=starting_equity,
            **kwargs,
        )
    elif firm in ("fortraders", "for_tradrs", "for-traders"):
        return ForTradersEngine(
            plan=model,
            account_type=account_type,
            starting_equity=starting_equity,
            **kwargs,
        )
    else:
        raise ValueError(f"Unknown prop firm: {firm}. Supported: fundingpips, fortradrs")
