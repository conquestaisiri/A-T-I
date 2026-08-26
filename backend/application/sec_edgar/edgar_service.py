# backend/application/sec_edgar/edgar_service.py
"""SEC EDGAR insider trading and 13F institutional holdings service.

Uses edgartools (MIT) to fetch:
- Form 4: insider transactions (buys/sells by officers/directors/10% owners)
- 13F: institutional holdings (quarterly, >$100M AUM managers)

Free, no API key required. Caches results for configurable TTL.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from edgar import get_filings, get_insider_transaction_filings  # type: ignore[import-untyped]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class InsiderTransaction:
    """Single insider transaction from Form 4."""

    symbol: str
    insider_name: str
    insider_title: str
    transaction_date: datetime
    transaction_type: str  # "P" = purchase, "S" = sale
    shares: float
    price_per_share: float | None
    value: float | None  # shares * price (if available)
    is_derivative: bool
    filing_date: datetime


@dataclass(frozen=True, slots=True)
class InstitutionalHolding:
    """Single institutional holding from 13F."""

    symbol: str
    institution_name: str
    cik: str
    shares: float
    value_usd: float
    report_date: datetime
    filing_date: datetime


class EdgarService:
    """Background service fetching SEC EDGAR insider and 13F data."""

    def __init__(
        self,
        *,
        symbols: tuple[str, ...] = ("BTC", "ETH", "SOL", "BNB", "XRP"),
        update_interval_hours: int = 24,
        lookback_days: int = 90,
    ) -> None:
        self._symbols = symbols
        self._update_interval = update_interval_hours * 3600
        self._lookback_days = lookback_days
        self._running = False
        self._task: asyncio.Task[None] | None = None

        # Caches
        self._insider_cache: dict[str, list[InsiderTransaction]] = defaultdict(list)
        self._institutional_cache: dict[str, list[InstitutionalHolding]] = defaultdict(list)
        self._last_update: datetime | None = None

        # Symbol -> CIK mapping (crypto doesn't have CIKs, but we can map to proxy stocks)
        # For crypto, we track proxy public companies (MSTR, COIN, RIOT, MARA, etc.)
        self._symbol_cik_map = {
            "BTC": ["0001567576"],  # MicroStrategy (MSTR)
            "ETH": ["0001567576", "0001595349"],  # MSTR, Coinbase (COIN)
            "SOL": ["0001595349"],  # Coinbase
            "BNB": ["0001595349"],  # Coinbase
            "XRP": ["0001595349"],  # Coinbase
        }

    @property
    def last_update(self) -> datetime | None:
        return self._last_update

    def get_insider_transactions(self, symbol: str) -> list[InsiderTransaction]:
        """Get cached insider transactions for a symbol."""
        return self._insider_cache.get(symbol.upper(), [])

    def get_institutional_holdings(self, symbol: str) -> list[InstitutionalHolding]:
        """Get cached 13F institutional holdings for a symbol."""
        return self._institutional_cache.get(symbol.upper(), [])

    def get_insider_signal(self, symbol: str) -> dict[str, Any]:
        """Compute aggregated insider signal for a symbol."""
        txns = self.get_insider_transactions(symbol)
        if not txns:
            return {"signal": 0.0, "count": 0, "net_shares": 0.0}

        # Filter to last 30 days
        cutoff = datetime.now(UTC) - timedelta(days=30)
        recent = [t for t in txns if t.transaction_date >= cutoff]

        if not recent:
            return {"signal": 0.0, "count": 0, "net_shares": 0.0}

        buys = sum(t.shares for t in recent if t.transaction_type == "P")
        sells = sum(t.shares for t in recent if t.transaction_type == "S")
        net = buys - sells
        total = buys + sells

        return {
            "signal": net / total if total > 0 else 0.0,  # -1 to +1
            "count": len(recent),
            "net_shares": net,
            "buy_volume": buys,
            "sell_volume": sells,
        }

    def get_institutional_signal(self, symbol: str) -> dict[str, Any]:
        """Compute aggregated institutional signal for a symbol."""
        holdings = self.get_institutional_holdings(symbol)
        if not holdings:
            return {"signal": 0.0, "institutions": 0, "total_shares": 0.0}

        # Filter to most recent quarter
        latest_date = max(h.report_date for h in holdings)
        current = [h for h in holdings if h.report_date == latest_date]

        if not current:
            return {"signal": 0.0, "institutions": 0, "total_shares": 0.0}

        total_shares = sum(h.shares for h in current)
        # Compare to previous quarter
        prev_date = min(h.report_date for h in holdings)
        previous = [h for h in holdings if h.report_date == prev_date]
        prev_shares = sum(h.shares for h in previous) if previous else total_shares

        change_pct = (total_shares - prev_shares) / prev_shares if prev_shares > 0 else 0.0

        return {
            "signal": max(-1.0, min(1.0, change_pct * 2)),  # clamp
            "institutions": len(current),
            "total_shares": total_shares,
            "change_pct": change_pct,
        }

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("EdgarService started for %s", self._symbols)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("EdgarService stopped")

    async def _run_loop(self) -> None:
        while self._running:
            try:
                await self._update_all()
            except Exception as exc:
                logger.warning("Edgar update failed: %s", exc)
            await asyncio.sleep(self._update_interval)

    async def _update_all(self) -> None:
        """Fetch insider transactions and 13F holdings for all mapped CIKs."""
        logger.info("Starting SEC EDGAR update for %s", self._symbols)

        # Get all unique CIKs
        all_ciks = set()
        for ciks in self._symbol_cik_map.values():
            all_ciks.update(ciks)

        # Fetch insider transactions (Form 4)
        await self._fetch_insider_transactions(all_ciks)

        # Fetch 13F holdings
        await self._fetch_institutional_holdings(all_ciks)

        self._last_update = datetime.now(UTC)
        logger.info("SEC EDGAR update complete")

    async def _fetch_insider_transactions(self, ciks: set[str]) -> None:
        """Fetch Form 4 insider transactions for all CIKs."""
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=self._lookback_days)

        for cik in ciks:
            try:
                # Run in thread pool since edgartools is sync (to_thread
                # accepts only positional args, so kwargs go via partial).
                filings = await asyncio.to_thread(
                    functools.partial(
                        get_insider_transaction_filings,
                        cik,
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
                if not filings:
                    continue

                for filing in filings:
                    for txn in filing.transactions:
                        symbol = self._cik_to_symbol(cik)
                        if symbol:
                            insider = InsiderTransaction(
                                symbol=symbol,
                                insider_name=filing.insider_name,
                                insider_title=filing.insider_title,
                                transaction_date=txn.transaction_date,
                                transaction_type=txn.transaction_code,
                                shares=txn.shares,
                                price_per_share=txn.price_per_share,
                                value=txn.value,
                                is_derivative=txn.is_derivative,
                                filing_date=filing.filing_date,
                            )
                            self._insider_cache[symbol].append(insider)

            except Exception as exc:
                logger.warning("Failed to fetch insider transactions for CIK %s: %s", cik, exc)

        # Sort and deduplicate
        for symbol in self._insider_cache:
            self._insider_cache[symbol].sort(key=lambda t: t.transaction_date, reverse=True)
            # Keep last 90 days
            cutoff = datetime.now(UTC) - timedelta(days=self._lookback_days)
            self._insider_cache[symbol] = [
                t for t in self._insider_cache[symbol] if t.transaction_date >= cutoff
            ]

    async def _fetch_institutional_holdings(self, ciks: set[str]) -> None:
        """Fetch 13F institutional holdings for all CIKs."""
        end_date = datetime.now(UTC)
        start_date = end_date - timedelta(days=self._lookback_days * 2)  # 2 quarters

        for cik in ciks:
            try:
                # Get 13F filings
                filings = await asyncio.to_thread(
                    functools.partial(
                        get_filings,
                        cik,
                        form="13F",
                        start_date=start_date,
                        end_date=end_date,
                    )
                )
                if not filings:
                    continue

                for filing in filings:
                    try:
                        thirteen_f = filing.obj()
                        if not isinstance(thirteen_f, list):
                            continue

                        for holding in thirteen_f:
                            if not hasattr(holding, "name_of_issuer"):
                                continue

                            symbol = self._issuer_to_symbol(holding.name_of_issuer)
                            if not symbol:
                                continue

                            inst = InstitutionalHolding(
                                symbol=symbol,
                                institution_name=holding.name_of_issuer,
                                cik=cik,
                                shares=getattr(holding, "ssh_prnamt", 0),
                                value_usd=getattr(holding, "value", 0),
                                report_date=filing.period_of_report,
                                filing_date=filing.filing_date,
                            )
                            self._institutional_cache[symbol].append(inst)

                    except Exception as exc:
                        logger.warning("Failed to parse 13F for CIK %s: %s", cik, exc)

            except Exception as exc:
                logger.warning("Failed to fetch 13F for CIK %s: %s", cik, exc)

        # Keep last 2 quarters
        for symbol in self._institutional_cache:
            self._institutional_cache[symbol].sort(key=lambda h: h.report_date, reverse=True)
            # Keep last 2 distinct report dates
            dates = sorted(
                set(h.report_date for h in self._institutional_cache[symbol]), reverse=True
            )
            if len(dates) > 2:
                cutoff = dates[1]
                self._institutional_cache[symbol] = [
                    h for h in self._institutional_cache[symbol] if h.report_date >= cutoff
                ]

    def _cik_to_symbol(self, cik: str) -> str | None:
        """Map CIK to our tracked symbols."""
        for symbol, ciks in self._symbol_cik_map.items():
            if cik in ciks:
                return symbol
        return None

    def _issuer_to_symbol(self, issuer_name: str) -> str | None:
        """Map issuer name to our tracked symbols."""
        name_lower = issuer_name.lower()
        if "microstrategy" in name_lower or "mstr" in name_lower:
            return "BTC"
        if "coinbase" in name_lower or "coin" in name_lower:
            return "ETH"  # proxy
        if "riot" in name_lower or "marathon" in name_lower:
            return "BTC"
        return None


# Demo/test function
async def _demo() -> None:
    service = EdgarService(symbols=("BTC", "ETH"), update_interval_hours=24, lookback_days=30)
    await service.start()
    await asyncio.sleep(2)  # Let it fetch
    print("BTC insider signal:", service.get_insider_signal("BTC"))
    print("BTC institutional signal:", service.get_institutional_signal("BTC"))
    await service.stop()


if __name__ == "__main__":
    asyncio.run(_demo())
