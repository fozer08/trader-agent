from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from trader_agent.engine.analysis import analyze_daily, policy_for
from trader_agent.engine.marketdata.types import BarData, OHLCV, TimeFrame


def series(count=300, close=100.0):
    return BarData("TEST", TimeFrame.D1, [
        OHLCV(datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
              close, close + 1, close - 1, close, 1000.0) for i in range(count)
    ])


@pytest.mark.parametrize("horizon,fast,slow,history", [
    ("intraday", 9, 21, 105), ("swing", 20, 50, 250), ("position", 20, 60, 300),
])
def test_horizon_windows_and_fixed_history(horizon, fast, slow, history):
    data = series(400)
    # Discarded history must not affect scan/detail results.
    data.bars[0] = replace(data.bars[0], close=10000)
    result = analyze_daily(data, horizon)
    assert result == analyze_daily(BarData("TEST", TimeFrame.D1, data.bars[-history:]), horizon)
    assert (result.ema_fast_period, result.ema_slow_period) == (fast, slow)
    assert result.history_required == result.bars_available == history
    assert result.ema_fast == result.ema_slow == 100
    assert result.price_vs_ema == "equal" and result.ema_alignment == "flat"
    assert result.rsi is None  # Unmoving prices are not momentum strength.
    assert result.atr == 2 and result.atr_pct == 2


def test_single_price_jump_matches_analytical_ema_and_wilder_atr():
    data = series()
    data.bars[-1] = replace(data.bars[-1], high=106, close=105, volume=2000)
    result = analyze_daily(data, "swing")
    assert result.ema_fast == pytest.approx(100 + 2 / 21 * 5)
    assert result.ema_slow == pytest.approx(100 + 2 / 51 * 5)
    assert result.atr == pytest.approx((13 * 2 + 7) / 14)
    assert result.rsi == 100
    assert result.price_vs_ema == "above" and result.ema_alignment == "bullish"
    assert result.relative_volume == 2
    assert result.return_5_pct == result.return_20_pct == result.return_60_pct == pytest.approx(5)
    assert result.levels.rolling_20_high == 106


def test_close_only_bars_support_closes_but_never_true_ranges():
    data = series()
    data.bars[-1] = replace(data.bars[-1], ohlc_kind="close_only")
    result = analyze_daily(data, "position")
    assert result.ema_fast == 100 and result.return_60_pct == 0
    assert result.atr is None and "atr" in result.unavailable
    assert result.levels.last_high is None and result.levels.rolling_5_low is None


def test_incomplete_older_bar_only_invalidates_affected_windows():
    data = series()
    data.bars[-40] = replace(data.bars[-40], ohlc_kind="close_only")
    result = analyze_daily(data, "swing")
    assert result.levels.rolling_20_high == 101
    assert result.levels.rolling_60_high is None and result.atr is None


@pytest.mark.parametrize("volumes", [[None] * 21, [0.] * 21, [1000.] * 19 + [None, 1000.]])
def test_relative_volume_requires_complete_positive_baseline(volumes):
    data = series()
    for offset, volume in enumerate(volumes, len(data.bars) - 21):
        data.bars[offset] = replace(data.bars[offset], volume=volume)
    assert analyze_daily(data, "swing").relative_volume is None


def test_short_history_is_partial_analysis_not_fabricated_signal():
    result = analyze_daily(series(4), "swing")
    assert result.ema_fast is result.rsi is result.atr is result.relative_volume is None
    assert result.levels.last_high == 101 and result.levels.rolling_5_high is None
    assert result.return_5_pct is None
    assert {"ema", "atr", "rsi", "relative_volume", "range_5"} <= result.unavailable.keys()


def test_returns_use_n_sessions_prior_not_n_bars():
    data = series(6)
    data.bars[0] = replace(data.bars[0], close=50)
    result = analyze_daily(data, "intraday")
    assert result.return_5_pct == 100 and result.return_20_pct is None


@pytest.mark.parametrize("tf,count", [(TimeFrame.D1, 0), (TimeFrame.M5, 300)])
def test_requires_completed_daily_series(tf, count):
    with pytest.raises(ValueError):
        analyze_daily(replace(series(count), timeframe=tf), "swing")


def test_unsupported_horizon_is_rejected():
    with pytest.raises(ValueError):
        policy_for("forever")


def test_missing_open_does_not_discard_reported_high_low():
    data = series()
    data.bars[:] = [replace(bar, ohlc_kind="range_only") for bar in data.bars]
    result = analyze_daily(data, "position")
    assert result.atr == 2 and result.levels.rolling_20_high == 101
