from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from time import monotonic
from uuid import uuid4

from .analysis import compute_indicator, compute_levels, compute_pulse
from .marketdata.service import MarketService, QuoteRevision, quote_from_data
from .news.service import NewsService
from .repository import PortfolioRepository, WatchlistRepository
from .tools.analysis import _indicators_to_dict, _levels_to_dict
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
    ) -> None:
        self.market = market
        self.repository = portfolio
        self.portfolio_tools = PortfolioTools(portfolio, market)
        self.news = NewsTools(news) if news else None
        self.watchlist_repository = watchlist
        self._watchlist_lock = asyncio.Lock()
        self.instance_id = str(uuid4())
        self._quotes: dict[str, dict] = {}
        self._refresh_task: asyncio.Task | None = None
        self._refresh_at = 0.0
        self._refresh_completed_at: str | None = None
        self._semaphore = asyncio.Semaphore(3)
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
            async with self._semaphore:
                try:
                    self._quotes[symbol] = await self.quote(symbol)
                except Exception:
                    previous = self._quotes.get(symbol, entries[symbol])
                    self._quotes[symbol] = {
                        **previous,
                        "pending": False,
                        "error": "Piyasa verisi alınamadı.",
                    }

        await asyncio.gather(*(refresh(symbol) for symbol in entries))
        self._refresh_completed_at = datetime.now(timezone.utc).isoformat()

    async def quote(self, symbol: str) -> dict:
        entry = self.watchlist_repository.stock(symbol)
        symbol = entry["symbol"]
        daily_series, snapshot = await asyncio.gather(
            self.market.get_daily(symbol, 100),
            self._snapshot(symbol),
        )
        if len(daily_series.bars) < 2:
            raise ValueError("Analiz için yeterli günlük veri bulunamadı.")
        ind = compute_indicator(daily_series)
        levels = compute_levels(daily_series)
        last = daily_series.bars[-1]
        quote = quote_from_data(symbol, daily_series, snapshot, self.market.calendar)
        return {
            **entry,
            "pending": False,
            "price": quote.price,
            "change_pct": quote.change_pct,
            "as_of": quote.as_of.isoformat() if quote.as_of else None,
            "session_date": quote.session_date.isoformat() if quote.session_date else None,
            "price_kind": quote.kind,
            "daily": {
                **_indicators_to_dict(ind),
                "close": last.close,
                "ema_fast": ind.ema_fast,
                "ema_slow": ind.ema_slow,
                "relative_volume": levels.relative_volume,
            },
            "pulse": asdict(compute_pulse(snapshot, daily_series, ind.ema_fast)) if snapshot and self.market.live_session_open() else None,
        }

    async def _snapshot(self, symbol: str):
        try:
            return await self.market.get_today(symbol)
        except Exception:
            return None

    async def symbol_view(self, symbol: str) -> dict:
        symbol = self.require_symbol(symbol)
        quote, series, intraday = await asyncio.gather(
            self.quote(symbol),
            self.market.get_daily(symbol, 100),
            self._intraday_indicators(symbol),
        )
        return {**quote, "levels": _levels_to_dict(compute_levels(series)), "intraday": intraday}

    async def _intraday_indicators(self, symbol: str) -> dict:
        async def indicators(tf: TimeFrame):
            try:
                series = await self.market.get_intraday(symbol, tf)
                return (
                    _indicators_to_dict(compute_indicator(series))
                    if len(series.bars) >= 2 else None
                )
            except Exception:
                return None

        m5, m15 = await asyncio.gather(indicators(TimeFrame.M5), indicators(TimeFrame.M15))
        return {"m5": m5, "m15": m15}

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
            "chart_type": "candlestick" if tf is TimeFrame.D1 else "line",
            "bars": [
                {
                    "time": bar.period_start.isoformat(),
                    "open": bar.open,
                    "high": bar.high,
                    "low": bar.low,
                    "close": bar.close,
                    "volume": bar.volume,
                }
                for bar in series.bars
            ],
        }

    async def portfolio_view(self) -> dict:
        positions = (await self.portfolio_tools.list_portfolio())["positions"]
        missing = sum(p["current_price"] is None for p in positions)
        total_cost = sum(p["quantity"] * p["avg_cost"] for p in positions)
        market_value = sum(
            p["quantity"] * p["current_price"] for p in positions if p["current_price"] is not None
        )
        pnl = sum(p.get("unrealized_pnl", 0) for p in positions)
        return {
            "positions": positions,
            "summary": {
                "total_cost": total_cost,
                "market_value": None if missing else market_value,
                "unrealized_pnl": None if missing else pnl,
                "unrealized_pnl_pct": None if missing or not total_cost else pnl / total_cost * 100,
                "realized_pnl": sum(p["realized_pnl"] for p in positions),
                "missing_prices": missing,
            },
        }

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
