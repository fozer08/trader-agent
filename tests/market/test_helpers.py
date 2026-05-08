from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from trader_agent.market.helpers import aggregate_bars, calc_session_bounds
from trader_agent.market.types import Bar, TimeFrame


TZ = ZoneInfo("Europe/Istanbul")


def test_aggregate_bars_uses_session_start_alignment_for_hourly_bars():
    bars = [
        _bar("2026-04-29T09:30:00+03:00", 10.0),
        _bar("2026-04-29T09:45:00+03:00", 12.0),
        _bar("2026-04-29T10:15:00+03:00", 11.0),
        _bar("2026-04-29T10:30:00+03:00", 13.0),
    ]

    aggregated = aggregate_bars(
        bars=bars,
        target_tf=TimeFrame.H1,
        closed_until=datetime.fromisoformat("2026-04-29T11:30:00+03:00"),
        session_start=time(9, 30),
    )

    assert [bar.datetime for bar in aggregated] == [
        datetime.fromisoformat("2026-04-29T09:30:00+03:00"),
        datetime.fromisoformat("2026-04-29T10:30:00+03:00"),
    ]
    assert aggregated[0].open == 10.0
    assert aggregated[0].high == 12.0
    assert aggregated[0].low == 10.0
    assert aggregated[0].close == 11.0
    assert aggregated[0].is_closed is True


def test_calc_session_bounds_uses_delay_for_reliable_end():
    bounds = calc_session_bounds(
        now=datetime.fromisoformat("2026-04-29T14:40:00+03:00"),
        session_start=time(10, 0),
        session_end=time(18, 0),
        delay_minutes=15,
    )

    assert bounds == (
        datetime.fromisoformat("2026-04-29T10:00:00+03:00"),
        datetime.fromisoformat("2026-04-29T14:25:00+03:00"),
    )


def test_calc_session_bounds_returns_none_before_reliable_session_start():
    bounds = calc_session_bounds(
        now=datetime.fromisoformat("2026-04-29T10:10:00+03:00"),
        session_start=time(10, 0),
        session_end=time(18, 0),
        delay_minutes=15,
    )

    assert bounds is None


def _bar(
    dt: str,
    price: float,
) -> Bar:
    return Bar(
        symbol="TEST",
        datetime=datetime.fromisoformat(dt).astimezone(TZ),
        timeframe=TimeFrame.M15,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=None,
        is_closed=True,
    )
