import asyncio
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from trader_agent.engine.analysis import analyze_session
from trader_agent.engine.marketdata.cache import MarketBarCache
from trader_agent.engine.marketdata.base import ProviderResponseError
from trader_agent.engine.marketdata.service import MarketService, quote_from_data
from trader_agent.engine.marketdata.types import BarData, OHLCV, SessionSnapshot, TimeFrame


def bar(calendar, on, price=100, volume=1000):
    start = calendar.day(date.fromisoformat(on), allow_uncovered=True).session.start
    return OHLCV(start, price, price + 2, price - 2, price, volume)


def service(calendar, at, bars, snapshot=None):
    provider = SimpleNamespace(
        calendar=calendar,
        delay_minutes=15,
        source="test",
        get_daily=AsyncMock(
            side_effect=lambda symbol, period: BarData(symbol, TimeFrame.D1, bars[-period:])
        ),
        get_today=AsyncMock(return_value=snapshot),
    )
    market = MarketService(provider, MarketBarCache())
    market.now = lambda: datetime.fromisoformat(at).replace(tzinfo=calendar.timezone)
    return market, provider


async def test_short_current_history_is_cached_without_repeat_fetch(bist_calendar):
    bars = [bar(bist_calendar, "2026-10-05"), bar(bist_calendar, "2026-10-06")]
    market, provider = service(bist_calendar, "2026-10-06T19:00", bars)
    results = await asyncio.gather(*(market.get_daily("NEW", 100) for _ in range(5)))
    assert all(len(series.bars) == 2 for series in results)
    assert provider.get_daily.await_count == 1
    await market.get_daily("NEW", 100)
    assert provider.get_daily.await_count == 1
    await market.get_daily("NEW", 200)
    assert provider.get_daily.await_count == 2


async def test_not_published_daily_bar_uses_bounded_retry_and_recovers(bist_calendar):
    bars = [bar(bist_calendar, "2026-10-05")]
    market, provider = service(bist_calendar, "2026-10-06T18:25", bars)
    with patch("trader_agent.engine.marketdata.service.monotonic", return_value=0):
        await asyncio.gather(*(market.get_daily("THYAO", 2) for _ in range(5)))
    assert provider.get_daily.await_count == 1
    bars.append(bar(bist_calendar, "2026-10-06", 110))
    with patch("trader_agent.engine.marketdata.service.monotonic", return_value=59):
        assert (await market.get_daily("THYAO", 2)).bars[-1].close == 100
    with patch("trader_agent.engine.marketdata.service.monotonic", return_value=60):
        assert (await market.get_daily("THYAO", 2)).bars[-1].close == 110
    assert provider.get_daily.await_count == 2


async def test_empty_success_retries_but_failed_fetch_is_not_remembered(bist_calendar):
    market, provider = service(bist_calendar, "2026-10-06T19:00", [])
    provider.get_daily.side_effect = RuntimeError("failed")
    with pytest.raises(RuntimeError):
        await market.get_daily("THYAO", 2)
    provider.get_daily.side_effect = None
    provider.get_daily.return_value = BarData("THYAO", TimeFrame.D1, [])
    with patch("trader_agent.engine.marketdata.service.monotonic", return_value=0):
        await market.get_daily("THYAO", 2)
        await market.get_daily("THYAO", 2)
    assert provider.get_daily.await_count == 2
    with patch("trader_agent.engine.marketdata.service.monotonic", return_value=60):
        await market.get_daily("THYAO", 2)
    assert provider.get_daily.await_count == 3


async def test_full_holiday_does_not_make_daily_cache_stale(bist_calendar):
    bars = [bar(bist_calendar, "2026-10-27"), bar(bist_calendar, "2026-10-28")]
    market, provider = service(bist_calendar, "2026-10-28T13:00", bars)
    await market.get_daily("THYAO", 2)
    market.now = lambda: datetime(2026, 10, 29, 19, tzinfo=bist_calendar.timezone)
    await market.get_daily("THYAO", 2)
    assert provider.get_daily.await_count == 1


def test_same_day_snapshot_does_not_reference_today_daily(bist_calendar):
    previous = bar(bist_calendar, "2026-10-27", 100, 1000)
    today = bar(bist_calendar, "2026-10-28", 110, 10000)
    snapshot = SessionSnapshot("THYAO", today, today.period_start.replace(hour=12, minute=29))
    daily = BarData("THYAO", TimeFrame.D1, [previous, today])
    session = analyze_session(snapshot, daily)
    assert session.change_pct == pytest.approx(10)
    assert session.previous_close == 100
    quote = quote_from_data("THYAO", daily, snapshot, bist_calendar)
    assert quote.kind == "close" and quote.reference_close == 100
    assert quote.change_pct == pytest.approx(10)
    assert quote.as_of.time() == time(12, 40)
    assert today.period_start.time() == time(10)


async def test_quotes_keep_snapshot_after_close_until_daily_is_available(bist_calendar):
    daily = [bar(bist_calendar, "2026-10-27", 100)]
    today = bar(bist_calendar, "2026-10-28", 110)
    snapshot = SessionSnapshot("THYAO", today, today.period_start.replace(hour=12, minute=29))
    market, _ = service(bist_calendar, "2026-10-28T12:45", daily, snapshot)
    quote = await market.get_quote(" thyao ")
    assert not market.live_session_open()
    assert quote.kind == "delayed" and quote.price == 110 and quote.reference_close == 100
    assert quote.as_of == snapshot.as_of and quote.session_date == date(2026, 10, 28)


async def test_quote_fallbacks_preserve_available_source(bist_calendar, caplog):
    market, provider = service(
        bist_calendar, "2026-10-29T15:00", [bar(bist_calendar, "2026-10-28", 110)]
    )
    provider.get_today.side_effect = ProviderResponseError("snapshot failed")
    quote = await market.get_quote("THYAO")
    assert quote.price == 110 and quote.kind == "close"
    assert quote.as_of.time() == time(12, 40)
    assert "THYAO" in caplog.text and "intraday" in caplog.text
    assert "snapshot failed" in caplog.text
    provider.get_daily.side_effect = ProviderResponseError("daily failed")
    market._store = MarketBarCache()
    market._daily_fetches.clear()
    with pytest.raises(ProviderResponseError, match="daily failed"):
        await market.get_quote("THYAO")


async def test_quote_keeps_cached_close_if_next_daily_fetch_fails(bist_calendar):
    market, provider = service(
        bist_calendar, "2026-10-05T19:00", [bar(bist_calendar, "2026-10-05", 100)]
    )
    await market.get_daily("THYAO", 1)
    provider.get_daily.side_effect = ProviderResponseError("daily unavailable")
    market.now = lambda: datetime(2026, 10, 6, 19, tzinfo=bist_calendar.timezone)
    quote = await market.get_quote("THYAO")
    assert quote.kind == "close" and quote.price == 100
    assert quote.session_date == date(2026, 10, 5)


@pytest.mark.parametrize("source", ["get_daily", "get_today"])
async def test_quote_propagates_unexpected_errors(bist_calendar, source):
    market, provider = service(bist_calendar, "2026-10-06T19:00", [])
    error = RuntimeError("programming error")
    getattr(provider, source).side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await market.get_quote("THYAO")
    assert caught.value is error


async def test_quote_does_not_turn_two_provider_failures_into_missing_data(bist_calendar):
    market, provider = service(bist_calendar, "2026-10-06T19:00", [])
    error = ProviderResponseError("invalid daily data")
    provider.get_daily.side_effect = error
    provider.get_today.side_effect = ProviderResponseError("invalid intraday data")
    with pytest.raises(ProviderResponseError) as caught:
        await market.get_quote("THYAO")
    assert caught.value is error


@pytest.mark.parametrize(
    "first,second",
    [
        ("2026-10-06T09:59", "2026-10-06T10:00"),
        ("2026-10-06T10:15", "2026-10-06T10:16"),
        ("2026-10-06T17:59", "2026-10-06T18:00"),
        ("2026-10-06T18:24", "2026-10-06T18:25"),
        ("2026-10-06T23:59", "2026-10-07T00:00"),
        ("2026-10-28T12:29", "2026-10-28T12:30"),
        ("2026-10-28T12:54", "2026-10-28T12:55"),
    ],
)
def test_cache_revision_changes_at_session_boundaries(bist_calendar, first, second):
    market, _ = service(bist_calendar, first, [])
    before = market.quote_revision()
    market.now = lambda: datetime.fromisoformat(second).replace(tzinfo=bist_calendar.timezone)
    assert market.quote_revision() != before
