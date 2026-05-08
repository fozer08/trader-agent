from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from trader_agent.market.cache import MarketIntradayCache
from trader_agent.market.types import Bar, TimeFrame


TZ = ZoneInfo("Europe/Istanbul")
SESSION_START = time(10, 0)


def _bar(dt: str, price: float, is_closed: bool = True) -> Bar:
    return Bar(
        symbol="TEST",
        datetime=datetime.fromisoformat(dt).astimezone(TZ),
        timeframe=TimeFrame.M1,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=None,
        is_closed=is_closed,
    )


@pytest.fixture
def cache() -> MarketIntradayCache:
    return MarketIntradayCache(session_start=SESSION_START)


def test_get_returns_empty_when_no_bars(cache):
    result = cache.get("TEST", TimeFrame.M1, start=datetime(2026, 4, 29, 10, 0, tzinfo=TZ))
    assert result == []


def test_merge_and_get_returns_bars(cache):
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:00:00+03:00", 100.0)])
    result = cache.get("TEST", TimeFrame.M1, start=datetime.fromisoformat("2026-04-29T10:00:00+03:00"))
    assert len(result) == 1
    assert result[0].close == 100.0


def test_merge_replaces_open_bar(cache):
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:00:00+03:00", 100.0, is_closed=False)])
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:00:00+03:00", 105.0, is_closed=False)])
    result = cache.get("TEST", TimeFrame.M1, start=datetime.fromisoformat("2026-04-29T10:00:00+03:00"))
    assert len(result) == 1
    assert result[0].close == 105.0


def test_merge_appends_new_bars(cache):
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:00:00+03:00", 100.0)])
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:01:00+03:00", 101.0)])
    result = cache.get("TEST", TimeFrame.M1, start=datetime.fromisoformat("2026-04-29T10:00:00+03:00"))
    assert len(result) == 2


def test_get_filters_by_start_and_end(cache):
    cache.merge("TEST", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+03:00", 100.0),
        _bar("2026-04-29T10:01:00+03:00", 101.0),
        _bar("2026-04-29T10:02:00+03:00", 102.0),
    ])
    start = end = datetime.fromisoformat("2026-04-29T10:01:00+03:00")
    result = cache.get("TEST", TimeFrame.M1, start=start, end=end)
    assert len(result) == 1
    assert result[0].close == 101.0


def test_missing_range_returns_full_session_when_empty(cache):
    end = datetime.fromisoformat("2026-04-29T14:00:00+03:00")
    result = cache.missing_range("TEST", TimeFrame.M1, end=end)
    assert result == (datetime.fromisoformat("2026-04-29T10:00:00+03:00"), end)


def test_missing_range_returns_none_when_up_to_date(cache):
    end = datetime.fromisoformat("2026-04-29T10:05:00+03:00")
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:05:00+03:00", 100.0, is_closed=True)])
    assert cache.missing_range("TEST", TimeFrame.M1, end=end) is None


def test_missing_range_starts_after_closed_bar(cache):
    end = datetime.fromisoformat("2026-04-29T10:10:00+03:00")
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:05:00+03:00", 100.0, is_closed=True)])
    result = cache.missing_range("TEST", TimeFrame.M1, end=end)
    assert result == (datetime.fromisoformat("2026-04-29T10:06:00+03:00"), end)


def test_missing_range_starts_from_open_bar(cache):
    end = datetime.fromisoformat("2026-04-29T10:10:00+03:00")
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:05:00+03:00", 100.0, is_closed=False)])
    result = cache.missing_range("TEST", TimeFrame.M1, end=end)
    assert result == (datetime.fromisoformat("2026-04-29T10:05:00+03:00"), end)


def test_prune_removes_previous_session(cache):
    cache.merge("TEST", TimeFrame.M1, [_bar("2026-04-29T10:00:00+03:00", 100.0)])
    end = datetime.fromisoformat("2026-04-30T14:00:00+03:00")
    cache.missing_range("TEST", TimeFrame.M1, end=end)
    assert cache.get("TEST", TimeFrame.M1, start=datetime.fromisoformat("2026-04-29T10:00:00+03:00")) == []
