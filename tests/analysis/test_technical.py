from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trader_agent.analysis.technical import PROFILES, compute
from trader_agent.market.types import Bar, TimeFrame


def _bar(i: int, close: float, tf: TimeFrame = TimeFrame.D1, volume: float | None = 1000.0) -> Bar:
    return Bar(
        symbol="TEST",
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
        timeframe=tf,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=volume,
        is_closed=True,
    )


def _bars(n: int, tf: TimeFrame = TimeFrame.D1, start: float = 100.0) -> list[Bar]:
    return [_bar(i, start + i * 0.5, tf) for i in range(n)]


# ---- Validasyon ---------------------------------------------------------------

def test_compute_raises_on_empty():
    with pytest.raises(ValueError, match="empty"):
        compute([])


def test_compute_raises_on_mixed_symbols():
    bars = _bars(30)
    mixed = bars + [Bar(
        symbol="OTHER",
        datetime=datetime(2026, 2, 1, tzinfo=timezone.utc),
        timeframe=TimeFrame.D1,
        open=100, high=101, low=99, close=100,
        volume=1000, is_closed=True,
    )]
    with pytest.raises(ValueError, match="same symbol"):
        compute(mixed)


def test_compute_raises_on_unknown_timeframe():
    bars = _bars(30, tf=TimeFrame.H1)
    with pytest.raises(ValueError, match="No profile"):
        compute(bars)


# ---- Metadata -----------------------------------------------------------------

def test_result_contains_correct_symbol_and_timeframe():
    result = compute(_bars(30))
    assert result.symbol == "TEST"
    assert result.timeframe is TimeFrame.D1
    assert result.profile is PROFILES[TimeFrame.D1]


# ---- D1 profili ---------------------------------------------------------------

def test_d1_rsi_uses_period_14():
    assert PROFILES[TimeFrame.D1].rsi_period == 14


def test_d1_macd_is_none_with_insufficient_bars():
    assert compute(_bars(20)).macd is None


def test_d1_macd_has_all_fields_with_sufficient_bars():
    result = compute(_bars(60))
    assert result.macd is not None
    assert isinstance(result.macd.macd, float)
    assert isinstance(result.macd.signal, float)
    assert isinstance(result.macd.histogram, float)


def test_d1_ema_alignment_bullish_when_fast_above_slow():
    result = compute(_bars(60))
    # Düzenli yükselen seri: ema_fast > ema_slow beklenir
    assert result.ema_alignment == "bullish"


def test_d1_ema_alignment_none_when_slow_unavailable():
    # Yeterli bar var ama M5 profilinde ema_slow=None
    from trader_agent.market.types import TimeFrame
    result = compute(_intraday_bars(20, TimeFrame.M5))
    assert result.ema_alignment is None


def test_d1_atr_is_none_with_insufficient_bars():
    assert compute(_bars(10)).atr is None


def test_d1_atr_is_float_with_sufficient_bars():
    assert isinstance(compute(_bars(20)).atr, float)



# ---- M15 profili --------------------------------------------------------------

def _intraday_bars(n: int, tf: TimeFrame) -> list[Bar]:
    return [
        Bar(
            symbol="TEST",
            datetime=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i * tf.minutes),
            timeframe=tf,
            open=100 + i * 0.1,
            high=101 + i * 0.1,
            low=99 + i * 0.1,
            close=100 + i * 0.1,
            volume=1000.0,
            is_closed=True,
        )
        for i in range(n)
    ]


def test_m15_uses_ema_8_13():
    p = PROFILES[TimeFrame.M15]
    assert p.ema_fast == 8
    assert p.ema_slow == 13


def test_m15_rsi_uses_period_9():
    assert PROFILES[TimeFrame.M15].rsi_period == 9


def test_m15_no_macd():
    assert not PROFILES[TimeFrame.M15].use_macd


def test_m15_ema_fast_computed():
    result = compute(_intraday_bars(20, TimeFrame.M15))
    assert isinstance(result.ema_fast, float)


# ---- M5 profili ---------------------------------------------------------------

def test_m5_no_ema_slow():
    assert PROFILES[TimeFrame.M5].ema_slow is None


def test_m5_ema_slow_is_none():
    result = compute(_intraday_bars(20, TimeFrame.M5))
    assert result.ema_slow is None


# ---- Bollinger Bands ----------------------------------------------------------

def test_d1_bollinger_profile_enabled():
    assert PROFILES[TimeFrame.D1].use_bollinger is True
    assert PROFILES[TimeFrame.D1].bb_period == 20


def test_m5_bollinger_profile_disabled():
    assert PROFILES[TimeFrame.M5].use_bollinger is False


def test_d1_bollinger_none_with_insufficient_bars():
    assert compute(_bars(15)).bb is None


def test_d1_bollinger_computed_with_sufficient_bars():
    result = compute(_bars(30))
    assert result.bb is not None


def test_d1_bollinger_has_all_fields():
    result = compute(_bars(30))
    bb = result.bb
    assert isinstance(bb.upper, float)
    assert isinstance(bb.middle, float)
    assert isinstance(bb.lower, float)
    assert isinstance(bb.width, float)
    assert isinstance(bb.pct_b, float)
    assert bb.upper > bb.middle > bb.lower


def test_d1_bollinger_pct_b_above_half_for_rising_series():
    # Düzenli yükselen seride son fiyat üst banda yakın olmalı
    result = compute(_bars(30))
    assert result.bb.pct_b > 0.5


def test_m15_bollinger_computed():
    result = compute(_intraday_bars(30, TimeFrame.M15))
    assert result.bb is not None


def test_m5_bollinger_is_none():
    result = compute(_intraday_bars(30, TimeFrame.M5))
    assert result.bb is None


# ---- RSI Divergence -----------------------------------------------------------

def test_d1_rsi_divergence_profile_enabled():
    assert PROFILES[TimeFrame.D1].use_rsi_divergence is True


def test_m15_rsi_divergence_profile_disabled():
    assert PROFILES[TimeFrame.M15].use_rsi_divergence is False


def test_d1_rsi_divergence_is_valid_value():
    result = compute(_bars(60))
    assert result.rsi_divergence in (None, "bullish", "bearish")


def test_m15_rsi_divergence_is_none():
    result = compute(_intraday_bars(30, TimeFrame.M15))
    assert result.rsi_divergence is None


def test_m5_rsi_divergence_is_none():
    result = compute(_intraday_bars(30, TimeFrame.M5))
    assert result.rsi_divergence is None


def test_d1_bearish_divergence_detected():
    """Fiyat yeni zirve yaparken RSI yapmamalı → bearish divergence.

    Her iki peak lookback (20) penceresinde: 30 setup + 20 pattern = 50 bar.
    """
    setup       = [_bar(i, 100.0 + i * 0.5) for i in range(30)]
    strong_rise = [_bar(30 + i, 115.0 + i * 8.0) for i in range(5)]   # güçlü +8/bar
    pullback    = [_bar(35 + i, 140.0 - i * 4.0) for i in range(5)]   # geri çekilme
    weak_rise   = [_bar(40 + i, 126.0 + i * 3.5) for i in range(8)]   # zayıf +3.5/bar, fiyat yeni zirve
    drop        = [_bar(48 + i, 148.0 - i * 2.0) for i in range(2)]   # peak 2'yi yerel zirve yap
    bars = setup + strong_rise + pullback + weak_rise + drop
    result = compute(bars)
    assert result.rsi_divergence == "bearish"


def test_d1_bullish_divergence_detected():
    """Fiyat yeni dip yaparken RSI yapmamalı → bullish divergence.

    Her iki dip lookback (20) penceresinde: 30 setup + 20 pattern = 50 bar.
    """
    setup       = [_bar(i, 200.0 - i * 0.5) for i in range(30)]
    strong_drop = [_bar(30 + i, 185.0 - i * 8.0) for i in range(5)]  # güçlü −8/bar
    bounce      = [_bar(35 + i, 158.0 + i * 4.0) for i in range(5)]  # toparlanma
    weak_drop   = [_bar(40 + i, 170.0 - i * 3.5) for i in range(8)]  # zayıf −3.5/bar, fiyat yeni dip
    rise        = [_bar(48 + i, 148.0 + i * 2.0) for i in range(2)]  # dip 2'yi yerel dip yap
    bars = setup + strong_drop + bounce + weak_drop + rise
    result = compute(bars)
    assert result.rsi_divergence == "bullish"
