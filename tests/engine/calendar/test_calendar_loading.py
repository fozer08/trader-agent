import json
from datetime import date, time
from zoneinfo import ZoneInfo

import pytest

from trader_agent.config.market import ExchangeConfig, MarketConfig
from trader_agent.engine.calendar.loader import load_calendar


def document(**changes):
    return {
        "exchange": "XIST",
        "covered_years": [2026],
        "days": [{"date": "2026-10-28", "type": "half_day", "name": "Arife", "close": "12:30"}],
        **changes,
    }


@pytest.fixture
def calendar_file(cfg_dir):
    path = cfg_dir / "data" / "calendar.json"
    path.parent.mkdir()
    path.write_text(json.dumps(document()))
    return path


def test_calendar_file_is_relative_to_data_dir_and_loaded_once(calendar_file):
    config = MarketConfig(calendar="calendar.json")
    assert config.calendar_path() == calendar_file
    calendar = load_calendar(config)
    assert calendar.day(date(2026, 10, 28)).session.end.time() == time(12, 30)
    calendar_file.write_text("broken")
    assert calendar.day(date(2026, 10, 28)).kind == "half_day"
    with pytest.raises(ValueError):
        load_calendar(config)


def test_referenced_missing_file_fails(cfg_dir):
    with pytest.raises(FileNotFoundError):
        load_calendar(MarketConfig(calendar="missing.json"))


def test_mismatching_exchange_rejected(calendar_file):
    calendar_file.write_text(json.dumps(document(exchange="XNYS")))
    with pytest.raises(ValueError, match="exchange değeri XIST olmalıdır"):
        load_calendar(MarketConfig())


@pytest.mark.parametrize(
    "changes",
    [
        {"covered_years": []},
        {"covered_years": [2026, 2026]},
        {"covered_years": [2025]},
        {"covered_years": [True]},
        {"covered_years": ["2026"]},
        {"days": document()["days"] * 2},
        {"unknown": 1},
    ],
)
def test_invalid_calendar_document_rejected(calendar_file, changes):
    calendar_file.write_text(json.dumps(document(**changes)))
    with pytest.raises(ValueError):
        load_calendar(MarketConfig())


@pytest.mark.parametrize(
    "changes",
    [
        {"date": "28-10-2026"},
        {"date": "2026-10-28T00:00:00"},
        {"date": 1793145600},
        {"type": "holiday"},
        {"close": "25:00"},
        {"name": ""},
    ],
)
def test_invalid_exception_fields_rejected(calendar_file, changes):
    day = {**document()["days"][0], **changes}
    calendar_file.write_text(json.dumps(document(days=[day])))
    with pytest.raises(ValueError):
        load_calendar(MarketConfig())


@pytest.mark.parametrize(
    "changes",
    [
        {"close": None},
        {"close": "10:00"},
        {"close": "18:00"},
        {"open": "09:00"},
        {"open": "12:40"},
        {"date": "2026-10-31"},
        {"daily_close": "12:20"},
        {"daily_close": "18:20"},
        {"type": "closed", "close": "12:30"},
    ],
)
def test_invalid_exception_schedule_rejected(calendar_file, changes):
    day = {**document()["days"][0], **changes}
    calendar_file.write_text(json.dumps(document(days=[day])))
    with pytest.raises(ValueError):
        load_calendar(MarketConfig())


def test_exchange_settings_are_converted_to_schedule(calendar_file):
    calendar_file.write_text(json.dumps(document(days=[])))
    exchange = ExchangeConfig(
        open="10:00:30", close="18:00:30", daily_close="18:10:30",
        weekdays=["sun", "mon", "tue", "wed", "thu"],
    )
    calendar = load_calendar(MarketConfig(exchange=exchange))
    assert calendar.schedule.start == time(10, 0, 30)
    assert calendar.schedule.end == time(18, 0, 30)
    assert calendar.schedule.daily_end == time(18, 10, 30)
    assert calendar.timezone == ZoneInfo("Europe/Istanbul")
    assert calendar.schedule.weekdays == (6, 0, 1, 2, 3)
    assert calendar.day(date(2026, 10, 11)).session.daily_end.time() == time(18, 10, 30)
    assert calendar.day(date(2026, 10, 9)).kind == "closed"


def test_half_day_defaults_are_resolved_when_loading(calendar_file):
    calendar = load_calendar(MarketConfig())
    session = calendar.day(date(2026, 10, 28)).session
    assert session.start.time() == calendar.schedule.start
    assert session.end.time() == time(12, 30)
    assert session.daily_end == session.end
