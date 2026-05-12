from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trader_agent.market.cache import MarketBarCache
from trader_agent.market.types import Bar, TimeFrame

UTC = timezone.utc


def _bar(dt: str, price: float = 100.0, tf: TimeFrame = TimeFrame.M1) -> Bar:
    return Bar(
        symbol="X",
        datetime=datetime.fromisoformat(dt),
        timeframe=tf,
        open=price, high=price, low=price, close=price,
        volume=None,
    )


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@pytest.fixture
def cache() -> MarketBarCache:
    return MarketBarCache(max_bars=10)


# ---- get -------------------------------------------------------------------

def test_get_returns_none_when_empty(cache):
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T11:00:00+00:00")) is None


def test_get_returns_none_when_end_not_cached(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-30T10:00:00+00:00")) is None


def test_get_returns_range(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T09:59:00+00:00"),
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
        _bar("2026-04-29T10:02:00+00:00"),
    ])
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00"))
    assert [b.datetime for b in result] == [_dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00")]


def test_get_spans_multiple_days(cache):
    cache.set("X", TimeFrame.D1, [
        _bar("2026-04-28T10:00:00+00:00", tf=TimeFrame.D1),
        _bar("2026-04-29T10:00:00+00:00", tf=TimeFrame.D1),
    ])
    result = cache.get("X", TimeFrame.D1, _dt("2026-04-28T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    assert len(result) == 2


def test_get_returns_none_when_start_not_cached(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:01:00+00:00")])
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00")) is None


def test_get_isolates_symbol_and_timeframe(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    assert cache.get("Y", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00")) is None
    assert cache.get("X", TimeFrame.M5, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00")) is None


# ---- set -------------------------------------------------------------------

def test_set_empty_is_noop(cache):
    cache.set("X", TimeFrame.M1, [])
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00")) is None


def test_set_deduplicates_same_timestamp(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 100.0)])
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 105.0)])
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    assert len(result) == 1
    assert result[0].close == 105.0


def test_set_tail_append(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ])
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:02:00+00:00")])
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:02:00+00:00"))
    assert len(result) == 3


def test_set_overlap_replaces_range(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:01:00+00:00", 50.0)])
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00", 99.0),
        _bar("2026-04-29T10:01:00+00:00", 99.0),
    ])
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00"))
    assert len(result) == 2
    assert all(b.close == 99.0 for b in result)


def test_set_trims_oldest_when_over_max(cache):
    cache.set("X", TimeFrame.D1, [_bar(f"2026-04-{d:02d}T10:00:00+00:00", tf=TimeFrame.D1) for d in range(1, 12)])
    assert cache.get("X", TimeFrame.D1, _dt("2026-04-01T10:00:00+00:00"), _dt("2026-04-11T10:00:00+00:00")) is None
    result = cache.get("X", TimeFrame.D1, _dt("2026-04-02T10:00:00+00:00"), _dt("2026-04-11T10:00:00+00:00"))
    assert len(result) == 10


def test_set_prepends_when_bars_before_inner(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:05:00+00:00"),
        _bar("2026-04-29T10:06:00+00:00"),
    ])
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ])
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:06:00+00:00"))
    assert [b.datetime for b in result] == [
        _dt("2026-04-29T10:00:00+00:00"),
        _dt("2026-04-29T10:01:00+00:00"),
        _dt("2026-04-29T10:05:00+00:00"),
        _dt("2026-04-29T10:06:00+00:00"),
    ]


def test_set_fills_gap(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
        _bar("2026-04-29T10:03:00+00:00"),
    ])
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:02:00+00:00")])
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:03:00+00:00"))
    assert [b.datetime for b in result] == [
        _dt("2026-04-29T10:00:00+00:00"),
        _dt("2026-04-29T10:01:00+00:00"),
        _dt("2026-04-29T10:02:00+00:00"),
        _dt("2026-04-29T10:03:00+00:00"),
    ]


def test_set_partial_overlap_extends_tail(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00", 50.0),
        _bar("2026-04-29T10:01:00+00:00", 50.0),
        _bar("2026-04-29T10:02:00+00:00", 50.0),
    ])
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:02:00+00:00", 99.0),
        _bar("2026-04-29T10:03:00+00:00", 99.0),
    ])
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:03:00+00:00"))
    assert [b.close for b in result] == [50.0, 50.0, 99.0, 99.0]


def test_set_isolates_symbol_and_timeframe(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 100.0)])
    cache.set("Y", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 200.0)])
    cache.set("X", TimeFrame.M5, [_bar("2026-04-29T10:00:00+00:00", 300.0, tf=TimeFrame.M5)])

    x_m1 = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    y_m1 = cache.get("Y", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    x_m5 = cache.get("X", TimeFrame.M5, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    assert x_m1[0].close == 100.0
    assert y_m1[0].close == 200.0
    assert x_m5[0].close == 300.0


# ---- last / last_n ---------------------------------------------------------

def test_last_returns_none_when_empty(cache):
    assert cache.last("X", TimeFrame.M1) is None


def test_last_returns_last_bar(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ])
    result = cache.last("X", TimeFrame.M1)
    assert result is not None
    assert result.datetime == _dt("2026-04-29T10:01:00+00:00")


def test_last_n_returns_last_n_bars(cache):
    cache.set("X", TimeFrame.M1, [_bar(f"2026-04-29T10:0{i}:00+00:00") for i in range(5)])
    result = cache.last_n("X", TimeFrame.M1, 3)
    assert [b.datetime for b in result] == [
        _dt("2026-04-29T10:02:00+00:00"),
        _dt("2026-04-29T10:03:00+00:00"),
        _dt("2026-04-29T10:04:00+00:00"),
    ]


def test_last_n_returns_all_when_n_exceeds_size(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ])
    result = cache.last_n("X", TimeFrame.M1, 10)
    assert len(result) == 2


def test_last_n_returns_empty_when_n_zero_or_negative(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    assert cache.last_n("X", TimeFrame.M1, 0) == []
    assert cache.last_n("X", TimeFrame.M1, -1) == []


def test_last_n_returns_empty_when_empty(cache):
    assert cache.last_n("X", TimeFrame.M1, 5) == []


# ---- clear / introspection -------------------------------------------------

def test_clear_removes_bucket(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    cache.set("Y", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    cache.clear("X", TimeFrame.M1)
    assert cache.last("X", TimeFrame.M1) is None
    assert cache.last("Y", TimeFrame.M1) is not None


def test_clear_all_empties_cache(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    cache.set("Y", TimeFrame.M5, [_bar("2026-04-29T10:00:00+00:00", tf=TimeFrame.M5)])
    cache.clear_all()
    assert len(cache) == 0


def test_keys_returns_active_buckets(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    cache.set("Y", TimeFrame.M5, [_bar("2026-04-29T10:00:00+00:00", tf=TimeFrame.M5)])
    assert set(cache.keys()) == {("X", TimeFrame.M1), ("Y", TimeFrame.M5)}


def test_size_returns_bucket_length(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ])
    assert cache.size("X", TimeFrame.M1) == 2
    assert cache.size("Y", TimeFrame.M1) == 0


def test_len_returns_bucket_count(cache):
    assert len(cache) == 0
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    cache.set("Y", TimeFrame.M5, [_bar("2026-04-29T10:00:00+00:00", tf=TimeFrame.M5)])
    assert len(cache) == 2


# ---- missing ---------------------------------------------------------------

def test_missing_returns_full_range_when_empty(cache):
    start, end = _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T18:00:00+00:00")
    assert cache.missing("X", TimeFrame.M1, start, end) == (start, end)


def test_missing_returns_none_when_end_in_cache(cache):
    cache.set("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ])
    assert cache.missing("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00")) is None


def test_missing_returns_tail_when_end_after_last(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    start = _dt("2026-04-29T10:00:00+00:00")
    end = _dt("2026-04-29T10:05:00+00:00")
    result = cache.missing("X", TimeFrame.M1, start, end)
    assert result == (_dt("2026-04-29T10:01:00+00:00"), end)


def test_missing_returns_full_range_when_first_after_start(cache):
    cache.set("X", TimeFrame.D1, [_bar("2026-04-30T10:00:00+00:00", tf=TimeFrame.D1)])
    start, end = _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-30T10:00:00+00:00")
    assert cache.missing("X", TimeFrame.D1, start, end) == (start, end)


def test_missing_daily_returns_tail(cache):
    cache.set("X", TimeFrame.D1, [_bar("2026-04-29T10:00:00+00:00", tf=TimeFrame.D1)])
    start = _dt("2026-04-29T10:00:00+00:00")
    end = _dt("2026-05-01T10:00:00+00:00")
    result = cache.missing("X", TimeFrame.D1, start, end)
    assert result == (_dt("2026-04-30T10:00:00+00:00"), end)


def test_missing_returns_full_range_when_last_before_start(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    start = _dt("2026-04-29T11:00:00+00:00")
    end = _dt("2026-04-29T12:00:00+00:00")
    assert cache.missing("X", TimeFrame.M1, start, end) == (start, end)


def test_missing_returns_none_when_single_bar_covers_point(cache):
    cache.set("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")])
    start = end = _dt("2026-04-29T10:00:00+00:00")
    assert cache.missing("X", TimeFrame.M1, start, end) is None
