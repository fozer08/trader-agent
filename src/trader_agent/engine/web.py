from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from .analysis import Horizon
from .positions import Source
from .recommendations import RecommendationService
from .repository.preferences import TradingPreferencesRepository
from .analysis.service import AnalysisService
from .marketdata.service import MarketService, QuoteRevision
from .news.service import NewsService
from .repository import PortfolioRepository, WatchlistRepository
from .tools.news import NewsTools
from .tools.portfolio import PortfolioTools
from .marketdata.types import TimeFrame


class WebService:
    """Structured views over the same services used by the conversational agent."""

    def __init__(
        self,
        market: MarketService,
        portfolio: PortfolioRepository,
        news: NewsService | None,
        watchlist: WatchlistRepository,
        analysis: AnalysisService,
        recommendations: RecommendationService,
        preferences: TradingPreferencesRepository | None = None,
    ) -> None:
        self.preferences = preferences
        self.analysis = analysis
        self.horizon: Horizon = "swing"
        self.market = market
        self.repository = portfolio
        self.portfolio_tools = PortfolioTools(portfolio, market)
        self.recommendations = recommendations
        self.news = NewsTools(news) if news else None
        self.watchlist_repository = watchlist
        self._watchlist_lock = asyncio.Lock()
        self.instance_id = str(uuid4())
        self._quotes: dict[str, dict] = {}
        self._refresh_task: asyncio.Task | None = None
        self._refresh_at = 0.0
        self._refresh_completed_at: str | None = None
        self._quote_revision: QuoteRevision | None = None

    @property
    def watchlist(self) -> dict[str, dict]:
        return {entry["symbol"]: entry for entry in self.watchlist_repository.list()}

    def stocklist_view(self) -> dict:
        return {"items": self.watchlist_repository.stocklist()}

    async def add_to_watchlist(self, symbol: str) -> dict:
        async with self._watchlist_lock:
            self.watchlist_repository.add(symbol)
            await self._reset_quotes()
            return await self.watchlist_view()

    async def remove_from_watchlist(self, symbol: str) -> dict:
        async with self._watchlist_lock:
            if not self.watchlist_repository.remove(symbol):
                raise KeyError(f"Hisse takip listesinde bulunamadı: {symbol}")
            await self._reset_quotes()
            return await self.watchlist_view()

    async def _reset_quotes(self) -> None:
        await self.aclose()
        self._refresh_task = None
        self._refresh_at = 0.0
        self._refresh_completed_at = None
        symbols = self.watchlist
        self._quotes = {symbol: quote for symbol, quote in self._quotes.items() if symbol in symbols}

    def metadata(self) -> dict:
        provider = self.market.provider
        calendar = self.market.calendar
        now = self.market.now()
        day = calendar.day(now.date())
        next_open = calendar.next_open(now)
        return {
            "instance_id": self.instance_id,
            "server_time": now.isoformat(),
            "timezone": str(calendar.timezone),
            "source": provider.source,
            "delay_minutes": provider.delay_minutes,
            "scheduled_open": day.is_open(now),
            "session_date": day.date.isoformat(),
            "day_kind": day.kind,
            "reason": day.reason,
            "calendar_years": sorted(calendar.covered_years),
            "session_start": day.session.start.strftime("%H:%M") if day.session else None,
            "session_end": day.session.end.strftime("%H:%M") if day.session else None,
            "daily_close": day.session.daily_end.strftime("%H:%M") if day.session else None,
            "next_open": next_open.isoformat() if next_open else None,
            "watchlist_count": len(self.watchlist),
        }

    def require_symbol(self, symbol: str) -> str:
        return self.watchlist_repository.stock(symbol)["symbol"]

    async def watchlist_view(self) -> dict:
        horizon = ((await asyncio.to_thread(self.preferences.get)).horizon if self.preferences else None) or "swing"
        if horizon != self.horizon:
            await self._reset_quotes()
            self._quotes.clear()
            self.horizon = horizon
        entries = self.watchlist
        revision = self.market.quote_revision()
        if revision != self._quote_revision:
            await self._reset_quotes()
            self._quotes.clear()
            self._quote_revision = revision
        # Polling never waits for the complete upstream scan.
        if entries and (self._refresh_task is None or self._refresh_task.done()) and (
            any(symbol not in self._quotes for symbol in entries)
            or monotonic() - self._refresh_at >= 300
        ):
            self._refresh_at = monotonic()
            self._refresh_task = asyncio.create_task(self._refresh_quotes())
        return {
            "items": [
                self._quotes.get(symbol, {**entry, "pending": True})
                for symbol, entry in entries.items()
            ],
            "refreshing": self._refresh_task is not None and not self._refresh_task.done(),
            "updated_at": self._refresh_completed_at,
        }

    async def _refresh_quotes(self) -> None:
        entries = self.watchlist

        async def refresh(symbol: str) -> None:
            try:
                self._quotes[symbol] = await self.quote(symbol, self.horizon)
            except Exception:
                previous = self._quotes.get(symbol, entries[symbol])
                self._quotes[symbol] = {
                    **previous,
                    "pending": False,
                    "error": "Piyasa verisi alınamadı.",
                }

        await asyncio.gather(*(refresh(symbol) for symbol in entries))
        self._refresh_completed_at = datetime.now(timezone.utc).isoformat()

    async def quote(self, symbol: str, horizon: Horizon = "swing") -> dict:
        entry = self.watchlist_repository.stock(symbol)
        return {**entry, **await self.analysis.analyze(entry["symbol"], horizon), "pending": False}

    async def symbol_view(self, symbol: str, horizon: Horizon = "swing") -> dict:
        return await self.quote(self.require_symbol(symbol), horizon)

    async def bars_view(self, symbol: str, timeframe: str) -> dict:
        symbol = self.require_symbol(symbol)
        tf = TimeFrame[timeframe]
        series = (
            await self.market.get_daily(symbol, 100)
            if tf is TimeFrame.D1
            else await self.market.get_intraday(symbol, tf)
        )
        return {
            "symbol": series.symbol,
            "timeframe": series.timeframe.name,
            "chart_type": "candlestick" if tf is TimeFrame.D1 and all(
                bar.ohlc_kind == "reported" for bar in series.bars
            ) else "line",
            "bars": [
                {
                    "time": bar.period_start.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                    "ohlc_kind": bar.ohlc_kind,
                }
                for bar in series.bars
            ],
        }

    async def portfolio_view(self, source: Source | None = None) -> dict:
        result = await self.portfolio_tools.list_portfolio(source)
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    async def news_view(self, symbol: str | None, days: int) -> dict:
        if symbol:
            symbol = self.require_symbol(symbol)
        if not self.news:
            return {"digests": []}
        if symbol:
            return (await self.news.get_symbol_news([symbol], days))[0]
        return await self.news.get_market_news(days)

    async def aclose(self) -> None:
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
