from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trader_agent.engine.marketdata.cache import MarketBarCache
from trader_agent.engine.marketdata.types import OHLCV, BarData, TimeFrame

UTC = timezone.utc


def _bar(dt: str, price: float = 100.0) -> OHLCV:
    return OHLCV(
        period_start=datetime.fromisoformat(dt),
        open=price, high=price, low=price, close=price,
        volume=None,
    )


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@pytest.fixture
def cache() -> MarketBarCache:
    return MarketBarCache(max_bars=10)


# ---- get -------------------------------------------------------------------

def test_get_returns_empty_series_when_empty(cache):
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T11:00:00+00:00")).bars == []


def test_get_returns_available_bars_when_end_not_cached(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")]))
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-30T10:00:00+00:00")).bars == [_bar("2026-04-29T10:00:00+00:00")]


def test_get_returns_range(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T09:59:00+00:00"),
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
        _bar("2026-04-29T10:02:00+00:00"),
    ]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00"))
    assert [b.period_start for b in result.bars] == [_dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00")]


def test_get_spans_multiple_days(cache):
    cache.set(BarData("X", TimeFrame.D1, [
        _bar("2026-04-28T10:00:00+00:00"),
        _bar("2026-04-29T10:00:00+00:00"),
    ]))
    result = cache.get("X", TimeFrame.D1, _dt("2026-04-28T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    assert len(result.bars) == 2


def test_get_returns_available_bars_when_start_not_cached(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:01:00+00:00")]))
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00")).bars == [_bar("2026-04-29T10:01:00+00:00")]


def test_get_isolates_symbol_and_timeframe(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")]))
    assert cache.get("Y", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00")).bars == []
    assert cache.get("X", TimeFrame.M5, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00")).bars == []


# ---- set -------------------------------------------------------------------

def test_set_empty_is_noop(cache):
    cache.set(BarData("X", TimeFrame.M1, []))
    assert cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00")).bars == []


def test_set_deduplicates_same_timestamp(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 100.0)]))
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 105.0)]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    assert len(result.bars) == 1
    assert result.bars[0].close == 105.0


def test_set_tail_append(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ]))
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:02:00+00:00")]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:02:00+00:00"))
    assert len(result.bars) == 3


def test_set_overlap_replaces_range(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:01:00+00:00", 50.0)]))
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00", 99.0),
        _bar("2026-04-29T10:01:00+00:00", 99.0),
    ]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00"))
    assert len(result.bars) == 2
    assert all(b.close == 99.0 for b in result.bars)


def test_set_trims_oldest_when_over_max(cache):
    cache.set(BarData("X", TimeFrame.D1, [_bar(f"2026-04-{d:02d}T10:00:00+00:00") for d in range(1, 12)]))
    result = cache.get("X", TimeFrame.D1, _dt("2026-04-02T10:00:00+00:00"), _dt("2026-04-11T10:00:00+00:00"))
    assert [bar.period_start.day for bar in result.bars] == list(range(2, 12))


def test_set_prepends_when_bars_before_inner(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:05:00+00:00"),
        _bar("2026-04-29T10:06:00+00:00"),
    ]))
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:06:00+00:00"))
    assert [b.period_start for b in result.bars] == [
        _dt("2026-04-29T10:00:00+00:00"),
        _dt("2026-04-29T10:01:00+00:00"),
        _dt("2026-04-29T10:05:00+00:00"),
        _dt("2026-04-29T10:06:00+00:00"),
    ]


def test_set_fills_gap(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
        _bar("2026-04-29T10:03:00+00:00"),
    ]))
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:02:00+00:00")]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:03:00+00:00"))
    assert [b.period_start for b in result.bars] == [
        _dt("2026-04-29T10:00:00+00:00"),
        _dt("2026-04-29T10:01:00+00:00"),
        _dt("2026-04-29T10:02:00+00:00"),
        _dt("2026-04-29T10:03:00+00:00"),
    ]


def test_sparse_update_preserves_existing_bars_and_last_duplicate_wins(cache):
    middle = _bar("2026-04-29T10:01:00+00:00", 101)
    cache.set(BarData("X", TimeFrame.M1, [middle]))
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:02:00+00:00", 102),
        _bar("2026-04-29T10:00:00+00:00", 99),
        _bar("2026-04-29T10:00:00+00:00", 100),
    ]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:02:00+00:00"))
    assert [bar.close for bar in result.bars] == [100, 101, 102]


def test_set_partial_overlap_extends_tail(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00", 50.0),
        _bar("2026-04-29T10:01:00+00:00", 50.0),
        _bar("2026-04-29T10:02:00+00:00", 50.0),
    ]))
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:02:00+00:00", 99.0),
        _bar("2026-04-29T10:03:00+00:00", 99.0),
    ]))
    result = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:03:00+00:00"))
    assert [b.close for b in result.bars] == [50.0, 50.0, 99.0, 99.0]


def test_set_isolates_symbol_and_timeframe(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 100.0)]))
    cache.set(BarData("Y", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00", 200.0)]))
    cache.set(BarData("X", TimeFrame.M5, [_bar("2026-04-29T10:00:00+00:00", 300.0)]))

    x_m1 = cache.get("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    y_m1 = cache.get("Y", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    x_m5 = cache.get("X", TimeFrame.M5, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:00:00+00:00"))
    assert x_m1.bars[0].close == 100.0
    assert y_m1.bars[0].close == 200.0
    assert x_m5.bars[0].close == 300.0
    assert x_m1.symbol == x_m5.symbol == "X"
    assert y_m1.symbol == "Y"
    assert x_m1.timeframe is y_m1.timeframe is TimeFrame.M1
    assert x_m5.timeframe is TimeFrame.M5


# ---- last / last_n ---------------------------------------------------------

def test_last_returns_none_when_empty(cache):
    assert cache.last("X", TimeFrame.M1) is None


def test_last_returns_last_bar(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ]))
    result = cache.last("X", TimeFrame.M1)
    assert result is not None
    assert result.period_start == _dt("2026-04-29T10:01:00+00:00")


def test_last_n_returns_last_n_bars(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar(f"2026-04-29T10:0{i}:00+00:00") for i in range(5)]))
    result = cache.last_n("X", TimeFrame.M1, 3)
    assert [b.period_start for b in result.bars] == [
        _dt("2026-04-29T10:02:00+00:00"),
        _dt("2026-04-29T10:03:00+00:00"),
        _dt("2026-04-29T10:04:00+00:00"),
    ]


def test_last_n_returns_all_when_n_exceeds_size(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ]))
    result = cache.last_n("X", TimeFrame.M1, 10)
    assert len(result.bars) == 2


def test_last_n_returns_empty_when_n_zero_or_negative(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")]))
    assert cache.last_n("X", TimeFrame.M1, 0) == BarData("X", TimeFrame.M1, [])
    assert cache.last_n("X", TimeFrame.M1, -1) == BarData("X", TimeFrame.M1, [])


def test_last_n_returns_empty_when_empty(cache):
    assert cache.last_n("X", TimeFrame.M1, 5) == BarData("X", TimeFrame.M1, [])


# ---- missing ---------------------------------------------------------------

def test_missing_returns_full_range_when_empty(cache):
    start, end = _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T18:00:00+00:00")
    assert cache.missing_tail("X", TimeFrame.M1, start, end) == (start, end)


def test_missing_returns_none_when_end_in_cache(cache):
    cache.set(BarData("X", TimeFrame.M1, [
        _bar("2026-04-29T10:00:00+00:00"),
        _bar("2026-04-29T10:01:00+00:00"),
    ]))
    assert cache.missing_tail("X", TimeFrame.M1, _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-29T10:01:00+00:00")) is None


def test_missing_returns_tail_when_end_after_last(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")]))
    start = _dt("2026-04-29T10:00:00+00:00")
    end = _dt("2026-04-29T10:05:00+00:00")
    result = cache.missing_tail("X", TimeFrame.M1, start, end)
    assert result == (_dt("2026-04-29T10:01:00+00:00"), end)


def test_missing_tail_does_not_refetch_missing_leading_bars(cache):
    cache.set(BarData("X", TimeFrame.D1, [_bar("2026-04-30T10:00:00+00:00")]))
    start, end = _dt("2026-04-29T10:00:00+00:00"), _dt("2026-04-30T10:00:00+00:00")
    assert cache.missing_tail("X", TimeFrame.D1, start, end) is None


def test_missing_daily_returns_tail(cache):
    cache.set(BarData("X", TimeFrame.D1, [_bar("2026-04-29T10:00:00+00:00")]))
    start = _dt("2026-04-29T10:00:00+00:00")
    end = _dt("2026-05-01T10:00:00+00:00")
    result = cache.missing_tail("X", TimeFrame.D1, start, end)
    assert result == (_dt("2026-04-30T10:00:00+00:00"), end)


def test_missing_returns_full_range_when_last_before_start(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")]))
    start = _dt("2026-04-29T11:00:00+00:00")
    end = _dt("2026-04-29T12:00:00+00:00")
    assert cache.missing_tail("X", TimeFrame.M1, start, end) == (start, end)


def test_missing_returns_none_when_single_bar_covers_point(cache):
    cache.set(BarData("X", TimeFrame.M1, [_bar("2026-04-29T10:00:00+00:00")]))
    start = end = _dt("2026-04-29T10:00:00+00:00")
    assert cache.missing_tail("X", TimeFrame.M1, start, end) is None
