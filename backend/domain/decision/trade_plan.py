# backend/domain/decision/trade_plan.py
"""Pre- and post-trade execution plans (blueprint Tier 1).

A ``PreTradePlan`` is the mandatory protective bracket every risk-increasing
action must carry before it can be executed: a stop-loss and a take-profit,
each expressible as an absolute price or a percentage distance, plus the risk
budget the trade is allowed to consume. A ``PostTradePlan`` documents how the
position is expected to exit and how the outcome feeds the learning loop.

The plan belongs to the proposal — the AI states its intent, the risk gate
verifies it, and the simulator turns the plan into a live OCO bracket on the
paper position.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class StopLevel:
    """A protective or target price level.

    Either an absolute ``price`` or a ``distance_pct`` from the fill price may
    be given; both must be positive when present. A level is only ``defined``
    when at least one is provided.
    """

    price: float | None = None
    distance_pct: float | None = None

    def __post_init__(self) -> None:
        if self.price is not None and self.price <= 0:
            raise ValueError("stop level price must be positive")
        if self.distance_pct is not None and self.distance_pct <= 0:
            raise ValueError("stop level distance_pct must be positive")

    @property
    def defined(self) -> bool:
        return self.price is not None or self.distance_pct is not None

    def as_dict(self) -> dict[str, Any]:
        return {"price": self.price, "distance_pct": self.distance_pct}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StopLevel:
        price = data.get("price")
        distance = data.get("distance_pct")
        return cls(
            price=float(price) if price is not None else None,
            distance_pct=float(distance) if distance is not None else None,
        )


@dataclass(frozen=True, slots=True)
class PreTradePlan:
    """Protective and sizing intent declared before entry.

    Attributes
    ----------
    entry: str
        Entry instruction, e.g. "market".
    entry_type: str
        Order type, e.g. "market" or "limit".
    entry_reference: str
        Reference price used for the entry, e.g. "mark_price" or "micro_price".
    entry_condition: str
        Optional condition that must hold for the entry to trigger.
    stop_loss: StopLevel
        Protective stop level (price and/or distance).
    take_profit: StopLevel
        Target level (price and/or distance).
    risk_per_trade_pct: float
        Maximum fraction of account equity this trade may lose (2% default).
    max_loss: float | None
        Absolute maximum loss in account currency, if known.
    risk_reward_ratio: float
        Expected reward per unit of risk (2.0 default). 0 disables the check.
    max_position_duration: str
        Maximum holding period before forced review, e.g. "24h".
    duration_alert_if_reached: bool
        Whether to alert when the max duration is reached.
    funding_rule_violation: bool
        Set when the venue marks funding against the position.
    """

    entry: str = "market"
    entry_type: str = "market"
    entry_reference: str = "mark_price"
    entry_condition: str = ""
    stop_loss: StopLevel = field(default_factory=StopLevel)
    take_profit: StopLevel = field(default_factory=StopLevel)
    risk_per_trade_pct: float = 0.02
    max_loss: float | None = None
    risk_reward_ratio: float = 2.0
    max_position_duration: str = "24h"
    duration_alert_if_reached: bool = True
    funding_rule_violation: bool = False

    def __post_init__(self) -> None:
        if not self.entry:
            raise ValueError("entry must be a non-empty string")
        if not 0.0 < self.risk_per_trade_pct <= 1.0:
            raise ValueError("risk_per_trade_pct must be in (0, 1]")
        if self.risk_reward_ratio < 0.0:
            raise ValueError("risk_reward_ratio cannot be negative")
        if self.max_loss is not None and self.max_loss <= 0:
            raise ValueError("max_loss must be positive when given")

    @property
    def has_bracket(self) -> bool:
        """Whether both a stop-loss and a take-profit are defined."""
        return self.stop_loss.defined and self.take_profit.defined

    @property
    def stop_distance_pct(self) -> float | None:
        """Stop-loss distance as a fraction of the fill price, if declared."""
        return self.stop_loss.distance_pct

    @property
    def take_profit_distance_pct(self) -> float | None:
        """Take-profit distance as a fraction of the fill price, if declared."""
        return self.take_profit.distance_pct

    def as_dict(self) -> dict[str, Any]:
        return {
            "entry": self.entry,
            "entry_type": self.entry_type,
            "entry_reference": self.entry_reference,
            "entry_condition": self.entry_condition,
            "stop_loss": self.stop_loss.as_dict(),
            "take_profit": self.take_profit.as_dict(),
            "risk_per_trade_pct": self.risk_per_trade_pct,
            "max_loss": self.max_loss,
            "risk_reward_ratio": self.risk_reward_ratio,
            "max_position_duration": self.max_position_duration,
            "duration_alert_if_reached": self.duration_alert_if_reached,
            "funding_rule_violation": self.funding_rule_violation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PreTradePlan:
        return cls(
            entry=str(data.get("entry", "market")),
            entry_type=str(data.get("entry_type", "market")),
            entry_reference=str(data.get("entry_reference", "mark_price")),
            entry_condition=str(data.get("entry_condition", "")),
            stop_loss=StopLevel.from_dict(data.get("stop_loss", {})),
            take_profit=StopLevel.from_dict(data.get("take_profit", {})),
            risk_per_trade_pct=float(data.get("risk_per_trade_pct", 0.02)),
            max_loss=(float(data["max_loss"]) if data.get("max_loss") is not None else None),
            risk_reward_ratio=float(data.get("risk_reward_ratio", 2.0)),
            max_position_duration=str(data.get("max_position_duration", "24h")),
            duration_alert_if_reached=bool(data.get("duration_alert_if_reached", True)),
            funding_rule_violation=bool(data.get("funding_rule_violation", False)),
        )


@dataclass(frozen=True, slots=True)
class PostTradePlan:
    """How a position is expected to exit and be reviewed.

    Attributes
    ----------
    exit_trigger: str
        What normally closes the position, e.g. "OCO bracket".
    exit_event: str
        Concrete exit events, e.g. "stop_loss OR take_profit OR manual OR funding".
    validation_notes: tuple[str, ...]
        Notes recorded against the plan after the fact.
    feedback: str
        Where the outcome is routed for learning.
    """

    exit_trigger: str = "OCO bracket"
    exit_event: str = "stop_loss OR take_profit OR manual OR funding"
    validation_notes: tuple[str, ...] = ("None",)
    feedback: str = "Review and record via the learning loop."

    def as_dict(self) -> dict[str, Any]:
        return {
            "exit_trigger": self.exit_trigger,
            "exit_event": self.exit_event,
            "validation_notes": list(self.validation_notes),
            "feedback": self.feedback,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PostTradePlan:
        notes = data.get("validation_notes") or ["None"]
        return cls(
            exit_trigger=str(data.get("exit_trigger", "OCO bracket")),
            exit_event=str(data.get("exit_event", "stop_loss OR take_profit OR manual OR funding")),
            validation_notes=tuple(str(note) for note in notes),
            feedback=str(data.get("feedback", "Review and record via the learning loop.")),
        )


def stop_distance_from_volatility(
    std_dev: float | None,
    *,
    multiple: float = 2.0,
    floor: float = 0.01,
    default: float = 0.02,
) -> float:
    """Stop distance from an annualised/feature volatility standard deviation.

    Uses ``multiple * std_dev`` with a floor so brackets are never unrealistically
    tight; falls back to ``default`` when volatility is unavailable.
    """
    if std_dev is not None and std_dev > 0.0:
        return min(0.50, max(floor, multiple * std_dev))
    return default


def bracket_plan(
    stop_distance_pct: float,
    *,
    risk_per_trade_pct: float = 0.02,
    risk_reward_ratio: float = 2.0,
) -> PreTradePlan:
    """A complete protective bracket at a 1:R / R×RR risk/reward profile."""
    return PreTradePlan(
        entry="market",
        entry_type="market",
        entry_reference="mark_price",
        stop_loss=StopLevel(distance_pct=stop_distance_pct),
        take_profit=StopLevel(distance_pct=stop_distance_pct * risk_reward_ratio),
        risk_per_trade_pct=risk_per_trade_pct,
        max_loss=None,
        risk_reward_ratio=risk_reward_ratio,
        max_position_duration="24h",
        duration_alert_if_reached=True,
    )
