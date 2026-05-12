from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.repository.base import Base
from trader_agent.repository.portfolio import PositionRepository
from trader_agent.tools.portfolio import PortfolioTools


@pytest.fixture
def tools() -> PortfolioTools:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PortfolioTools(repository=PositionRepository(session_factory))


# ---- add_position ------------------------------------------------------------

async def test_add_position_new_symbol(tools):
    result = await tools.add_position("THYAO", 100, 285.50)
    assert result["previous"] is None
    assert result["position"]["symbol"] == "THYAO"
    assert result["position"]["quantity"] == 100
    assert result["position"]["avg_cost"] == 285.50
    assert "updated_at" in result["position"]


async def test_add_position_returns_previous_on_update(tools):
    await tools.add_position("THYAO", 100, 10.0)
    result = await tools.add_position("THYAO", 50, 13.0)
    assert result["previous"]["quantity"] == 100
    assert result["position"]["quantity"] == 150
    assert result["position"]["avg_cost"] == pytest.approx(11.0)


async def test_add_position_invalid_returns_error(tools):
    result = await tools.add_position("THYAO", 0, 100.0)
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


# ---- as_tool_list ------------------------------------------------------------

def test_as_tool_list_returns_all_four_tools(tools):
    names = {t.name for t in tools.as_tool_list()}
    assert names == {"add_position", "remove_position", "clear_portfolio", "list_portfolio"}
