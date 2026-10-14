import asyncio
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from trader_agent.engine.analysis.service import AnalysisService
from trader_agent.engine.calendar import ExchangeSchedule, TradingCalendar
from trader_agent.engine.marketdata.base import ProviderError
from trader_agent.engine.marketdata.cache import MarketBarCache
from trader_agent.engine.marketdata.service import MarketService
from trader_agent.engine.marketdata.types import BarData, OHLCV, TimeFrame
from trader_agent.engine.tools.analysis import AnalysisTools


@pytest.fixture
def setup():
    calendar = TradingCalendar(ExchangeSchedule(time(10), time(18), timezone.utc), {}, frozenset({2025, 2026}))
    market = MarketService(SimpleNamespace(calendar=calendar, delay_minutes=15), MarketBarCache())
    market.now = lambda: datetime(2026, 10, 6, 15, tzinfo=timezone.utc)
    bars = [OHLCV(datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i),
                  100, 102, 99, 101, 1000) for i in range(400)]
    market.get_daily = AsyncMock(side_effect=lambda symbol, count: BarData(symbol, TimeFrame.D1, bars[-count:]))
    market.get_today = AsyncMock(return_value=None)
    market.get_intraday = AsyncMock()
    stocks = {symbol: {"symbol": symbol, "name": symbol} for symbol in ["THYAO", "AKBNK"]}
    watchlist = SimpleNamespace(list=lambda: list(stocks.values()), stock=lambda symbol: stocks[symbol])
    service = AnalysisService(market)
    return AnalysisTools(service, watchlist), market, service


@pytest.mark.parametrize("horizon,history", [("intraday", 105), ("swing", 250), ("position", 300)])
async def test_scan_and_detail_share_history_and_values(setup, horizon, history):
    tools, market, _ = setup
    scan = await tools.scan(horizon)
    detail = (await tools.analyze(["THYAO"], horizon))[0]
    assert len(scan) == 2
    for key, value in scan[0]["daily"].items():
        assert value == detail["daily"][key]
    assert all(call.args[1] == history for call in market.get_daily.await_args_list)
    assert detail["daily_stale"] is True  # Old daily data remains visible as stale.
    market.get_intraday.assert_not_called()


async def test_missing_symbol_and_provider_failure_are_explicit(setup):
    tools, market, _ = setup
    market.get_daily.side_effect = ProviderError("provider unavailable")
    result = await tools.analyze(["THYAO", "UNKNOWN"], "swing")
    assert [item["symbol"] for item in result] == ["THYAO", "UNKNOWN"]
    assert all("error" in item for item in result)


async def test_session_error_preserves_daily_with_error_context(setup):
    tools, market, _ = setup
    market.get_today.side_effect = ProviderError("session unavailable")
    result = (await tools.analyze(["THYAO"], "intraday"))[0]
    assert result["daily"]["close"] == 101
    assert result["session"] is None and result["session_error"] == "session unavailable"
    assert result["delay_minutes"] == 15


async def test_programming_errors_and_cancellation_are_not_silenced(setup):
    tools, market, _ = setup
    market.get_today.side_effect = RuntimeError("bug")
    with pytest.raises(RuntimeError, match="bug"):
        await tools.analyze(["THYAO"], "swing")
    market.get_today.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await tools.scan("swing")


async def test_tool_validation_happens_before_io(setup):
    tools, market, _ = setup
    for symbols, horizon in [([], "swing"), (["THYAO"] * 11, "swing"), (["../x"], "swing"), (["THYAO"], "forever")]:
        with pytest.raises(ValidationError):
            await tools.analyze(symbols, horizon)
    market.get_daily.assert_not_called()
    with pytest.raises(ValidationError):
        await tools.evaluate_trade(True, 95, 110)
    with pytest.raises(ValueError, match="stop < entry < target"):
        await tools.evaluate_trade(100, 105, 110)
    assert (await tools.evaluate_trade(100, 95, 110))["reward_to_risk"] == 2


async def test_duplicates_are_coalesced_and_tool_surface_is_small(setup):
    tools, market, _ = setup
    assert len(await tools.analyze(["THYAO", "THYAO"], "swing")) == 1
    assert market.get_daily.await_count == 1
    registered = tools.as_tool_list()
    assert {tool.name for tool in registered} == {"scan", "analyze", "evaluate_trade"}
    assert all(tool.input_schema["additionalProperties"] is False for tool in registered)


async def test_concurrent_requests_share_provider_limit(setup):
    _, market, service = setup
    original = market.get_daily.side_effect
    release = asyncio.Event()
    ready = asyncio.Event()
    active = 0
    peak = 0

    async def delayed(symbol, count):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 3:
            ready.set()
        await release.wait()
        active -= 1
        return original(symbol, count)

    market.get_daily.side_effect = delayed
    tasks = [asyncio.create_task(service.analyze("THYAO", "swing")) for _ in range(10)]
    try:
        await asyncio.wait_for(ready.wait(), 1)
        release.set()
        await asyncio.gather(*tasks)
        assert peak == 3
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_preopen_previous_close_is_current_daily_reference_not_15_minutes_old(setup, bist_calendar):
    tools, market, _ = setup
    market.calendar = bist_calendar
    market.now = lambda: datetime(2026, 10, 14, 9, 10, tzinfo=bist_calendar.timezone)
    bars = []
    session = bist_calendar.previous_session(market.now().date())
    for _ in range(260):
        bars.append(OHLCV(session.start, 100, 102, 99, 101, 1000))
        session = bist_calendar.previous_session(session.date)
    bars.reverse()
    market.get_daily.side_effect = lambda symbol, count: BarData(symbol, TimeFrame.D1, bars[-count:])
    for result in [*(await tools.scan("swing")), *(await tools.analyze(["THYAO"], "swing"))]:
        assert result['observed_at'] == '2026-10-14T09:10:00+03:00'
        assert result['session_date'] == '2026-10-13'
        assert result['as_of'] == '2026-10-13T18:10:00+03:00'
        assert result['price_kind'] == 'close'
        assert result['delay_minutes'] == 15
        assert result['price_age_minutes'] == 900
        assert result['daily_stale'] is False
        assert result['session_open'] is False
        assert result['session'] is None
    # A genuinely missing session must still be reported as stale.
    bars[:] = bars[:-1]
    result = (await tools.analyze(["THYAO"], "swing"))[0]
    assert result['daily_stale'] is True
