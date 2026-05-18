from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trader_agent.market.base import MarketDataProvider
from trader_agent.types import Bar, IntradaySnapshot, TimeFrame
from trader_agent.tools.analysis import AnalysisTools


# ---- Mock provider -----------------------------------------------------------

class MockProvider(MarketDataProvider):
    """Minimal MarketDataProvider stub'u; AnalysisTools davranışı testlenir."""

    calls_per_minute = 60

    def __init__(
        self,
        daily: dict[str, list[Bar]] | None = None,
        intraday: dict[tuple[str, TimeFrame], list[Bar]] | None = None,
        today: dict[str, IntradaySnapshot | None] | None = None,
    ) -> None:
        self._daily = daily or {}
        self._intraday = intraday or {}
        self._today = today or {}

    async def get_daily(self, symbol: str, period: int) -> list[Bar]:
        return self._daily.get(symbol, [])[-period:]

    async def get_today(self, symbol: str) -> IntradaySnapshot | None:
        return self._today.get(symbol)

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
    )


def _daily_bars(n: int, symbol: str = "THYAO") -> list[Bar]:
    return [_bar(i, symbol=symbol, close=100.0 + i * 0.5) for i in range(n)]


def _intraday_bars(n: int, symbol: str = "THYAO", tf: TimeFrame = TimeFrame.M15) -> list[Bar]:
    return [
        Bar(
            symbol=symbol,
            datetime=datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=i * tf.minutes),
            timeframe=tf,
            open=100 + i * 0.1,
            high=101 + i * 0.1,
            low=99 + i * 0.1,
            close=100 + i * 0.1,
            volume=1000.0,
        )
        for i in range(n)
    ]


def _snapshot(symbol: str = "THYAO", close: float = 125.0) -> IntradaySnapshot:
    return IntradaySnapshot(
        symbol=symbol,
        datetime=datetime(2026, 1, 1, tzinfo=timezone.utc),
        open=close,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=1000.0,
    )


WATCHLIST = [
    {"symbol": "THYAO", "name": "Türk Hava Yolları"},
    {"symbol": "AKBNK", "name": "Akbank"},
]


# ---- scan --------------------------------------------------------------------

async def test_scan_returns_one_entry_per_watchlist_symbol():
    provider = MockProvider(daily={
        "THYAO": _daily_bars(30, "THYAO"),
        "AKBNK": _daily_bars(30, "AKBNK"),
    })
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    symbols = {r["symbol"] for r in results}
    assert symbols == {"THYAO", "AKBNK"}


async def test_scan_daily_block_has_expected_fields():
    provider = MockProvider(daily={
        "THYAO": _daily_bars(30, "THYAO"),
        "AKBNK": _daily_bars(30, "AKBNK"),
    })
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    for r in results:
        assert "daily" in r
        for key in ("close", "ema_trend", "ema_alignment", "rsi", "atr", "relative_volume", "candle"):
            assert key in r["daily"]


async def test_scan_pulse_null_when_no_today_bar():
    provider = MockProvider(
        daily={"THYAO": _daily_bars(30), "AKBNK": _daily_bars(30, "AKBNK")},
        today={},  # boş — get_today None döner
    )
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    for r in results:
        assert r["pulse"] is None


async def test_scan_pulse_present_when_today_bar():
    provider = MockProvider(
        daily={"THYAO": _daily_bars(30), "AKBNK": _daily_bars(30, "AKBNK")},
        today={"THYAO": _snapshot("THYAO", 125.0), "AKBNK": _snapshot("AKBNK", 60.0)},
    )
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    thyao = next(r for r in results if r["symbol"] == "THYAO")
    assert thyao["pulse"] is not None
    assert thyao["pulse"]["current_price"] == 125.0
    assert "change_pct" in thyao["pulse"]
    assert "price_vs_ema" in thyao["pulse"]


async def test_scan_returns_error_for_symbol_with_insufficient_bars():
    provider = MockProvider(daily={"THYAO": _daily_bars(1), "AKBNK": _daily_bars(30, "AKBNK")})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.scan()
    thyao = next(r for r in results if r["symbol"] == "THYAO")
    assert "error" in thyao


# ---- get_daily_indicators -----------------------------------------------------

async def test_get_daily_indicators_structure():
    provider = MockProvider(daily={"THYAO": _daily_bars(60)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_daily_indicators(["THYAO"])
    assert len(results) == 1
    r = results[0]
    assert r["symbol"] == "THYAO"
    assert r["name"] == "Türk Hava Yolları"
    ind = r["indicators"]
    for key in ("ema_trend", "ema_alignment", "rsi", "rsi_divergence", "macd_histogram", "atr", "bb_width", "bb_pct_b"):
        assert key in ind


async def test_get_daily_indicators_error_on_insufficient_bars():
    provider = MockProvider(daily={"THYAO": _daily_bars(1)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_daily_indicators(["THYAO"])
    assert "error" in results[0]


async def test_get_daily_indicators_multiple_symbols_parallel():
    provider = MockProvider(daily={
        "THYAO": _daily_bars(60, "THYAO"),
        "AKBNK": _daily_bars(60, "AKBNK"),
    })
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_daily_indicators(["THYAO", "AKBNK"])
    assert {r["symbol"] for r in results} == {"THYAO", "AKBNK"}


# ---- get_intraday_indicators --------------------------------------------------

async def test_get_intraday_indicators_returns_m15_and_m5():
    provider = MockProvider(intraday={
        ("THYAO", TimeFrame.M15): _intraday_bars(20, tf=TimeFrame.M15),
        ("THYAO", TimeFrame.M5): _intraday_bars(20, tf=TimeFrame.M5),
    })
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_intraday_indicators(["THYAO"])
    r = results[0]
    assert "m15" in r
    assert "m5" in r


async def test_get_intraday_indicators_error_when_both_empty():
    provider = MockProvider(intraday={})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_intraday_indicators(["THYAO"])
    assert "error" in results[0]


# ---- get_pulse ---------------------------------------------------------------

async def test_get_pulse_returns_session_snapshot_when_today_available():
    provider = MockProvider(
        daily={"THYAO": _daily_bars(30)},
        today={"THYAO": _snapshot("THYAO", 125.0)},
    )
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_pulse(["THYAO"])
    r = results[0]
    assert r["pulse"]["current_price"] == 125.0
    assert "change_pct" in r["pulse"]
    assert "gap_pct" in r["pulse"]
    assert "range_position" in r["pulse"]


async def test_get_pulse_error_when_session_closed():
    provider = MockProvider(daily={"THYAO": _daily_bars(30)}, today={})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_pulse(["THYAO"])
    assert "error" in results[0]


# ---- get_levels --------------------------------------------------------------

async def test_get_levels_structure():
    provider = MockProvider(daily={"THYAO": _daily_bars(30)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_levels(["THYAO"])
    r = results[0]
    assert r["symbol"] == "THYAO"
    levels = r["levels"]
    for key in ("pivot", "prev_high", "prev_low", "prev_close", "weekly_high", "weekly_low", "candle", "relative_volume"):
        assert key in levels


async def test_get_levels_pivot_fields():
    provider = MockProvider(daily={"THYAO": _daily_bars(30)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_levels(["THYAO"])
    pivot = results[0]["levels"]["pivot"]
    for key in ("pp", "r1", "r2", "s1", "s2"):
        assert key in pivot


async def test_get_levels_error_on_insufficient_bars():
    provider = MockProvider(daily={"THYAO": _daily_bars(1)})
    tools = AnalysisTools(provider, WATCHLIST)
    results = await tools.get_levels(["THYAO"])
    assert "error" in results[0]


# ---- as_tool_list ------------------------------------------------------------

def test_as_tool_list_has_all_five_tools():
    tools = AnalysisTools(MockProvider(), WATCHLIST)
    assert len(tools.as_tool_list()) == 5


def test_as_tool_list_names():
    tools = AnalysisTools(MockProvider(), WATCHLIST)
    names = {t.name for t in tools.as_tool_list()}
    assert names == {
        "scan",
        "get_daily_indicators",
        "get_intraday_indicators",
        "get_pulse",
        "get_levels",
    }


def test_tool_to_api_dict_has_required_keys():
    tools = AnalysisTools(MockProvider(), WATCHLIST)
    for tool in tools.as_tool_list():
        d = tool.to_api_dict()
        assert "name" in d
        assert "description" in d
        assert "input_schema" in d
