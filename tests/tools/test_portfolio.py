from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.market.provider_base import MarketDataProvider
from trader_agent.market.types import Bar, IntradaySnapshot, TimeFrame
from trader_agent.repository.base import Base
from trader_agent.repository.portfolio import PositionRepository
from trader_agent.tools.portfolio import PortfolioTools


class _MockProvider(MarketDataProvider):
    """Test için sade provider; sembol→canlı fiyat eşlemesi tutar."""

    def __init__(self, prices: dict[str, float] | None = None, delay_minutes: int | None = None) -> None:
        self._prices = prices or {}
        self.delay_minutes = delay_minutes

    async def get_today(self, symbol: str) -> IntradaySnapshot | None:
        price = self._prices.get(symbol)
        if price is None:
            return None
        from datetime import datetime, timezone
        return IntradaySnapshot(
            symbol=symbol,
            datetime=datetime.now(timezone.utc),
            open=price,
            high=price,
            low=price,
            close=price,
            volume=1000.0,
        )

    async def get_daily(self, symbol: str, period: int) -> list[Bar]:
        return []

    async def get_intraday(self, symbol: str, tf: TimeFrame) -> list[Bar]:
        return []


@pytest.fixture
def tools() -> PortfolioTools:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PortfolioTools(
        repository=PositionRepository(session_factory),
        provider=_MockProvider(),
    )


def _make_tools(prices: dict[str, float] | None = None, delay_minutes: int | None = None) -> PortfolioTools:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PortfolioTools(
        repository=PositionRepository(session_factory),
        provider=_MockProvider(prices=prices, delay_minutes=delay_minutes),
    )


# ---- add_position ------------------------------------------------------------

async def test_add_position_new_symbol(tools):
    result = await tools.add_position("THYAO", 100, 285.50)
    assert result["previous"] is None
    assert result["position"]["symbol"] == "THYAO"
    assert result["position"]["quantity"] == 100
    assert result["position"]["avg_cost"] == 285.50
    assert "updated_at" in result["position"]


async def test_add_position_with_stop_and_target(tools):
    result = await tools.add_position("THYAO", 100, 100.0, stop_loss=95.0, target=110.0)
    assert result["position"]["stop_loss"] == 95.0
    assert result["position"]["target"] == 110.0


async def test_add_position_returns_previous_on_update(tools):
    await tools.add_position("THYAO", 100, 10.0)
    result = await tools.add_position("THYAO", 50, 13.0)
    assert result["previous"]["quantity"] == 100
    assert result["position"]["quantity"] == 150
    assert result["position"]["avg_cost"] == pytest.approx(11.0)


async def test_add_position_preserves_levels_when_not_given(tools):
    await tools.add_position("THYAO", 100, 100.0, stop_loss=95.0, target=110.0)
    result = await tools.add_position("THYAO", 50, 105.0)
    assert result["position"]["stop_loss"] == 95.0
    assert result["position"]["target"] == 110.0


async def test_add_position_overwrites_levels_when_given(tools):
    await tools.add_position("THYAO", 100, 100.0, stop_loss=95.0, target=110.0)
    result = await tools.add_position("THYAO", 50, 105.0, stop_loss=99.0)
    assert result["position"]["stop_loss"] == 99.0
    assert result["position"]["target"] == 110.0


async def test_add_position_invalid_returns_error(tools):
    result = await tools.add_position("THYAO", 0, 100.0)
    assert "error" in result


# ---- set_position_levels -----------------------------------------------------

async def test_set_position_levels_updates_stop_only(tools):
    await tools.add_position("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    result = await tools.set_position_levels("THYAO", stop_loss=95.0)
    assert result["position"]["stop_loss"] == 95.0
    assert result["position"]["target"] == 120.0


async def test_set_position_levels_missing_position(tools):
    result = await tools.set_position_levels("XYZ", stop_loss=10.0)
    assert "error" in result


async def test_set_position_levels_requires_at_least_one(tools):
    await tools.add_position("THYAO", 100, 100.0)
    result = await tools.set_position_levels("THYAO")
    assert "error" in result


# ---- remove_position ---------------------------------------------------------

async def test_remove_position_existing(tools):
    await tools.add_position("THYAO", 10, 100.0)
    result = await tools.remove_position("THYAO")
    assert result == {"symbol": "THYAO", "removed": True}


async def test_remove_position_missing(tools):
    result = await tools.remove_position("THYAO")
    assert result == {"symbol": "THYAO", "removed": False}


# ---- clear_portfolio ---------------------------------------------------------

async def test_clear_portfolio(tools):
    await tools.add_position("THYAO", 10, 100.0)
    await tools.add_position("AKBNK", 20, 50.0)
    result = await tools.clear_portfolio()
    assert result == {"removed_count": 2}


async def test_clear_portfolio_empty(tools):
    result = await tools.clear_portfolio()
    assert result == {"removed_count": 0}


# ---- list_portfolio ----------------------------------------------------------

async def test_list_portfolio_empty(tools):
    result = await tools.list_portfolio()
    assert result == {"positions": []}


async def test_list_portfolio_returns_positions(tools):
    await tools.add_position("THYAO", 10, 100.0)
    await tools.add_position("AKBNK", 20, 50.0)
    result = await tools.list_portfolio()
    assert [p["symbol"] for p in result["positions"]] == ["AKBNK", "THYAO"]


async def test_list_portfolio_enriches_with_pnl():
    tools = _make_tools(prices={"THYAO": 110.0}, delay_minutes=15)
    await tools.add_position("THYAO", 100, 100.0, stop_loss=95.0, target=120.0)
    result = await tools.list_portfolio()
    pos = result["positions"][0]
    assert pos["current_price"] == 110.0
    assert pos["pnl_pct"] == pytest.approx(10.0)
    assert pos["distance_to_stop_pct"] == pytest.approx(round((110.0 - 95.0) / 110.0 * 100, 2))
    assert pos["distance_to_target_pct"] == pytest.approx(round((120.0 - 110.0) / 110.0 * 100, 2))
    assert result["delay_minutes"] == 15


async def test_list_portfolio_skips_pnl_when_no_price():
    tools = _make_tools(prices={})
    await tools.add_position("THYAO", 100, 100.0, stop_loss=95.0)
    result = await tools.list_portfolio()
    pos = result["positions"][0]
    assert "current_price" not in pos
    assert "pnl_pct" not in pos
    assert pos["stop_loss"] == 95.0


# ---- as_tool_list ------------------------------------------------------------

def test_as_tool_list_returns_all_tools(tools):
    names = {t.name for t in tools.as_tool_list()}
    assert names == {
        "add_position",
        "set_position_levels",
        "remove_position",
        "clear_portfolio",
        "list_portfolio",
    }
