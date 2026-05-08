from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trader_agent.market.provider_base import MarketDataProvider
from trader_agent.market.types import Bar, TimeFrame
from trader_agent.tools.analysis import AnalysisTools


# ---- Mock provider -----------------------------------------------------------

class MockProvider(MarketDataProvider):
    def __init__(
        self,
        daily: dict[str, list[Bar]] | None = None,
        intraday: dict[tuple[str, TimeFrame], list[Bar]] | None = None,
        today: Bar | None = None,
    ) -> None:
        self._daily = daily or {}
        self._intraday = intraday or {}
        self._today = today

    async def get_daily(self, symbol: str, period: int) -> list[Bar]:
        return self._daily.get(symbol, [])[-period:]

    async def get_today(self, symbol: str) -> Bar | None:
        return self._today

    async def get_intraday(self, symbol: str, tf: TimeFrame) -> list[Bar]:
        return self._intraday.get((symbol, tf), [])


# ---- Helpers -----------------------------------------------------------------

def _bar(i: int, symbol: str = "THYAO", tf: TimeFrame = TimeFrame.D1, close: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
        timeframe=tf,
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000.0,
        is_closed=True,
    )


def _daily_bars(n: int, symbol: str = "THYAO") -> list[Bar]:
    return [_bar(i, symbol=symbol, close=100.0 + i * 0.5) for i in range(n)]


def _intraday_bars(n: int, symbol: str = "THYAO", tf: TimeFrame = TimeFrame.M15) -> list[Bar]:
    return [_bar(i, symbol=symbol, tf=tf, close=100.0 + i * 0.1) for i in range(n)]


WATCHLIST = [
    {"symbol": "THYAO", "name": "Türk Hava Yolları"},
    {"symbol": "AKBNK", "name": "Akbank"},
]


# ---- scan --------------------------------------------------------------------

async def test_scan_full_watchlist_when_no_symbols():
    provider = MockProvider(daily={
        "THYAO": _daily_bars(30, "THYAO"),
        "AKBNK": _daily_bars(30, "AKBNK"),
    })
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    symbols = [r["symbol"] for r in results]
    assert "THYAO" in symbols
    assert "AKBNK" in symbols


async def test_scan_filtered_when_symbols_given():
    provider = MockProvider(daily={
        "THYAO": _daily_bars(30, "THYAO"),
        "AKBNK": _daily_bars(30, "AKBNK"),
    })
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan(symbols=["THYAO"])
    assert len(results) == 1
    assert results[0]["symbol"] == "THYAO"


async def test_scan_d1_structure():
    provider = MockProvider(daily={"THYAO": _daily_bars(30), "AKBNK": _daily_bars(30, "AKBNK")})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    for r in results:
        assert "d1" in r
        assert "close" in r["d1"]
        assert "ema_trend" in r["d1"]
        assert "ema_alignment" in r["d1"]
        assert "rsi" in r["d1"]
        assert "atr" in r["d1"]
        assert "relative_volume" in r["d1"]
        assert "candle" in r["d1"]


async def test_scan_session_null_when_no_today():
    provider = MockProvider(daily={"THYAO": _daily_bars(30), "AKBNK": _daily_bars(30, "AKBNK")}, today=None)
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    for r in results:
        assert r["session"] is None


async def test_scan_session_present_when_today_bar():
    today_bar = _bar(0, symbol="THYAO", tf=TimeFrame.D1, close=125.0)
    provider = MockProvider(daily={"THYAO": _daily_bars(30), "AKBNK": _daily_bars(30, "AKBNK")}, today=today_bar)
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    thyao = next(r for r in results if r["symbol"] == "THYAO")
    assert thyao["session"] is not None
    assert thyao["session"]["current_price"] == 125.0
    assert "change_pct" in thyao["session"]
    assert "price_vs_ema" in thyao["session"]


async def test_scan_error_on_insufficient_bars():
    provider = MockProvider(daily={"THYAO": _daily_bars(1), "AKBNK": _daily_bars(30, "AKBNK")})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    thyao = next(r for r in results if r["symbol"] == "THYAO")
    assert "error" in thyao


async def test_scan_handles_missing_symbol_gracefully():
    provider = MockProvider(daily={"THYAO": _daily_bars(30)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    akbnk = next((r for r in results if r["symbol"] == "AKBNK"), None)
    assert akbnk is not None
    assert "error" in akbnk


# ---- get_technicals ----------------------------------------------------------

async def test_get_technicals_d1_structure():
    provider = MockProvider(daily={"THYAO": _daily_bars(60)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_technicals(["THYAO"])
    assert len(results) == 1
    r = results[0]
    assert r["symbol"] == "THYAO"
    assert r["name"] == "Türk Hava Yolları"
    assert "d1" in r
    assert "indicators" in r["d1"]
    assert "levels" in r["d1"]
    assert "close" in r["d1"]


async def test_get_technicals_session_null_when_no_today():
    provider = MockProvider(daily={"THYAO": _daily_bars(60)}, today=None)
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_technicals(["THYAO"])
    assert results[0]["session"] is None


async def test_get_technicals_session_fields_when_today_bar():
    today_bar = _bar(0, symbol="THYAO", tf=TimeFrame.D1, close=125.0)
    provider = MockProvider(daily={"THYAO": _daily_bars(60)}, today=today_bar)
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_technicals(["THYAO"])
    session = results[0]["session"]
    assert session is not None
    assert session["current_price"] == 125.0
    assert "change_pct" in session
    assert "gap_pct" in session
    assert "range_pct" in session
    assert "range_position" in session
    assert "price_vs_ema" in session
    assert "volume" in session
    assert "relative_volume" in session


async def test_get_technicals_intraday_included_when_session_active():
    provider = MockProvider(
        daily={"THYAO": _daily_bars(60)},
        intraday={
            ("THYAO", TimeFrame.M15): _intraday_bars(20, tf=TimeFrame.M15),
            ("THYAO", TimeFrame.M5): _intraday_bars(20, tf=TimeFrame.M5),
        },
    )
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_technicals(["THYAO"])
    r = results[0]
    assert r["session_active"] is True
    assert "m15" in r
    assert "m5" in r


async def test_get_technicals_intraday_excluded_when_session_closed():
    provider = MockProvider(daily={"THYAO": _daily_bars(60)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_technicals(["THYAO"])
    r = results[0]
    assert r["session_active"] is False
    assert "m15" not in r
    assert "m5" not in r


async def test_get_technicals_error_on_insufficient_bars():
    provider = MockProvider(daily={"THYAO": _daily_bars(1)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_technicals(["THYAO"])
    assert "error" in results[0]


async def test_get_technicals_multiple_symbols():
    provider = MockProvider(daily={
        "THYAO": _daily_bars(60, "THYAO"),
        "AKBNK": _daily_bars(60, "AKBNK"),
    })
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_technicals(["THYAO", "AKBNK"])
    assert len(results) == 2


# ---- get_levels --------------------------------------------------------------

async def test_get_levels_structure():
    provider = MockProvider(daily={"THYAO": _daily_bars(30)})
    tools = AnalysisTools(provider, WATCHLIST)
    result = await tools.get_levels("THYAO")
    assert result["symbol"] == "THYAO"
    assert "levels" in result
    levels = result["levels"]
    assert "pivot" in levels
    assert "prev_high" in levels
    assert "prev_low" in levels
    assert "prev_close" in levels
    assert "weekly_high" in levels
    assert "weekly_low" in levels
    assert "candle" in levels
    assert "relative_volume" in levels


async def test_get_levels_pivot_fields():
    provider = MockProvider(daily={"THYAO": _daily_bars(30)})
    tools = AnalysisTools(provider, WATCHLIST)
    result = await tools.get_levels("THYAO")
    pivot = result["levels"]["pivot"]
    assert "pp" in pivot
    assert "r1" in pivot
    assert "r2" in pivot
    assert "s1" in pivot
    assert "s2" in pivot


async def test_get_levels_error_on_insufficient_bars():
    provider = MockProvider(daily={"THYAO": _daily_bars(1)})
    tools = AnalysisTools(provider, WATCHLIST)
    result = await tools.get_levels("THYAO")
    assert "error" in result


# ---- as_tool_list ------------------------------------------------------------

def test_as_tool_list_has_three_tools():
    tools = AnalysisTools(MockProvider(), WATCHLIST)
    assert len(tools.as_tool_list()) == 3


def test_as_tool_list_names():
    tools = AnalysisTools(MockProvider(), WATCHLIST)
    names = {t.name for t in tools.as_tool_list()}
    assert names == {"scan", "get_technicals", "get_levels"}


def test_tool_to_api_dict_has_required_keys():
    tools = AnalysisTools(MockProvider(), WATCHLIST)
    for tool in tools.as_tool_list():
        d = tool.to_api_dict()
        assert "name" in d
        assert "description" in d
        assert "input_schema" in d
