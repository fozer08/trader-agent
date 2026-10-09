from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from trader_agent.engine.calendar import ExchangeSchedule, TradingCalendar
from trader_agent.engine.marketdata.base import MarketDataProvider
from trader_agent.engine.marketdata.cache import MarketBarCache
from trader_agent.engine.marketdata.service import MarketService
from trader_agent.engine.marketdata.types import OHLCV, BarData, SessionSnapshot, TimeFrame

TZ = ZoneInfo("Europe/Istanbul")
CALENDAR = TradingCalendar(ExchangeSchedule(time(10), time(18), TZ), {}, frozenset({2026}))


class _PreparedProvider(MarketDataProvider):
    """Kaynak çözünürlüğünü açıklamadan hazır bar ve fiyat özeti döndürür."""

    source = "prepared"
    delay_minutes = 0
    calendar = CALENDAR

    def __init__(self) -> None:
        self.completed_daily_dt = datetime(2026, 10, 6, 10, 0, tzinfo=TZ)
        self.daily_calls: list[tuple[str, int]] = []
        self.intraday_calls: list[tuple[str, TimeFrame]] = []
        self.today_calls: list[str] = []
        self.daily_error: RuntimeError | None = None
        self.intraday_bars = BarData("THYAO", TimeFrame.M15, [OHLCV(
            period_start=datetime(2026, 10, 7, 10, 0, tzinfo=TZ),
            open=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=2500.0,
        )])
        self.snapshot: SessionSnapshot | None = SessionSnapshot(
            symbol="THYAO",
            ohlcv=OHLCV(
                period_start=datetime(2026, 10, 7, 10, 0, tzinfo=TZ),
                open=100.0,
                high=115.0,
                low=95.0,
                close=110.0,
                volume=10000.0,
            ),
            as_of=datetime(2026, 10, 7, 11, 0, tzinfo=TZ),
        )

    async def get_daily(self, symbol: str, period: int) -> BarData:
        self.daily_calls.append((symbol, period))
        # Eşzamanlı servis çağrılarının kilitte beklemesine fırsat verir.
        await asyncio.sleep(0)
        if self.daily_error is not None:
            raise self.daily_error
        bars = [
            OHLCV(
                period_start=self.completed_daily_dt - timedelta(days=i),
                open=100.0,
                high=110.0,
                low=95.0,
                close=105.0,
                volume=2500.0,
            )
            for i in reversed(range(period))
        ]
        return BarData(symbol=symbol, timeframe=TimeFrame.D1, bars=bars)

    async def get_intraday(self, symbol: str, tf: TimeFrame) -> BarData:
        self.intraday_calls.append((symbol, tf))
        return self.intraday_bars

    async def get_today(self, symbol: str) -> SessionSnapshot | None:
        self.today_calls.append(symbol)
        return self.snapshot

@pytest.fixture
def market():
    provider = _PreparedProvider()
    with patch("trader_agent.engine.marketdata.service.datetime", wraps=datetime) as clock:
        clock.now.side_effect = lambda tz: provider.completed_daily_dt.replace(hour=18)
        yield MarketService(provider, store=MarketBarCache()), provider


@pytest.mark.asyncio
async def test_get_daily_cache_hit_avoids_provider_call(market):
    service, provider = market
    first = await service.get_daily(" thyao ", period=5)
    second = await service.get_daily("THYAO", period=3)

    assert len(first.bars) == 5
    assert second.bars == first.bars[-3:]
    assert first.symbol == second.symbol == "THYAO"
    assert first.timeframe is second.timeframe is TimeFrame.D1
    assert provider.daily_calls == [("THYAO", 5)]


@pytest.mark.asyncio
async def test_get_daily_concurrent_calls_dedupe_fetch(market):
    service, provider = market
    results = await asyncio.gather(
        *[service.get_daily("THYAO", period=3) for _ in range(5)]
    )

    assert all(len(bars.bars) == 3 for bars in results)
    assert all(bars == results[0] for bars in results)
    assert provider.daily_calls == [("THYAO", 3)]


async def test_daily_request_above_cache_capacity_keeps_full_result(market):
    service, provider = market
    first = await service.get_daily("THYAO", 600)
    second = await service.get_daily("THYAO", 600)
    assert len(first.bars) == len(second.bars) == 600
    assert first == second


async def test_short_history_above_cache_capacity_is_not_truncated(market):
    service, provider = market
    service._store = MarketBarCache(max_bars=2)
    original = provider.get_daily

    async def short_history(symbol, period):
        return await original(symbol, min(period, 3))

    provider.get_daily = short_history
    first = await service.get_daily("THYAO", 10)
    second = await service.get_daily("THYAO", 10)
    assert len(first.bars) == len(second.bars) == 3
    assert first == second


@pytest.mark.asyncio
async def test_get_daily_cross_symbol_isolation(market):
    service, provider = market
    thyao, garan = await asyncio.gather(
        service.get_daily("THYAO", period=1),
        service.get_daily("GARAN", period=1),
    )

    assert thyao.symbol == "THYAO"
    assert garan.symbol == "GARAN"
    assert sorted(provider.daily_calls) == [("GARAN", 1), ("THYAO", 1)]


@pytest.mark.asyncio
async def test_get_daily_refreshes_when_new_session_completes(market):
    service, provider = market
    previous = await service.get_daily("THYAO", period=3)
    provider.completed_daily_dt += timedelta(days=1)
    refreshed = await service.get_daily("THYAO", period=3)

    assert refreshed.bars[-1].period_start == provider.completed_daily_dt
    assert refreshed.bars[-1].period_start > previous.bars[-1].period_start
    assert provider.daily_calls == [("THYAO", 3), ("THYAO", 3)]


@pytest.mark.asyncio
async def test_get_daily_failed_fetch_can_be_retried(market):
    service, provider = market
    provider.daily_error = RuntimeError("fetch failed")
    with pytest.raises(RuntimeError, match="fetch failed"):
        await service.get_daily("THYAO", period=3)

    provider.daily_error = None
    bars = await service.get_daily("THYAO", period=3)
    assert len(bars.bars) == 3
    assert provider.daily_calls == [("THYAO", 3), ("THYAO", 3)]


@pytest.mark.asyncio
async def test_get_intraday_returns_provider_bars_without_processing(market):
    service, provider = market
    bars = await service.get_intraday(" thyao ", TimeFrame.M15)

    assert bars is provider.intraday_bars
    assert provider.intraday_calls == [("THYAO", TimeFrame.M15)]


@pytest.mark.asyncio
async def test_get_today_returns_provider_snapshot_without_processing(market):
    service, provider = market
    snapshot = await service.get_today(" thyao ")

    assert snapshot is provider.snapshot
    assert provider.today_calls == ["THYAO"]


@pytest.mark.asyncio
async def test_get_today_preserves_missing_snapshot(market):
    service, provider = market
    provider.snapshot = None
    assert await service.get_today("THYAO") is None


@pytest.mark.asyncio
async def test_get_intraday_raises_on_d1(market):
    service, provider = market
    with pytest.raises(ValueError, match="get_daily"):
        await service.get_intraday("THYAO", TimeFrame.D1)
    assert provider.intraday_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("period", [0, -1])
async def test_get_daily_raises_on_nonpositive_period(market, period):
    service, provider = market
    with pytest.raises(ValueError, match="positive"):
        await service.get_daily("THYAO", period=period)
    assert provider.daily_calls == []


@pytest.mark.asyncio
async def test_empty_symbol_does_not_reach_provider(market):
    service, provider = market
    assert await service.get_daily("   ", period=3) == BarData("", TimeFrame.D1, [])
    assert await service.get_intraday("   ", TimeFrame.M15) == BarData("", TimeFrame.M15, [])
    assert await service.get_today("   ") is None
    assert provider.daily_calls == []
    assert provider.intraday_calls == []
    assert provider.today_calls == []
