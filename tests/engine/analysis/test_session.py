from datetime import datetime, timedelta, timezone

from trader_agent.engine.analysis import analyze_session
from trader_agent.engine.marketdata.types import BarData, OHLCV, SessionSnapshot, TimeFrame


def test_session_uses_previous_day_not_same_day_and_preserves_sample_quality():
    now = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
    previous = OHLCV(now - timedelta(days=1), 100, 101, 99, 100, 1000)
    observed = OHLCV(now, 101, 105, 101, 103, None, "sampled")
    snapshot = SessionSnapshot("TEST", observed, now + timedelta(hours=2))
    daily = BarData("TEST", TimeFrame.D1, [previous, observed])
    result = analyze_session(snapshot, daily)
    assert result.previous_close == 100 and round(result.change_pct, 8) == 3
    assert result.range_position_pct == 50
    assert result.ohlc_kind == "sampled" and result.as_of == snapshot.as_of
    assert result.observed_open == 101


def test_flat_session_or_no_previous_close_is_not_fake_neutral():
    now = datetime(2026, 10, 6, 10, tzinfo=timezone.utc)
    bar = OHLCV(now, 100, 100, 100, 100, None, "sampled")
    result = analyze_session(SessionSnapshot("TEST", bar, now), BarData("TEST", TimeFrame.D1, []))
    assert result.range_position_pct is result.previous_close is result.change_pct is None
