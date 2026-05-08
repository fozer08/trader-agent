from __future__ import annotations

import pytest

from trader_agent.config.market import MarketConfig
from trader_agent.market.provider import IsYatirimProvider
from trader_agent.market.types import Bar, TimeFrame


@pytest.fixture
async def provider():
    config = MarketConfig.load()
    session = config.exchanges["bist"].trading_session("equities")
    async with IsYatirimProvider(session=session, timeout=10) as p:
        yield p


@pytest.mark.integration
async def test_get_daily_returns_bars(provider):
    bars = await provider.get_daily("THYAO", period=5)
    assert len(bars) > 0
    assert all(isinstance(b, Bar) for b in bars)
    assert all(b.timeframe is TimeFrame.D1 for b in bars)
    assert all(b.close > 0 for b in bars)


@pytest.mark.integration
async def test_get_intraday_m5(provider):
    bars = await provider.get_intraday("THYAO", TimeFrame.M5)
    assert all(isinstance(b, Bar) for b in bars)
    assert all(b.timeframe is TimeFrame.M5 for b in bars)


@pytest.mark.integration
async def test_get_today_returns_bar_or_none(provider):
    result = await provider.get_today("THYAO")
    assert result is None or isinstance(result, Bar)
    if result is not None:
        assert result.timeframe is TimeFrame.D1
        assert result.close > 0
