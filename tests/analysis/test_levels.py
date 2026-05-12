from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trader_agent.analysis.levels import compute_levels, compute_session_snapshot
from trader_agent.market.types import Bar, IntradaySnapshot, TimeFrame


def _bar(
    i: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = 1000.0,
) -> Bar:
    return Bar(
        symbol="TEST",
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
        timeframe=TimeFrame.D1,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _neutral(i: int, price: float = 100.0, volume: float | None = 1000.0) -> Bar:
    return _bar(i, price, price + 2, price - 2, price, volume)


def _snapshot(
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: float | None = 1000.0,
) -> IntradaySnapshot:
    return IntradaySnapshot(
        symbol="TEST",
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


# ---- Validasyon ---------------------------------------------------------------

def test_raises_with_fewer_than_2_bars():
    with pytest.raises(ValueError, match="2 bars"):
        compute_levels([_neutral(0)])


def test_raises_on_mixed_symbols():
    bars = [_neutral(0), _neutral(1)]
    bars[1] = Bar(
        symbol="OTHER",
        datetime=bars[1].datetime,
        timeframe=bars[1].timeframe,
        open=bars[1].open,
        high=bars[1].high,
        low=bars[1].low,
        close=bars[1].close,
        volume=bars[1].volume,
    )
    with pytest.raises(ValueError, match="same symbol"):
        compute_levels(bars)


def test_raises_on_mixed_timeframes():
    bars = [_neutral(0), _neutral(1)]
    bars[1] = Bar(
        symbol=bars[1].symbol,
        datetime=bars[1].datetime,
        timeframe=TimeFrame.M15,
        open=bars[1].open,
        high=bars[1].high,
        low=bars[1].low,
        close=bars[1].close,
        volume=bars[1].volume,
    )
    with pytest.raises(ValueError, match="same timeframe"):
        compute_levels(bars)


# ---- PDH / PDL / PDC ----------------------------------------------------------

def test_prev_levels_from_last_bar():
    bars = [_neutral(0), _bar(1, open_=100, high=115, low=95, close=110)]
    levels = compute_levels(bars)
    assert levels.prev_high == 115
    assert levels.prev_low == 95
    assert levels.prev_close == 110


# ---- Pivot noktaları -----------------------------------------------------------

def test_pivot_calculation():
    # H=115, L=95, C=110 → PP=(115+95+110)/3=106.67
    bars = [_neutral(0), _bar(1, 100, 115, 95, 110)]
    p = compute_levels(bars).pivot
    expected_pp = (115 + 95 + 110) / 3
    assert p.pp == pytest.approx(expected_pp)
    assert p.r1 == pytest.approx(2 * expected_pp - 95)
    assert p.r2 == pytest.approx(expected_pp + (115 - 95))
    assert p.s1 == pytest.approx(2 * expected_pp - 115)
    assert p.s2 == pytest.approx(expected_pp - (115 - 95))


# ---- Haftalık high / low -------------------------------------------------------

def test_weekly_range_uses_last_5_bars():
    bars = [_bar(i, 100, 100 + i * 2, 100 - i, 100) for i in range(10)]
    levels = compute_levels(bars)
    last5 = bars[-5:]
    assert levels.weekly_high == max(b.high for b in last5)
    assert levels.weekly_low == min(b.low for b in last5)


# ---- Mum formasyonları ---------------------------------------------------------

def test_doji_detected():
    prev = _neutral(0)
    # Gövde küçük, toplam aralık büyük
    doji = _bar(1, open_=100, high=110, low=90, close=100.5)
    levels = compute_levels([prev, doji])
    assert levels.candle is not None
    assert levels.candle.name == "doji"


def test_hammer_detected():
    prev = _neutral(0)
    # Küçük gövde üstte, uzun alt gölge
    hammer = _bar(1, open_=108, high=109, low=100, close=109)
    levels = compute_levels([prev, hammer])
    assert levels.candle is not None
    assert levels.candle.name == "hammer"
    assert levels.candle.bullish is True


def test_shooting_star_detected():
    prev = _neutral(0)
    # Gövde: 100→101.5 (body=1.5), üst gölge: 101.5→109.5 (=8>2*1.5), alt gölge: 0.5<1.5
    star = _bar(1, open_=100, high=109.5, low=99.5, close=101.5)
    levels = compute_levels([prev, star])
    assert levels.candle is not None
    assert levels.candle.name == "shooting_star"
    assert levels.candle.bullish is False


def test_bullish_engulfing_detected():
    # Önceki: düşüş mumu (open > close)
    prev = _bar(0, open_=105, high=106, low=99, close=100)
    # Şimdiki: yükseliş mumu, öncekini tamamen yutuyor
    curr = _bar(1, open_=98, high=112, low=97, close=111)
    levels = compute_levels([prev, curr])
    assert levels.candle is not None
    assert levels.candle.name == "bullish_engulfing"
    assert levels.candle.bullish is True


def test_engulfing_has_priority_over_hammer_shape():
    prev = _bar(0, open_=105, high=106, low=99, close=100)
    # Bu mum hem önceki gövdeyi yutuyor hem de uzun alt gölge taşıyor.
    curr = _bar(1, open_=98, high=112, low=70, close=111)
    levels = compute_levels([prev, curr])
    assert levels.candle is not None
    assert levels.candle.name == "bullish_engulfing"


def test_bearish_engulfing_detected():
    # Önceki: yükseliş mumu
    prev = _bar(0, open_=100, high=106, low=99, close=105)
    # Şimdiki: düşüş mumu, öncekini tamamen yutuyor
    curr = _bar(1, open_=107, high=108, low=98, close=99)
    levels = compute_levels([prev, curr])
    assert levels.candle is not None
    assert levels.candle.name == "bearish_engulfing"
    assert levels.candle.bullish is False


def test_no_pattern_for_ordinary_candle():
    prev = _neutral(0)
    curr = _bar(1, open_=100, high=103, low=99, close=102)
    assert compute_levels([prev, curr]).candle is None


# ---- Relative volume ----------------------------------------------------------

def test_relative_volume_above_one_on_high_volume():
    bars = [_neutral(i, volume=1000.0) for i in range(22)]
    bars[-1] = _bar(21, 100, 102, 98, 101, volume=3000.0)
    levels = compute_levels(bars)
    assert levels.relative_volume == pytest.approx(3.0)


def test_relative_volume_none_when_volume_missing():
    bars = [_neutral(i, volume=None) for i in range(5)]
    assert compute_levels(bars).relative_volume is None


# ---- compute_session_snapshot ---------------------------------------------------

def test_session_snapshot_change_pct():
    today = _snapshot(open_=100, high=106, low=99, close=104)
    ctx = compute_session_snapshot(today, prev_close=100.0)
    assert ctx.change_pct == pytest.approx(4.0)


def test_session_snapshot_gap_pct():
    # Dün kapanış 100, bugün açılış 103 → %3 gap up
    today = _snapshot(open_=103, high=106, low=99, close=104)
    ctx = compute_session_snapshot(today, prev_close=100.0)
    assert ctx.gap_pct == pytest.approx(3.0)


def test_session_snapshot_range_pct():
    today = _snapshot(open_=100, high=110, low=90, close=105)
    ctx = compute_session_snapshot(today, prev_close=100.0)
    assert ctx.range_pct == pytest.approx(20.0)


def test_session_snapshot_range_position_at_top():
    # close == high → tepe, %100
    today = _snapshot(open_=100, high=110, low=90, close=110)
    ctx = compute_session_snapshot(today, prev_close=100.0)
    assert ctx.range_position == pytest.approx(100.0)


def test_session_snapshot_range_position_at_bottom():
    # close == low → dip, %0
    today = _snapshot(open_=100, high=110, low=90, close=90)
    ctx = compute_session_snapshot(today, prev_close=100.0)
    assert ctx.range_position == pytest.approx(0.0)


def test_session_snapshot_range_position_middle():
    # close tam ortada
    today = _snapshot(open_=100, high=110, low=90, close=100)
    ctx = compute_session_snapshot(today, prev_close=100.0)
    assert ctx.range_position == pytest.approx(50.0)


def test_session_snapshot_fields():
    today = _snapshot(open_=100, high=106, low=99, close=104, volume=2000.0)
    ctx = compute_session_snapshot(today, prev_close=100.0)
    assert ctx.open == 100
    assert ctx.high == 106
    assert ctx.low == 99
    assert ctx.close == 104
    assert ctx.volume == 2000.0


def test_session_snapshot_raises_on_zero_prev_close():
    today = _snapshot(open_=100, high=106, low=99, close=104)
    with pytest.raises(ValueError):
        compute_session_snapshot(today, prev_close=0.0)
