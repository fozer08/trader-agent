from __future__ import annotations

import pytest

from trader_agent.config.market import MarketConfig
from trader_agent.engine.marketdata.cache import MarketBarCache
from trader_agent.engine.calendar.loader import load_calendar
from trader_agent.engine.marketdata.provider import IsYatirimProvider
from trader_agent.engine.marketdata.service import MarketService
from trader_agent.engine.marketdata.types import OHLCV, SessionSnapshot, TimeFrame
from trader_agent.env import load_env


@pytest.fixture
def bar_store():
    return MarketBarCache()


@pytest.fixture
async def provider(bar_store):
    load_env()
    config = MarketConfig.load()
    calendar = load_calendar(config)
    async with IsYatirimProvider(calendar=calendar, timeout=10, store=bar_store) as p:
        yield p


@pytest.fixture
def market(provider, bar_store):
    return MarketService(provider, store=bar_store)


@pytest.mark.integration
async def test_get_daily_returns_bars(provider):
    bars = await provider.get_daily("THYAO", period=5)
    assert len(bars.bars) > 0
    assert bars.symbol == "THYAO"
    assert bars.timeframe is TimeFrame.D1
    assert all(isinstance(b, OHLCV) for b in bars.bars)
    assert all(b.close > 0 for b in bars.bars)


@pytest.mark.integration
async def test_get_intraday_m5(market):
    bars = await market.get_intraday("THYAO", TimeFrame.M5)
    assert bars.symbol == "THYAO"
    assert bars.timeframe is TimeFrame.M5
    assert all(isinstance(b, OHLCV) for b in bars.bars)


@pytest.mark.integration
async def test_get_today_returns_snapshot_or_none(market):
    result = await market.get_today("THYAO")
    assert result is None or isinstance(result, SessionSnapshot)
    if result is not None:
        assert result.ohlcv.close > 0
