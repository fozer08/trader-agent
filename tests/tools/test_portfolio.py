from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from trader_agent.market.base import MarketDataProvider
from trader_agent.types import Bar, IntradaySnapshot, TimeFrame
from trader_agent.repository.base import Base
from trader_agent.repository.portfolio import PortfolioRepository
from trader_agent.tools.portfolio import PortfolioTools


class _MockProvider(MarketDataProvider):
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


def _make_tools(prices: dict[str, float] | None = None, delay_minutes: int | None = None) -> PortfolioTools:
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return PortfolioTools(
        repository=PortfolioRepository(session_factory),
        provider=_MockProvider(prices=prices, delay_minutes=delay_minutes),
    )


@pytest.fixture
def tools() -> PortfolioTools:
    return _make_tools()


# ---- buy_position ------------------------------------------------------------

async def test_buy_position_new_symbol(tools):
    result = await tools.buy_position("THYAO", 100, 285.50)
    pos = result["position"]
    assert pos["symbol"] == "THYAO"
    assert pos["quantity"] == 100
    assert pos["avg_cost"] == 285.50
    assert "opened_at" in pos
    assert pos["hold_days"] >= 1


async def test_buy_position_with_levels(tools):
    result = await tools.buy_position("THYAO", 100, 100.0, stop_loss=95.0, target=110.0)
    pos = result["position"]
    assert pos["stop_loss"] == 95.0
    assert pos["target"] == 110.0


async def test_buy_position_weighted_average(tools):
    await tools.buy_position("THYAO", 100, 10.0)
    result = await tools.buy_position("THYAO", 50, 13.0)
    assert result["position"]["quantity"] == 150
    assert result["position"]["avg_cost"] == pytest.approx(11.0)


async def test_buy_position_invalid_returns_error(tools):
    result = await tools.buy_position("THYAO", 0, 100.0)
    assert "error" in result


# ---- sell_position -----------------------------------------------------------

async def test_sell_position_partial(tools):
    await tools.buy_position("THYAO", 100, 10.0)
    result = await tools.sell_position("THYAO", 30, 12.0)
    assert result["closed"] is False
    pos = result["position"]
    assert pos["quantity"] == 70
    assert pos["realized_pnl"] == pytest.approx(30 * (12.0 - 10.0))


async def test_sell_position_full_archives(tools):
    await tools.buy_position("THYAO", 100, 10.0)
    result = await tools.sell_position("THYAO", 100, 12.0)
    assert result["closed"] is True
    archived = result["archived"]
    assert archived["symbol"] == "THYAO"
    assert archived["realized_pnl"] == pytest.approx(200.0)
    assert "annualized_return_pct" in archived


async def test_sell_position_too_many(tools):
    await tools.buy_position("THYAO", 100, 10.0)
    result = await tools.sell_position("THYAO", 150, 12.0)
    assert "error" in result


async def test_sell_position_missing(tools):
    result = await tools.sell_position("XYZ", 10, 5.0)
    assert "error" in result


# ---- set_levels --------------------------------------------------------------

async def test_set_levels_updates_stop(tools):
    await tools.buy_position("THYAO", 100, 100.0, stop_loss=90.0, target=120.0)
    result = await tools.set_levels("THYAO", stop_loss=95.0)
    assert result["position"]["stop_loss"] == 95.0
    assert result["position"]["target"] == 120.0


async def test_set_levels_adds_to_position_without_levels(tools):
    await tools.buy_position("THYAO", 100, 100.0)
    result = await tools.set_levels("THYAO", stop_loss=90.0, target=120.0)
    assert result["position"]["stop_loss"] == 90.0
    assert result["position"]["target"] == 120.0


async def test_set_levels_missing(tools):
    result = await tools.set_levels("XYZ", stop_loss=10.0)
    assert "error" in result


async def test_set_levels_requires_at_least_one(tools):
    await tools.buy_position("THYAO", 100, 100.0)
    result = await tools.set_levels("THYAO")
    assert "error" in result


# ---- remove / clear ----------------------------------------------------------

async def test_remove_position_existing(tools):
    await tools.buy_position("THYAO", 10, 100.0)
    result = await tools.remove_position("THYAO")
    assert result == {"symbol": "THYAO", "removed": True}


async def test_remove_position_missing(tools):
    result = await tools.remove_position("THYAO")
    assert "error" in result


async def test_clear_portfolio(tools):
    await tools.buy_position("THYAO", 10, 100.0)
    await tools.buy_position("AKBNK", 20, 50.0)
    result = await tools.clear_portfolio()
    assert result == {"removed_count": 2}


async def test_clear_portfolio_empty(tools):
    result = await tools.clear_portfolio()
    assert result == {"removed_count": 0}


# ---- list_portfolio ----------------------------------------------------------

async def test_list_portfolio_empty(tools):
    result = await tools.list_portfolio()
    assert result == {"positions": []}


async def test_list_portfolio_sorted(tools):
    await tools.buy_position("THYAO", 10, 100.0)
    await tools.buy_position("AKBNK", 20, 50.0)
    result = await tools.list_portfolio()
    assert [p["symbol"] for p in result["positions"]] == ["AKBNK", "THYAO"]


async def test_list_portfolio_enriches_with_unrealized():
    tools = _make_tools(prices={"THYAO": 110.0}, delay_minutes=15)
    await tools.buy_position("THYAO", 100, 100.0, stop_loss=95.0, target=120.0)
    result = await tools.list_portfolio()
    pos = result["positions"][0]
    assert pos["current_price"] == 110.0
    assert pos["unrealized_pnl_pct"] == pytest.approx(10.0)
    assert pos["unrealized_pnl"] == pytest.approx(100 * (110.0 - 100.0))
    assert pos["distance_to_stop_pct"] == pytest.approx(round((110.0 - 95.0) / 110.0 * 100, 2))
    assert pos["distance_to_target_pct"] == pytest.approx(round((120.0 - 110.0) / 110.0 * 100, 2))


async def test_list_portfolio_skips_enrichment_when_no_price():
    tools = _make_tools(prices={})
    await tools.buy_position("THYAO", 100, 100.0, stop_loss=95.0)
    result = await tools.list_portfolio()
    pos = result["positions"][0]
    assert pos["current_price"] is None
    assert "unrealized_pnl" not in pos
    assert "unrealized_pnl_pct" not in pos
    assert "distance_to_stop_pct" not in pos
    assert pos["stop_loss"] == 95.0


# ---- get_position_tx --------------------------------------------------------

async def test_get_position_tx_returns_chronological(tools):
    await tools.buy_position("THYAO", 100, 10.0)
    await tools.sell_position("THYAO", 30, 11.0)
    await tools.buy_position("THYAO", 50, 12.0)
    result = await tools.get_position_tx("THYAO")
    txs = result["transactions"]
    assert len(txs) == 3
    assert [t["kind"] for t in txs] == ["BUY", "SELL", "BUY"]
    assert [t["quantity"] for t in txs] == [100, 30, 50]


async def test_get_position_tx_missing_returns_error(tools):
    result = await tools.get_position_tx("XYZ")
    assert "error" in result


# ---- list_closed -------------------------------------------------------------

async def test_list_closed_after_full_sell(tools):
    await tools.buy_position("THYAO", 100, 10.0)
    await tools.sell_position("THYAO", 100, 12.0)
    result = await tools.list_closed()
    assert len(result["closed"]) == 1
    c = result["closed"][0]
    assert c["symbol"] == "THYAO"
    assert c["realized_pnl"] == pytest.approx(200.0)
    assert c["hold_days"] >= 1


async def test_list_closed_filtered_by_symbol(tools):
    await tools.buy_position("THYAO", 10, 100.0)
    await tools.sell_position("THYAO", 10, 110.0)
    await tools.buy_position("AKBNK", 20, 50.0)
    await tools.sell_position("AKBNK", 20, 55.0)
    result = await tools.list_closed(symbol="THYAO")
    assert len(result["closed"]) == 1
    assert result["closed"][0]["symbol"] == "THYAO"


async def test_list_closed_empty(tools):
    result = await tools.list_closed()
    assert result == {"closed": []}


# ---- as_tool_list ------------------------------------------------------------

def test_as_tool_list_returns_all_tools(tools):
    names = {t.name for t in tools.as_tool_list()}
    assert names == {
        "buy_position",
        "sell_position",
        "set_levels",
        "remove_position",
        "clear_portfolio",
        "list_portfolio",
        "get_position_tx",
        "list_closed",
    }
