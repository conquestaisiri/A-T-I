# backend/domain/macro/event.py
"""Economic-event domain: parsing, identity, and surprise arithmetic.

Turns Forex Factory's official weekly JSON export into typed records and
computes expectation-vs-reality measures, including the revision trap: when
the prior reading is revised *before* the new actual lands, the naive
``actual - forecast`` surprise mislabels the print. The net informational
surprise is measured against the effective prior (revised when present).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from typing import Any

IMPACT_HIGH = "High"
IMPACT_MEDIUM = "Medium"
IMPACT_LOW = "Low"
_IMPACTS = frozenset({IMPACT_HIGH, IMPACT_MEDIUM, IMPACT_LOW})


def parse_economic_value(raw: str | None) -> float | None:
    """Parse an FF economic value into a float.

    Handles ``"0.3%"``, ``"-11.0"``, ``"9.5K"``, ``"1.6M"``, ``"620K"``,
    auction strings like ``"4.00|1.7"`` (yield|coverage — yield wins),
    and empty/missing fields. Returns ``None`` when nothing parseable.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    # Auction format "4.00|1.7": take the first component.
    text = text.split("|", 1)[0].strip().rstrip("%")
    if not text:
        return None
    multiplier = 1.0
    if text[-1] in "Kk":
        multiplier, text = 1_000.0, text[:-1]
    elif text[-1] in "Mm":
        multiplier, text = 1_000_000.0, text[:-1]
    elif text[-1] in "Bb":
        multiplier, text = 1_000_000_000.0, text[:-1]
    try:
        return float(text) * multiplier
    except ValueError:
        return None


def normalise_impact(raw: str | None) -> str:
    """Normalise FF impact labels; unknown labels degrade to Low."""
    text = (raw or "").strip().title()
    return text if text in _IMPACTS else IMPACT_LOW


@dataclass(frozen=True, slots=True)
class MacroEventData:
    """One scheduled/released economic event (schema of record)."""

    event_id: str
    currency: str
    title: str
    scheduled_at: datetime
    impact: str
    forecast: float | None
    previous: float | None
    actual: float | None
    forecast_raw: str = ""
    previous_raw: str = ""
    actual_raw: str = ""

    @property
    def released(self) -> bool:
        """True once an actual value has been published."""
        return self.actual is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "currency": self.currency,
            "title": self.title,
            "scheduled_at": self.scheduled_at.isoformat(),
            "impact": self.impact,
            "forecast": self.forecast,
            "previous": self.previous,
            "actual": self.actual,
            "forecast_raw": self.forecast_raw,
            "previous_raw": self.previous_raw,
            "actual_raw": self.actual_raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MacroEventData:
        return cls(
            event_id=str(data["event_id"]),
            currency=str(data["currency"]),
            title=str(data["title"]),
            scheduled_at=datetime.fromisoformat(str(data["scheduled_at"])),
            impact=normalise_impact(str(data.get("impact", ""))),
            forecast=_opt_float(data.get("forecast")),
            previous=_opt_float(data.get("previous")),
            actual=_opt_float(data.get("actual")),
            forecast_raw=str(data.get("forecast_raw", "")),
            previous_raw=str(data.get("previous_raw", "")),
            actual_raw=str(data.get("actual_raw", "")),
        )

    @classmethod
    def from_ff_json(cls, item: dict[str, Any]) -> MacroEventData | None:
        """Build from one entry of the official weekly JSON export.

        Returns ``None`` for entries without a usable timestamp (rare).
        """
        title = str(item.get("title", "")).strip()
        currency = str(item.get("country", "")).strip().upper()
        raw_date = str(item.get("date", "")).strip()
        if not title or not currency or not raw_date:
            return None
        try:
            scheduled = datetime.fromisoformat(raw_date)
        except ValueError:
            return None
        if scheduled.tzinfo is None:  # pragma: no cover - feed always offsets
            return None
        forecast_raw = str(item.get("forecast", "") or "")
        previous_raw = str(item.get("previous", "") or "")
        actual_raw = str(item.get("actual", "") or "")
        identity = f"{currency}|{title}|{scheduled.isoformat()}"
        event_id = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:16]
        return cls(
            event_id=event_id,
            currency=currency,
            title=title,
            scheduled_at=scheduled.astimezone(scheduled.tzinfo),
            impact=normalise_impact(str(item.get("impact", ""))),
            forecast=parse_economic_value(forecast_raw),
            previous=parse_economic_value(previous_raw),
            actual=parse_economic_value(actual_raw),
            forecast_raw=forecast_raw,
            previous_raw=previous_raw,
            actual_raw=actual_raw,
        )


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class EventSurprise:
    """Expectation-vs-reality measures for one released event."""

    headline_surprise: float | None
    revision_delta: float | None
    net_surprise: float | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline_surprise": self.headline_surprise,
            "revision_delta": self.revision_delta,
            "net_surprise": self.net_surprise,
        }


def currencies_for_symbol(symbol: str) -> set[str]:
    """Map a tradeable symbol to its macro-currency exposures.

    Crypto quotes are USD-quoted books (``BTCUSDT`` → ``{"USD"}``); FX pairs
    expose both legs (``frxEURUSD``/``EURUSD`` → ``{"EUR", "USD"}``). Unknown
    shapes return an empty set (no veto coverage — never guessed).
    """
    text = symbol.strip().upper()
    for prefix in ("FRX", "FX:", "FX"):
        if text.startswith(prefix) and len(text) > len(prefix):
            text = text[len(prefix) :]
            break
    text = text.replace("/", "").replace("_", "").replace("-", "")
    if len(text) == 6 and text.isalpha():
        return {text[:3], text[3:]}
    if text.endswith("USDT") and len(text) > 4:
        return {"USD"}
    if text.endswith("USD") and len(text) > 3:
        return {"USD"}
    return set()


def compute_event_surprise(
    *,
    actual: float,
    forecast: float | None,
    previous: float | None,
    revised_previous: float | None = None,
) -> EventSurprise:
    """Surprise arithmetic, revision-aware.

    - ``headline_surprise``: ``actual - forecast`` (what naive systems use).
    - ``revision_delta``: how much the prior moved before this print.
    - ``net_surprise``: ``actual - effective_prior`` where the effective prior
      is the *revised* previous when present. This is the honest informational
      content of the release against the data the market actually had.

    Example (the §6 trap): previous 2.4, revised 2.7, forecast 2.5,
    actual 2.6 → headline +0.1 (hawkish-looking) but net −0.1 (dovish truth):
    the underlying data was already stronger than the market's anchor.
    """
    headline = actual - forecast if forecast is not None else None
    revision = (
        revised_previous - previous
        if revised_previous is not None and previous is not None
        else None
    )
    effective_prior = revised_previous if revised_previous is not None else previous
    net = actual - effective_prior if effective_prior is not None else None
    return EventSurprise(headline_surprise=headline, revision_delta=revision, net_surprise=net)
