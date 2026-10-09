from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from trader_agent.engine.calendar import ExchangeSchedule, TradingCalendar
from trader_agent.engine.marketdata.helpers import (
    aggregate_bars,
    delayed_now,
    expected_period_start,
)
from trader_agent.engine.marketdata.types import OHLCV, BarData, TimeFrame


TZ = ZoneInfo("Europe/Istanbul")
CALENDAR = TradingCalendar(ExchangeSchedule(time(10), time(18), TZ), {}, frozenset({2026}))


@pytest.mark.parametrize(
    "now,expected",
    [
        ("2026-10-08T09:00:00+03:00", "2026-10-07T10:00:00+03:00"),
        ("2026-10-08T18:14:59+03:00", "2026-10-07T10:00:00+03:00"),
        ("2026-10-08T18:15:00+03:00", "2026-10-08T10:00:00+03:00"),
        ("2026-10-08T18:20:00+03:00", "2026-10-08T10:00:00+03:00"),
        ("2026-10-10T18:20:00+03:00", "2026-10-09T10:00:00+03:00"),
        ("2026-10-11T11:00:00+03:00", "2026-10-09T10:00:00+03:00"),
        ("2026-10-12T09:00:00+03:00", "2026-10-09T10:00:00+03:00"),
    ],
)
def test_expected_period_start_d1_accounts_for_delay_and_weekends(now, expected):
    result = expected_period_start(
        TimeFrame.D1, CALENDAR, delayed_now(datetime.fromisoformat(now), 15),
    )

    assert result == datetime.fromisoformat(expected)
    assert result.tzinfo is CALENDAR.timezone


@pytest.mark.parametrize("delay_minutes,expected_day", [(0, 8), (15, 7)])
def test_expected_period_start_d1_uses_delayed_time(delay_minutes, expected_day):
    now = datetime(2026, 10, 8, 18, tzinfo=TZ)

    assert expected_period_start(
        TimeFrame.D1, CALENDAR, delayed_now(now, delay_minutes),
    ) == datetime(2026, 10, expected_day, 10, tzinfo=TZ)


@pytest.mark.parametrize("minute,expected_day", [(44, 7), (45, 8)])
def test_expected_period_start_d1_uses_configured_session(minute, expected_day):
    calendar = TradingCalendar(ExchangeSchedule(time(9, 30), time(16, 30), ZoneInfo("UTC")), {}, frozenset({2026}))
    now = datetime(2026, 10, 8, 16, minute, tzinfo=calendar.timezone)

    assert expected_period_start(
        TimeFrame.D1, calendar, delayed_now(now, 15),
    ) == datetime(2026, 10, expected_day, 9, 30, tzinfo=calendar.timezone)


@pytest.mark.parametrize("tf,expected_hour,expected_minute", [
    (TimeFrame.M1, 11, 6),
    (TimeFrame.M5, 11, 0),
    (TimeFrame.M15, 10, 45),
    (TimeFrame.M30, 10, 30),
    (TimeFrame.H1, 10, 0),
])
def test_expected_period_start_intraday_uses_last_completed_bar(
    tf, expected_hour, expected_minute,
):
    now = datetime(2026, 10, 8, 11, 22, tzinfo=TZ)

    assert expected_period_start(tf, CALENDAR, delayed_now(now, 15)) == datetime(
        2026, 10, 8, expected_hour, expected_minute, tzinfo=TZ,
    )


@pytest.mark.parametrize("tf", [
    TimeFrame.M1, TimeFrame.M5, TimeFrame.M15, TimeFrame.M30, TimeFrame.H1,
])
def test_expected_period_start_intraday_waits_for_first_bar_to_complete(tf):
    start = datetime(2026, 10, 8, 10, tzinfo=TZ)
    first_available = start + timedelta(minutes=15 + tf.minutes)

    assert expected_period_start(
        tf, CALENDAR, delayed_now(first_available - timedelta(seconds=1), 15),
    ) is None
    assert expected_period_start(tf, CALENDAR, delayed_now(first_available, 15)) == start


@pytest.mark.parametrize("tf,expected_minute", [
    (TimeFrame.M1, 59),
    (TimeFrame.M5, 55),
    (TimeFrame.M15, 45),
    (TimeFrame.M30, 30),
    (TimeFrame.H1, 0),
])
def test_expected_period_start_intraday_stops_at_session_close(tf, expected_minute):
    now = datetime(2026, 10, 8, 20, tzinfo=TZ)

    assert expected_period_start(tf, CALENDAR, delayed_now(now, 15)) == datetime(
        2026, 10, 8, 17, expected_minute, tzinfo=TZ,
    )


@pytest.mark.parametrize("now", [
    "2026-10-08T10:14:59+03:00",
    "2026-10-10T14:00:00+03:00",
    "2026-10-12T09:00:00+03:00",
])
def test_expected_period_start_intraday_has_no_bar_for_unstarted_session(now):
    assert expected_period_start(
        TimeFrame.M5, CALENDAR, delayed_now(datetime.fromisoformat(now), 15),
    ) is None


@pytest.mark.parametrize("hour,minute,expected_hour", [
    (11, 20, 9),
    (11, 45, 10),
    (17, 0, 15),
])
def test_expected_period_start_intraday_aligns_to_session_start(hour, minute, expected_hour):
    calendar = TradingCalendar(ExchangeSchedule(time(9, 30), time(16, 45), ZoneInfo("UTC")), {}, frozenset({2026}))
    now = datetime(2026, 10, 8, hour, minute, tzinfo=calendar.timezone)

    assert expected_period_start(TimeFrame.H1, calendar, delayed_now(now, 15)) == datetime(
        2026, 10, 8, expected_hour, 30, tzinfo=calendar.timezone,
    )


def test_expected_period_start_intraday_has_no_full_bar_in_short_session():
    calendar = TradingCalendar(ExchangeSchedule(time(9, 30), time(10), ZoneInfo("UTC")), {}, frozenset({2026}))
    now = datetime(2026, 10, 8, 11, tzinfo=calendar.timezone)

    assert expected_period_start(TimeFrame.H1, calendar, delayed_now(now, 15)) is None


@pytest.mark.parametrize("ref,forward,expected_day", [
    (date(2026, 10, 10), True, 12),
    (date(2026, 10, 11), False, 9),
    (date(2026, 10, 8), True, 8),
])
def test_nearest_session_shifts_in_requested_direction(ref, forward, expected_day):
    calendar = TradingCalendar(ExchangeSchedule(time(9, 30), time(17), TZ), {}, frozenset({2026}))

    nearest = calendar.next_session if forward else calendar.previous_session
    assert nearest(ref, inclusive=True).start == datetime(
        2026, 10, expected_day, 9, 30, tzinfo=TZ,
    )


@pytest.mark.parametrize("target_tf", [TimeFrame.M15, TimeFrame.H1])
def test_aggregate_empty_series_preserves_symbol_and_target_timeframe(target_tf):
    result = aggregate_bars(
        series=BarData("THYAO", TimeFrame.M15, []),
        target_tf=target_tf,
        closed_until=datetime.fromisoformat("2026-04-29T11:30:00+03:00"),
    )

    assert result == BarData("THYAO", target_tf, [])


def test_aggregate_same_timeframe_filters_unclosed_bars_and_preserves_metadata():
    closed = _bar("2026-04-29T10:00:00+03:00", 10.0)
    unclosed = _bar("2026-04-29T10:15:00+03:00", 12.0)
    result = aggregate_bars(
        series=BarData("THYAO", TimeFrame.M15, [unclosed, closed]),
        target_tf=TimeFrame.M15,
        closed_until=datetime.fromisoformat("2026-04-29T10:20:00+03:00"),
    )

    assert result == BarData("THYAO", TimeFrame.M15, [closed])


def test_aggregate_bars_uses_session_start_alignment_for_hourly_bars():
    bars = [
        _bar("2026-04-29T09:30:00+03:00", 10.0),
        _bar("2026-04-29T09:45:00+03:00", 12.0),
        _bar("2026-04-29T10:15:00+03:00", 11.0),
        _bar("2026-04-29T10:30:00+03:00", 13.0),
    ]

    aggregated = aggregate_bars(
        series=BarData("TEST", TimeFrame.M15, bars),
        target_tf=TimeFrame.H1,
        closed_until=datetime.fromisoformat("2026-04-29T11:30:00+03:00"),
        session_start=time(9, 30),
    )

    assert aggregated.symbol == "TEST"
    assert aggregated.timeframe is TimeFrame.H1
    assert [bar.period_start for bar in aggregated.bars] == [
        datetime.fromisoformat("2026-04-29T09:30:00+03:00"),
        datetime.fromisoformat("2026-04-29T10:30:00+03:00"),
    ]
    assert aggregated.bars[0].open == 10.0
    assert aggregated.bars[0].high == 12.0
    assert aggregated.bars[0].low == 10.0
    assert aggregated.bars[0].close == 11.0
    assert len(aggregated.bars) == 2


def test_reliable_bounds_uses_delayed_time_for_reliable_end():
    bounds = CALENDAR.reliable_bounds(
        datetime.fromisoformat("2026-04-29T14:25:00+03:00"),
    )

    assert bounds == (
        datetime.fromisoformat("2026-04-29T10:00:00+03:00"),
        datetime.fromisoformat("2026-04-29T14:25:00+03:00"),
    )


def test_reliable_bounds_returns_none_before_reliable_session_start():
    bounds = CALENDAR.reliable_bounds(
        datetime.fromisoformat("2026-04-29T09:55:00+03:00"),
    )

    assert bounds is None


def _bar(
    dt: str,
    price: float,
) -> OHLCV:
    return OHLCV(
        period_start=datetime.fromisoformat(dt).astimezone(TZ),
        open=price,
        high=price,
        low=price,
        close=price,
        volume=None,
    )
