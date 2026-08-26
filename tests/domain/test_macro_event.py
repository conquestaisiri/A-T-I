# tests/domain/test_macro_event.py
"""Domain tests: economic-value parsing, event identity, surprise arithmetic."""

from __future__ import annotations

from datetime import datetime

from backend.domain.macro.event import (
    MacroEventData,
    compute_event_surprise,
    currencies_for_symbol,
    normalise_impact,
    parse_economic_value,
)


class TestParseEconomicValue:
    def test_percent(self) -> None:
        assert parse_economic_value("0.3%") == 0.3

    def test_negative_decimal(self) -> None:
        assert parse_economic_value("-11.0") == -11.0

    def test_thousands_suffix(self) -> None:
        assert parse_economic_value("9.5K") == 9500.0
        assert parse_economic_value("620K") == 620_000.0

    def test_millions_and_billions(self) -> None:
        assert parse_economic_value("1.6M") == 1_600_000.0
        assert parse_economic_value("-100.8B") == -100_800_000_000.0

    def test_auction_yield_pipe(self) -> None:
        assert parse_economic_value("4.00|1.7") == 4.0

    def test_zero_is_preserved(self) -> None:
        assert parse_economic_value("0.0%") == 0.0

    def test_empty_and_none(self) -> None:
        assert parse_economic_value("") is None
        assert parse_economic_value(None) is None

    def test_garbage_is_none(self) -> None:
        assert parse_economic_value("n/a") is None


def test_normalise_impact_degrades_unknown_to_low() -> None:
    assert normalise_impact("high") == "High"
    assert normalise_impact("MEDIUM") == "Medium"
    assert normalise_impact("") == "Low"
    assert normalise_impact("extreme") == "Low"


class TestFromFFJson:
    def _item(self) -> dict[str, str]:
        return {
            "title": "Core PCE Price Index m/m",
            "country": "USD",
            "date": "2026-08-26T08:30:00-04:00",
            "impact": "High",
            "forecast": "0.2%",
            "previous": "0.1%",
        }

    def test_maps_all_fields(self) -> None:
        event = MacroEventData.from_ff_json(self._item())
        assert event is not None
        assert event.currency == "USD"
        assert event.title == "Core PCE Price Index m/m"
        assert event.impact == "High"
        assert event.forecast == 0.2
        assert event.previous == 0.1
        assert event.actual is None
        assert not event.released

    def test_stable_event_identity(self) -> None:
        one = MacroEventData.from_ff_json(self._item())
        two = MacroEventData.from_ff_json(self._item())
        assert one is not None and two is not None
        assert one.event_id == two.event_id

    def test_rejects_entries_without_date(self) -> None:
        item = self._item()
        item["date"] = ""
        assert MacroEventData.from_ff_json(item) is None


def test_revision_trap_net_surprise() -> None:
    """§6 case: naive headline flips sign once revisions are respected."""
    surprise = compute_event_surprise(actual=2.6, forecast=2.5, previous=2.4, revised_previous=2.7)
    assert surprise.headline_surprise is not None
    assert abs(surprise.headline_surprise - 0.1) < 1e-9
    assert surprise.revision_delta is not None
    assert abs(surprise.revision_delta - 0.3) < 1e-9
    # Net vs the *effective* prior is dovish, not hawkish.
    assert surprise.net_surprise is not None
    assert abs(surprise.net_surprise - (-0.1)) < 1e-9


def test_surprise_without_revisions_uses_previous_as_prior() -> None:
    surprise = compute_event_surprise(actual=3.8, forecast=3.2, previous=3.1)
    assert surprise.headline_surprise is not None
    assert abs(surprise.headline_surprise - 0.6) < 1e-9
    assert surprise.revision_delta is None
    assert surprise.net_surprise is not None
    assert abs(surprise.net_surprise - 0.7) < 1e-9


def test_roundtrip_as_dict_from_dict() -> None:
    event = MacroEventData(
        event_id="abc123",
        currency="EUR",
        title="German ifo Business Climate",
        scheduled_at=datetime.fromisoformat("2026-08-25T04:00:00-04:00"),
        impact="Low",
        forecast=87.2,
        previous=86.6,
        actual=None,
    )
    restored = MacroEventData.from_dict(event.as_dict())
    assert restored == event


class TestCurrenciesForSymbol:
    def test_crypto_quotes_usd(self) -> None:
        assert currencies_for_symbol("BTCUSDT") == {"USD"}
        assert currencies_for_symbol("SOLUSDT") == {"USD"}

    def test_fx_pair_exposes_both_legs(self) -> None:
        assert currencies_for_symbol("frxEURUSD") == {"EUR", "USD"}
        assert currencies_for_symbol("EURUSD") == {"EUR", "USD"}
        assert currencies_for_symbol("GBP/JPY") == {"GBP", "JPY"}

    def test_unknown_shape_returns_empty(self) -> None:
        assert currencies_for_symbol("XAU") == set()
        assert currencies_for_symbol("") == set()
