from __future__ import annotations

import asyncio
from dataclasses import asdict

from .daily import Horizon, analyze_daily, policy_for
from .session import analyze_session
from ..calendar import CalendarCoverageError
from ..marketdata.base import ProviderError
from ..marketdata.service import MarketService, quote_from_data
from ..marketdata.types import SessionSnapshot


class AnalysisService:
    """Tarama, detay ve web için aynı veri penceresini ve analiz akışını uygular.

    I/O bu servistedir; formüller daily/session/risk modüllerinde saftır.
    Ortak semaphore provider yükünü sınırlar. Kısmi seans hatası görünür kalır;
    günlük veri hatası çağırana iletilir. Kendi kaynak veya background task'ı yoktur.
    """

    def __init__(self, market: MarketService) -> None:
        self._market = market
        self._semaphore = asyncio.Semaphore(3)

    async def analyze(self, symbol: str, horizon: Horizon) -> dict:
        policy = policy_for(horizon)
        async with self._semaphore:
            # get_today also supplies the last delayed observation after the close.
            daily = await self._market.get_daily(symbol, policy.history_bars)
            snapshot, session_error = await self._snapshot(symbol)
        analysis = analyze_daily(daily, horizon)
        session = analyze_session(snapshot, daily) if snapshot else None
        quote = quote_from_data(symbol, daily, snapshot, self._market.calendar)
        now = self._market.now()
        expected = self._market.quote_revision()[4]
        daily_stale = analysis.as_of.date() < expected if expected is not None else None
        session_data = asdict(session) if session else None
        if session_data is not None and session is not None:
            session_data["as_of"] = session.as_of.isoformat()
        daily_data = asdict(analysis)
        daily_data["as_of"] = analysis.as_of.isoformat()
        return {
            "symbol": symbol,
            "horizon": horizon,
            "price": quote.price,
            "price_kind": quote.kind,
            "change_pct": quote.change_pct,
            "as_of": quote.as_of.isoformat() if quote.as_of else None,
            "session_date": quote.session_date.isoformat() if quote.session_date else None,
            "delay_minutes": self._market.provider.delay_minutes,
            "observed_at": now.isoformat(),
            "price_age_minutes": (now - quote.as_of).total_seconds() / 60 if quote.as_of else None,
            "session_open": self._market.live_session_open(),
            "daily_stale": daily_stale,
            "daily": daily_data,
            "session": session_data,
            "session_error": session_error,
            "session_age_minutes": (now - session.as_of).total_seconds() / 60 if session else None,
        }

    async def _snapshot(self, symbol: str) -> tuple[SessionSnapshot | None, str | None]:
        try:
            return await self._market.get_today(symbol), None
        except (ProviderError, CalendarCoverageError) as exc:
            return None, str(exc)
