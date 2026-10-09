from datetime import date, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from trader_agent.engine.calendar import CalendarCoverageError
from trader_agent.engine.marketdata.provider import IsYatirimProvider
from trader_agent.engine.marketdata.types import TimeFrame


async def test_half_day_provider_clamps_request_and_never_emits_partial_h1(bist_calendar):
    start = bist_calendar.day(date(2026, 10, 28)).session.start
    points = [[(start + timedelta(minutes=i)).timestamp() * 1000, 100 + i] for i in range(481)]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"data": points})

    async with IsYatirimProvider(
        bist_calendar, _transport=httpx.MockTransport(handler)
    ) as provider:
        with patch.object(provider, "_now", return_value=start.replace(hour=14)):
            hourly = await provider.get_intraday("THYAO", TimeFrame.H1)
            snapshot = await provider.get_today("THYAO")
        assert [bar.period_start.hour for bar in hourly.bars] == [10, 11]
        assert requests[0].url.params["to"] == "20261028123000"
        assert len(requests) == 1
        assert snapshot.ohlcv.period_start == start
        assert snapshot.as_of == start.replace(hour=12, minute=29)


async def test_full_holiday_makes_no_intraday_http_calls(bist_calendar):
    def handler(request):
        pytest.fail("Tatil gününde gün içi HTTP çağrısı yapılmamalı.")

    async with IsYatirimProvider(
        bist_calendar, _transport=httpx.MockTransport(handler)
    ) as provider:
        with patch.object(provider, "_now") as clock:
            clock.return_value = datetime(2026, 10, 29, 15, tzinfo=bist_calendar.timezone)
            assert (await provider.get_intraday("THYAO", TimeFrame.M5)).bars == []
            assert await provider.get_today("THYAO") is None
            assert provider._daily_fetch_range(200)[1].date() == date(2026, 10, 28)
            clock.return_value = datetime(2028, 1, 3, 15, tzinfo=bist_calendar.timezone)
            with pytest.raises(CalendarCoverageError):
                await provider.get_today("THYAO")


async def test_daily_fetch_widens_by_actual_count_and_does_not_require_historic_coverage(
    bist_calendar,
):
    ends = []
    starts = []
    now = datetime(2026, 10, 6, 19, tzinfo=bist_calendar.timezone)

    def handler(request):
        start = datetime.strptime(request.url.params["startdate"], "%d-%m-%Y").date()
        end = datetime.strptime(request.url.params["enddate"], "%d-%m-%Y").date()
        starts.append(start)
        ends.append(end)
        if len(starts) == 1:
            dates = [end - timedelta(days=i) for i in range(20)]
        else:
            dates = [end - timedelta(days=i) for i in range(1000)]
        # Include future/full-holiday rows to exercise local filtering.
        dates += [date(2026, 10, 7), date(2026, 5, 27)]
        return httpx.Response(
            200,
            json={
                "ok": True,
                "value": [
                    {"HG_TARIH": on.isoformat(), "HG_KAPANIS": 100} for on in dates if on >= start
                ],
            },
        )

    async with IsYatirimProvider(
        bist_calendar, _transport=httpx.MockTransport(handler)
    ) as provider:
        with patch.object(provider, "_now", return_value=now):
            series = await provider.get_daily("THYAO", 500)
        assert len(starts) == 2 and starts[1] < starts[0]
        assert starts[0].year == 2024
        assert ends == [date(2026, 10, 6)] * 2
        assert len(series.bars) == 500
        assert series.bars[-1].period_start.date() == date(2026, 10, 6)
        assert all(bar.period_start.date() != date(2026, 5, 27) for bar in series.bars)


async def test_new_listing_fetch_attempts_are_bounded(bist_calendar):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200,
            json={
                "ok": True,
                "value": [
                    {"HG_TARIH": "2026-10-05", "HG_KAPANIS": 100},
                ],
            },
        )

    async with IsYatirimProvider(
        bist_calendar, _transport=httpx.MockTransport(handler)
    ) as provider:
        with patch.object(
            provider, "_now", return_value=datetime(2026, 10, 6, 15, tzinfo=bist_calendar.timezone)
        ):
            series = await provider.get_daily("NEW", 200)
        assert len(calls) == 3 and len(series.bars) == 1
