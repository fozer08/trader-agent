from dataclasses import FrozenInstanceError
from datetime import date, datetime, time, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from trader_agent.engine.calendar import (
    CalendarCoverageError, DaySchedule, ExchangeSchedule, TradingCalendar, TradingSession,
)
from trader_agent.engine.marketdata.helpers import delayed_now, expected_period_start
from trader_agent.engine.marketdata.types import TimeFrame

TZ = ZoneInfo("Europe/Istanbul")


def moment(value):
    return datetime.fromisoformat(value).replace(tzinfo=TZ)


def test_calendar_import_is_independent_of_config_and_external_libraries():
    source = Path(__file__).resolve().parents[3] / "src"
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import sys",
            "import trader_agent.engine.calendar",
            "assert not any(name.startswith('trader_agent.config') for name in sys.modules)",
            "assert not any(name.startswith('trader_agent.engine.marketdata') for name in sys.modules)",
            "assert 'pydantic' not in sys.modules",
            "assert 'httpx' not in sys.modules",
        ])],
        env={**os.environ, "PYTHONPATH": str(source)},
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "on,kind,reason",
    [
        ("2026-10-06", "normal", None),
        ("2026-10-10", "closed", "Çalışma günü değil."),
        ("2026-10-28", "half_day", "Cumhuriyet Bayramı arifesi"),
        ("2026-10-29", "closed", "Cumhuriyet Bayramı"),
        ("2027-01-04", "normal", None),
        ("2028-01-03", "unknown", "Takvim bu yılı kapsamıyor."),
    ],
)
def test_date_resolution(bist_calendar, on, kind, reason):
    day = bist_calendar.day(date.fromisoformat(on))
    assert day.kind == kind and day.reason == reason
    assert bool(day.session) == (kind in ("normal", "half_day"))


@pytest.mark.parametrize(
    "at,opened",
    [
        ("2026-10-28T09:59", False),
        ("2026-10-28T10:00", True),
        ("2026-10-28T12:29:59", True),
        ("2026-10-28T12:30", False),
        ("2026-10-29T11:00", False),
        ("2027-01-04T11:00", True),
        ("2028-01-03T11:00", None),
    ],
)
def test_open_boundaries(bist_calendar, at, opened):
    assert bist_calendar.is_open(moment(at)) is opened
    assert bist_calendar.is_open(moment(at).astimezone(timezone.utc)) is opened


def test_closed_chain_and_next_open(bist_calendar):
    assert bist_calendar.previous_session(date(2026, 5, 28)).date == date(2026, 5, 26)
    assert bist_calendar.next_session(date(2026, 5, 26)).date == date(2026, 6, 1)
    assert bist_calendar.next_open(moment("2026-05-27T11:00")) == moment("2026-06-01T10:00")
    assert bist_calendar.next_open(moment("2026-10-28T09:00")) == moment("2026-10-28T10:00")


def test_2027_holiday_chains_and_half_day_completion(bist_calendar):
    assert bist_calendar.next_open(moment("2027-01-01T11:00")) == moment("2027-01-04T10:00")
    assert bist_calendar.next_open(moment("2027-03-08T13:00")) == moment("2027-03-12T10:00")
    assert bist_calendar.previous_session(date(2027, 3, 11)).date == date(2027, 3, 8)
    assert expected_period_start(
        TimeFrame.D1, bist_calendar, delayed_now(moment("2027-03-08T12:55"), 15)
    ) == moment("2027-03-08T10:00")
    assert bist_calendar.day(date(2027, 5, 15)).session is None
    assert bist_calendar.previous_session(date(2027, 5, 19)).date == date(2027, 5, 14)
    assert bist_calendar.next_open(moment("2027-05-19T11:00")) == moment("2027-05-20T10:00")
    assert bist_calendar.next_open(moment("2027-10-28T13:00")) == moment("2027-11-01T10:00")


def test_actual_market_close_and_delayed_data_are_separate(bist_calendar):
    now = moment("2026-10-28T12:40")
    assert bist_calendar.is_open(now) is False
    assert bist_calendar.reliable_bounds(delayed_now(now, 15)) == (
        moment("2026-10-28T10:00"),
        moment("2026-10-28T12:25"),
    )
    assert bist_calendar.reliable_bounds(moment("2026-10-28T18:00"))[1] == moment(
        "2026-10-28T12:30"
    )
    assert expected_period_start(TimeFrame.H1, bist_calendar, moment("2026-10-28T13:00")) == moment(
        "2026-10-28T11:00"
    )
    assert bist_calendar.reliable_bounds(moment("2026-10-29T11:00")) is None


@pytest.mark.parametrize(
    "at,expected",
    [
        ("2026-10-28T12:54", "2026-10-27"),
        ("2026-10-28T12:55", "2026-10-28"),
        ("2026-10-29T18:30", "2026-10-28"),
        ("2026-10-06T18:24", "2026-10-05"),
        ("2026-10-06T18:25", "2026-10-06"),
    ],
)
def test_daily_completion_includes_closing_auction_and_delay(bist_calendar, at, expected):
    last = expected_period_start(TimeFrame.D1, bist_calendar, delayed_now(moment(at), 15))
    assert last.date().isoformat() == expected


def test_coverage_does_not_block_historical_ranges(bist_calendar):
    assert bist_calendar.day(date(2024, 10, 7)).kind == "unknown"
    assert bist_calendar.day(date(2024, 10, 7), allow_uncovered=True).session is not None
    assert bist_calendar.next_open(moment("2028-01-03T12:00")) is None
    with pytest.raises(CalendarCoverageError):
        bist_calendar.reliable_bounds(moment("2028-01-03T12:00"))
    with pytest.raises(CalendarCoverageError):
        bist_calendar.previous_session(date(2025, 1, 1))


def test_weekdays_are_configurable_and_half_day_can_override_open():
    schedule = ExchangeSchedule(time(10), time(18), TZ, (6, 0, 1, 2, 3))
    on = date(2026, 10, 11)
    session = TradingSession(
        moment("2026-10-11T11:00"), moment("2026-10-11T13:00"), moment("2026-10-11T13:00"),
    )
    calendar = TradingCalendar(
        schedule, {on: DaySchedule(on, "half_day", session, "Test")}, frozenset({2026}),
    )
    assert calendar.day(date(2026, 10, 9)).kind == "closed"
    assert calendar.day(date(2026, 10, 11)).session.start == moment("2026-10-11T11:00")
    assert calendar.next_session(date(2026, 10, 8)).date == date(2026, 10, 11)


def test_calendar_is_immutable_and_requires_aware_queries(bist_calendar):
    with pytest.raises(FrozenInstanceError):
        bist_calendar.schedule = bist_calendar.schedule
    with pytest.raises(TypeError):
        bist_calendar.exceptions[date(2026, 1, 1)] = None
    with pytest.raises(ValueError, match="timezone"):
        bist_calendar.is_open(datetime(2026, 10, 6, 11))


def test_calendar_snapshots_supplied_exceptions():
    on = date(2026, 10, 7)
    exceptions = {on: DaySchedule(on, "closed", None, "Test")}
    calendar = TradingCalendar(
        ExchangeSchedule(time(10), time(18), TZ), exceptions, frozenset({2026}),
    )
    exceptions.clear()
    assert calendar.day(on).kind == "closed"


def test_session_search_stops_at_missing_year():
    calendar = TradingCalendar(
        ExchangeSchedule(time(10), time(18), TZ), {}, frozenset({2025, 2027}),
    )
    with pytest.raises(CalendarCoverageError):
        calendar.next_session(date(2025, 12, 31))
    with pytest.raises(CalendarCoverageError):
        calendar.previous_session(date(2027, 1, 1))
    assert calendar.next_session(date(2025, 12, 31), allow_uncovered=True).date == date(2026, 1, 1)


def test_session_search_reports_date_limits_without_overflow():
    calendar = TradingCalendar(
        ExchangeSchedule(time(10), time(18), TZ), {}, frozenset({1, 9999}),
    )
    with pytest.raises(CalendarCoverageError):
        calendar.previous_session(date.min)
    with pytest.raises(CalendarCoverageError):
        calendar.next_session(date.max)


@pytest.mark.parametrize("tf", [TimeFrame.M1, TimeFrame.M5, TimeFrame.M15, TimeFrame.H1])
def test_completed_period_uses_exact_session_start(tf):
    calendar = TradingCalendar(
        ExchangeSchedule(time(9, 30, 30), time(16, 45, 30), TZ), {}, frozenset({2026}),
    )
    start = moment("2026-10-07T09:30:30")
    first_end = start + timedelta(minutes=tf.minutes)
    assert expected_period_start(tf, calendar, first_end - timedelta(microseconds=1)) is None
    assert expected_period_start(tf, calendar, first_end) == start
    assert expected_period_start(tf, calendar, first_end.astimezone(timezone.utc)) == start
    assert expected_period_start(tf, calendar, first_end + timedelta(minutes=tf.minutes)) == first_end
