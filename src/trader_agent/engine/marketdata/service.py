from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from dataclasses import dataclass
from time import monotonic

from ...utils.logging import get_logger
from .base import MarketDataProvider, ProviderError
from .cache import MarketBarCache
from ..calendar import CalendarCoverageError, TradingCalendar
from .helpers import delayed_now, expected_period_start
from .types import BarData, PriceQuote, SessionSnapshot, TimeFrame


QuoteRevision = tuple[date, str, bool | None, bool, date | None]
_log = get_logger(__name__)


@dataclass(frozen=True)
class _DailyFetch:
    requested: int
    expected: datetime
    retry_at: float
    received: int


def quote_from_data(
    symbol: str,
    daily: BarData,
    snapshot: SessionSnapshot | None,
    calendar: TradingCalendar,
) -> PriceQuote:
    """Tek fiyat politikası: tamamlanmış D1, daha yeni seans özeti, yoksa unavailable."""
    bars = daily.bars
    last = bars[-1] if bars else None
    if snapshot is not None:
        snapshot_date = snapshot.ohlcv.period_start.astimezone(calendar.timezone).date()
        if last is None or last.period_start.astimezone(calendar.timezone).date() < snapshot_date:
            return PriceQuote(
                symbol,
                snapshot.ohlcv.close,
                snapshot.as_of,
                "delayed",
                snapshot_date,
                last.close if last else None,
            )
    if last:
        on = last.period_start.astimezone(calendar.timezone).date()
        day = calendar.day(on, allow_uncovered=True)
        reference = next(
            (
                bar.close
                for bar in reversed(bars[:-1])
                if bar.period_start.astimezone(calendar.timezone).date() < on
            ),
            None,
        )
        return PriceQuote(
            symbol,
            last.close,
            day.session.daily_end if day.session else None,
            "close",
            on,
            reference,
        )
    return PriceQuote(symbol, None, None, "unavailable")


class MarketService:
    """Sağlayıcıya ortak erişim ve günlük bar önbelleği.

    Gün içi barlar ve fiyat özeti sağlayıcıdan hazır alınır. Günlük verinin
    tazelik kontrolü ve sembol başına sorgu kilidi bu katmanda yönetilir.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        store: MarketBarCache,
    ) -> None:
        self._provider = provider
        self._store = store
        self._daily_locks: dict[str, asyncio.Lock] = {}
        self._daily_fetches: dict[str, _DailyFetch] = {}
        self.calendar = provider.calendar

    def now(self) -> datetime:
        return datetime.now(self.calendar.timezone)

    def live_session_open(self) -> bool:
        return self.calendar.is_open(self.now()) is True

    def quote_revision(self) -> QuoteRevision:
        """Seans veya verinin tamamlanma durumu değişince fiyat görünümünü yeniler."""
        now = self.calendar.local_time(self.now())
        day = self.calendar.day(now.date())
        delay = self._provider.delay_minutes
        try:
            completed = self.calendar.last_completed_session(delayed_now(now, delay)).date
        except CalendarCoverageError:
            completed = None
        first_bar_ready = (
            day.session is not None
            and now >= day.session.start + timedelta(minutes=delay + 1)
        )
        return day.date, day.kind, day.is_open(now), first_bar_ready, completed

    async def get_quote(self, symbol: str) -> PriceQuote:
        symbol = symbol.strip().upper()
        daily, snapshot = await asyncio.gather(
            self.get_daily(symbol, 2),
            self.get_today(symbol),
            return_exceptions=True,
        )
        failure: ProviderError | CalendarCoverageError | None = None
        for source, result in (("daily", daily), ("intraday", snapshot)):
            if isinstance(result, BaseException):
                if not isinstance(result, (ProviderError, CalendarCoverageError)):
                    raise result
                _log.warning("Fiyat kaynağı başarısız [%s] %s: %s", symbol, source, result)
                if failure is None:
                    failure = result
        if isinstance(daily, BaseException):
            daily = self._store.last_n(symbol, TimeFrame.D1, 2)
        if isinstance(snapshot, BaseException):
            snapshot = None
        quote = quote_from_data(symbol, daily, snapshot, self.calendar)
        if quote.price is None and failure is not None:
            raise failure
        return quote

    # ---- Public API --------------------------------------------------------

    async def get_intraday(self, symbol: str, tf: TimeFrame) -> BarData:
        """Bugünün barlarını istenen periyotta sağlayıcıdan alır."""
        if tf is TimeFrame.D1:
            raise ValueError("Use get_daily() for daily bars.")

        symbol = symbol.strip().upper()
        if not symbol:
            return BarData(symbol=symbol, timeframe=tf, bars=[])
        return await self._provider.get_intraday(symbol, tf)

    async def get_today(self, symbol: str) -> SessionSnapshot | None:
        """Bugünün OHLCV özetini ve son veri zamanını sağlayıcıdan alır."""
        symbol = symbol.strip().upper()
        if not symbol:
            return None
        return await self._provider.get_today(symbol)

    async def get_daily(self, symbol: str, period: int) -> BarData:
        """Son `period` günlük barı sembol ve D1 periyot bilgisiyle döndürür.

        Cache hit (yeterli bar + tazelik) sağlanırsa API'ye gidilmez.
        """
        if period <= 0:
            raise ValueError("period must be positive.")

        symbol = symbol.strip().upper()
        if not symbol:
            return BarData(symbol=symbol, timeframe=TimeFrame.D1, bars=[])

        now = self.now()
        expected_start = expected_period_start(
            TimeFrame.D1,
            self.calendar,
            delayed_now(now, self._provider.delay_minutes),
            allow_uncovered=True,
        )
        assert expected_start is not None  # D1 için tamamlanmış seans vardır.
        if not self._daily_cache_satisfies(symbol, period, expected_start):
            async with self._lock_for(symbol):
                # Double-check (başka coroutine doldurmuş olabilir)
                if not self._daily_cache_satisfies(symbol, period, expected_start):
                    series = await self._provider.get_daily(symbol, period=period)
                    # Kaynağın yanlışlıkla tamamlanmamış/future D1 döndürmesini engeller.
                    series = BarData(
                        symbol,
                        TimeFrame.D1,
                        [bar for bar in series.bars if bar.period_start <= expected_start],
                    )
                    if series.bars:
                        self._store.set(series)
                    self._daily_fetches[symbol] = _DailyFetch(
                        period,
                        expected_start,
                        monotonic() + 60,
                        len(series.bars),
                    )
                    cached = self._store.last_n(symbol, TimeFrame.D1, period)
                    if len(series.bars) > len(cached.bars):
                        return series
        return self._store.last_n(symbol, TimeFrame.D1, period)

    # ---- Provider metadata ------------------------------------------------

    @property
    def provider(self) -> MarketDataProvider:
        """Veri kaynağı ve seans bilgilerini sağlayan provider."""
        return self._provider

    # ---- Cache orchestration (internal) -----------------------------------

    def _daily_cache_satisfies(
        self,
        symbol: str,
        period: int,
        expected_period_start: datetime,
    ) -> bool:
        cached = self._store.last_n(symbol, TimeFrame.D1, period).bars
        current = bool(cached and cached[-1].period_start >= expected_period_start)
        if current and len(cached) >= period:
            return True
        fetched = self._daily_fetches.get(symbol)
        if (
            fetched is None
            or fetched.expected != expected_period_start
            or fetched.requested < period
            or len(cached) < min(period, fetched.received)
        ):
            return False
        return (current and fetched.received < fetched.requested) or monotonic() < fetched.retry_at

    def _lock_for(self, symbol: str) -> asyncio.Lock:
        lock = self._daily_locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            self._daily_locks[symbol] = lock
        return lock
