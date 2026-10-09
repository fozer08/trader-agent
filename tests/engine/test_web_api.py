from contextlib import asynccontextmanager
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from trader_agent.engine.api import create_app
from trader_agent.engine.repository import (
    PortfolioRepository,
    WatchlistRepository,
    create_db_engine,
    create_session_factory,
    init_schema,
)
from trader_agent.engine.runtime import EngineRuntime
from trader_agent.engine.tools.analysis import AnalysisTools
from trader_agent.engine.tools.portfolio import PortfolioTools
from trader_agent.engine.marketdata.types import OHLCV, BarData, SessionSnapshot, TimeFrame
from trader_agent.engine.calendar import ExchangeSchedule, TradingCalendar
from trader_agent.engine.marketdata.service import MarketService
from trader_agent.engine.marketdata.cache import MarketBarCache
from trader_agent.engine.marketdata.provider import IsYatirimProvider


@pytest.fixture
async def web_client(tmp_path):
    tz = ZoneInfo("Europe/Istanbul")
    session = ExchangeSchedule(time(10), time(18), tz)
    calendar = TradingCalendar(session, {}, frozenset({2026}))
    dates = []
    current = calendar.previous_session(datetime(2026, 10, 6).date())
    for _ in range(100):
        dates.append(current.start)
        current = calendar.previous_session(current.date)
    bars = [
        OHLCV(
            period_start,
            100 + i,
            103 + i,
            99 + i,
            101 + i,
            1000 + i,
        )
        for i, period_start in enumerate(reversed(dates))
    ]
    market = MarketService(
        SimpleNamespace(calendar=calendar, source="test", delay_minutes=15),
        MarketBarCache(),
    )
    market.get_daily = AsyncMock(return_value=BarData("THYAO", TimeFrame.D1, bars))
    market.get_today = AsyncMock(return_value=None)
    market.get_intraday = AsyncMock(side_effect=lambda symbol, tf: BarData(symbol, tf, []))
    market.now = lambda: datetime(2026, 10, 6, 15, tzinfo=tz)
    database = create_db_engine(tmp_path / "test.db")
    init_schema(database)
    session_factory = create_session_factory(database)
    repository = PortfolioRepository(session_factory)
    watchlist = WatchlistRepository(session_factory, [
        {"symbol": "THYAO", "name": "Türk Hava Yolları"},
        {"symbol": "ASELS", "name": "Aselsan"},
    ])
    watchlist.add("THYAO")
    runtime = EngineRuntime(
        lambda _: None,
        market=market,
        portfolio=repository,
        watchlist=watchlist,
    )

    @asynccontextmanager
    async def factory():
        yield runtime
        await runtime.aclose()

    app = create_app(factory, api_key="secret")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://engine",
            headers={"Authorization": "Bearer secret"},
        ) as client:
            yield client, runtime, market, repository
    database.dispose()


@pytest.mark.parametrize("value", [True, "100", "Infinity"])
@pytest.mark.parametrize("field", ["price", "stop_loss", "target"])
async def test_transaction_price_fields_reject_non_numeric_json(web_client, field, value):
    client, _, _, repository = web_client
    response = await client.post("/v1/web/portfolio/transactions", json={
        "symbol": "THYAO", "kind": "BUY", "quantity": 1, "price": 100, field: value,
    })
    assert response.status_code == 422
    assert repository.get("THYAO") is None
    assert repository.transactions("THYAO") == []


@pytest.mark.parametrize("value", [True, "100", "Infinity"])
@pytest.mark.parametrize("field", ["stop_loss", "target"])
async def test_plan_price_fields_reject_non_numeric_json(web_client, field, value):
    client, _, _, repository = web_client
    original = repository.buy("THYAO", 1, 100, 90, 120)
    response = await client.patch("/v1/web/portfolio/THYAO", json={field: value})
    assert response.status_code == 422
    assert repository.get("THYAO") == original
    assert len(repository.transactions("THYAO")) == 1


async def test_watchlist_returns_placeholders_and_shares_refresh(web_client):
    client, runtime, market, _ = web_client
    first = (await client.get("/v1/web/watchlist")).json()
    assert first["refreshing"] and first["items"][0]["pending"]
    task = runtime.web._refresh_task
    await client.get("/v1/web/watchlist")
    assert runtime.web._refresh_task is task
    await task
    ready = (await client.get("/v1/web/watchlist")).json()
    quote = ready["items"][0]
    assert not ready["refreshing"]
    assert quote["price"] == 200
    assert quote["price_kind"] == "close"
    assert quote["change_pct"] == pytest.approx((200 / 199 - 1) * 100)
    assert quote["as_of"]
    assert quote["daily"]["rsi"] is not None
    market.get_daily.assert_awaited_once()


async def test_watchlist_edits_persist_and_only_catalog_stocks_are_allowed(web_client):
    client, runtime, _, _ = web_client
    repository = runtime.web.watchlist_repository
    stocks = (await client.get("/v1/web/stocklist")).json()["items"]
    assert [stock["symbol"] for stock in stocks] == ["THYAO", "ASELS"]

    response = await client.post("/v1/web/watchlist", json={"symbol": "asels"})
    assert response.status_code == 200
    assert [item["symbol"] for item in response.json()["items"]] == ["THYAO", "ASELS"]
    assert [item["symbol"] for item in repository.list()] == ["THYAO", "ASELS"]
    assert (await client.get("/v1/web/meta")).json()["watchlist_count"] == 2

    await client.post("/v1/web/watchlist", json={"symbol": "ASELS"})
    assert len(repository.list()) == 2
    invalid = await client.post("/v1/web/watchlist", json={"symbol": "UNKNOWN"})
    assert invalid.status_code == 400
    assert "temel listede" in invalid.json()["detail"]
    assert len(repository.list()) == 2

    removed = await client.delete("/v1/web/watchlist/thyao")
    assert removed.status_code == 200
    assert [item["symbol"] for item in removed.json()["items"]] == ["ASELS"]
    assert [item["symbol"] for item in repository.list()] == ["ASELS"]
    assert (await client.get("/v1/web/meta")).json()["watchlist_count"] == 1
    assert (await client.delete("/v1/web/watchlist/THYAO")).status_code == 404


async def test_removing_last_stock_does_not_reseed_on_polling(web_client):
    client, _, _, _ = web_client
    removed = await client.delete("/v1/web/watchlist/THYAO")
    assert removed.status_code == 200
    assert removed.json()["items"] == []
    assert not removed.json()["refreshing"]
    assert (await client.get("/v1/web/watchlist")).json()["items"] == []


async def test_removed_watchlist_stock_keeps_portfolio_and_stock_details(web_client):
    client, _, _, repository = web_client
    repository.buy("THYAO", quantity=10, price=100)
    await client.delete("/v1/web/watchlist/THYAO")

    assert repository.get("THYAO").quantity == 10
    assert (await client.get("/v1/web/symbols/THYAO")).status_code == 200


async def test_watchlist_write_endpoints_require_auth_and_validate_input(web_client):
    client, runtime, _, _ = web_client
    headers = {"Authorization": "wrong"}
    assert (await client.post(
        "/v1/web/watchlist", json={"symbol": "ASELS"}, headers=headers,
    )).status_code == 401
    assert (await client.delete(
        "/v1/web/watchlist/THYAO", headers=headers,
    )).status_code == 401
    for payload in ({"symbol": ""}, {"symbol": "../ASELS"}, {"symbol": "ASELS", "name": "fake"}):
        assert (await client.post("/v1/web/watchlist", json=payload)).status_code == 422
    assert [item["symbol"] for item in runtime.web.watchlist_repository.list()] == ["THYAO"]


async def test_analysis_scan_uses_ui_edits_without_recreating_tools(web_client):
    client, runtime, market, _ = web_client
    tools = AnalysisTools(market, runtime.web.watchlist_repository)
    assert [entry["symbol"] for entry in await tools.scan()] == ["THYAO"]

    await client.post("/v1/web/watchlist", json={"symbol": "ASELS"})
    await client.delete("/v1/web/watchlist/THYAO")

    results = await tools.scan()
    assert [entry["symbol"] for entry in results] == ["ASELS"]
    assert results[0]["name"] == "Aselsan"


async def test_symbol_charts_and_scope_validation(web_client):
    client, _, _, _ = web_client
    details = await client.get("/v1/web/symbols/thyao")
    assert details.status_code == 200
    assert "pivot" in details.json()["levels"]
    daily = (await client.get("/v1/web/symbols/THYAO/bars")).json()
    assert daily["chart_type"] == "candlestick" and len(daily["bars"]) == 100
    intraday = (await client.get("/v1/web/symbols/THYAO/bars?timeframe=M5")).json()
    assert intraday["chart_type"] == "line" and intraday["bars"] == []
    assert (await client.get("/v1/web/symbols/UNKNOWN")).status_code == 404
    assert (await client.get("/v1/web/symbols/THYAO/bars?timeframe=BAD")).status_code == 422


@pytest.mark.parametrize("path", ["symbols/THYAO", "symbols/THYAO/bars"])
@pytest.mark.parametrize(
    "error,status,message",
    [
        (httpx.ReadTimeout(""), 504, "zamanında yanıt vermedi"),
        (httpx.ConnectError(""), 502, "veri alınamadı"),
        (
            httpx.HTTPStatusError(
                "upstream failed",
                request=httpx.Request("GET", "https://provider.test"),
                response=httpx.Response(503),
            ),
            502,
            "veri alınamadı",
        ),
    ],
)
async def test_provider_failures_return_readable_errors(web_client, path, error, status, message):
    client, _, market, _ = web_client
    market.get_daily.side_effect = error
    response = await client.get("/v1/web/" + path)
    assert response.status_code == status
    assert message in response.json()["detail"]
    assert (await client.get("/health")).status_code == 200


async def test_intraday_chart_timeout_returns_readable_error(web_client):
    client, _, market, _ = web_client
    market.get_intraday.side_effect = httpx.ReadTimeout("")
    response = await client.get("/v1/web/symbols/THYAO/bars?timeframe=M5")
    assert response.status_code == 504
    assert "zamanında yanıt vermedi" in response.json()["detail"]


@pytest.mark.parametrize("timeframe,response", [
    ("D1", httpx.Response(200, text="<html>Temporarily unavailable</html>")),
    ("M5", httpx.Response(200, text="<html>Temporarily unavailable</html>")),
    ("D1", httpx.Response(200, json=[])),
    ("D1", httpx.Response(200, json={"ok": False})),
    ("D1", httpx.Response(200, json={"ok": True, "value": {}})),
    ("M5", httpx.Response(200, json={"data": {}})),
    ("M5", httpx.Response(200, json={"data": [["invalid timestamp", 100]]})),
    ("M5", httpx.Response(200, json={"data": [[1791270000000, "invalid price"]]})),
])
async def test_malformed_provider_response_returns_502(web_client, timeframe, response):
    client, runtime, market, _ = web_client
    async with IsYatirimProvider(
        market.calendar, _transport=httpx.MockTransport(lambda request: response),
    ) as provider:
        provider._now = market.now
        runtime.web.market = MarketService(provider, MarketBarCache())
        runtime.web.market.now = market.now
        result = await client.get(
            "/v1/web/symbols/THYAO/bars", params={"timeframe": timeframe},
        )
    assert result.status_code == 502
    assert "veri alınamadı" in result.json()["detail"]
    assert "invalid" not in result.json()["detail"]
    assert (await client.get("/health")).status_code == 200


async def test_watchlist_handles_provider_timeout(web_client):
    client, runtime, market, _ = web_client
    market.get_daily.side_effect = httpx.ReadTimeout("")
    await client.get("/v1/web/watchlist")
    await runtime.web._refresh_task
    response = await client.get("/v1/web/watchlist")
    assert response.status_code == 200
    quote = response.json()["items"][0]
    assert not quote["pending"]
    assert quote["error"]
    assert "price" not in quote


async def test_transaction_plan_partial_sale_and_close_preserve_history(web_client):
    client, _, _, repository = web_client
    base = "/v1/web/portfolio"
    buy = await client.post(
        base + "/transactions",
        json={
            "symbol": "THYAO",
            "kind": "BUY",
            "quantity": 10,
            "price": 100,
            "stop_loss": 90,
            "target": 220,
            "note": "deneme",
        },
    )
    assert buy.status_code == 200
    plan = await client.patch(base + "/THYAO", json={"stop_loss": 95, "note": "yeni"})
    assert plan.status_code == 200
    assert plan.json()["position"]["target"] == 220
    snapshot = (await client.get(base)).json()
    assert snapshot["summary"]["market_value"] == 2000
    assert snapshot["summary"]["unrealized_pnl"] == 1000
    assert snapshot["positions"][0]["price_kind"] == "close"
    oversell = await client.post(
        base + "/transactions",
        json={
            "symbol": "THYAO",
            "kind": "SELL",
            "quantity": 11,
            "price": 120,
        },
    )
    assert oversell.status_code == 400
    assert repository.get("THYAO").quantity == 10
    for quantity in (4, 6):
        sell = await client.post(
            base + "/transactions",
            json={
                "symbol": "THYAO",
                "kind": "SELL",
                "quantity": quantity,
                "price": 120,
            },
        )
        assert sell.status_code == 200
    assert sell.json()["closed"]
    assert (await client.get(base)).json()["positions"] == []
    closed = (await client.get(base + "/closed")).json()["closed"]
    assert closed[0]["realized_pnl"] == 200
    assert len((await client.get(base + "/THYAO/transactions")).json()["transactions"]) == 3


async def test_missing_prices_do_not_produce_misleading_totals(web_client):
    client, _, market, repository = web_client
    repository.buy("THYAO", 10, 100)
    market.get_daily.side_effect = RuntimeError("Provider unavailable")
    portfolio = (await client.get("/v1/web/portfolio")).json()
    assert portfolio["summary"]["missing_prices"] == 1
    assert portfolio["summary"]["market_value"] is None
    assert portfolio["summary"]["unrealized_pnl"] is None
    assert portfolio["positions"][0]["price_kind"] == "unavailable"


async def test_delayed_price_has_source_timestamp(web_client):
    client, _, market, repository = web_client
    moment = datetime(2026, 10, 6, 15, tzinfo=ZoneInfo("Europe/Istanbul"))
    market.get_today.return_value = SessionSnapshot(
        symbol="THYAO",
        ohlcv=OHLCV(moment.replace(hour=10), 200, 205, 198, 202, None),
        as_of=moment,
    )
    repository.buy("THYAO", 5, 100)
    portfolio = (await client.get("/v1/web/portfolio")).json()
    assert portfolio["summary"]["market_value"] == 1010
    assert portfolio["positions"][0]["as_of"] == moment.isoformat()
    assert portfolio["positions"][0]["price_kind"] == "delayed"
    quote = (await client.get("/v1/web/symbols/THYAO")).json()
    assert quote["price"] == 202
    assert quote["as_of"] == moment.isoformat()
    assert quote["price_kind"] == "delayed"


@pytest.mark.parametrize("field,value", [("quantity", 0), ("quantity", 1.5), ("price", -1)])
async def test_invalid_transactions_do_not_write(web_client, field, value):
    client, _, _, repository = web_client
    payload = {"symbol": "THYAO", "kind": "BUY", "quantity": 1, "price": 100, field: value}
    assert (await client.post("/v1/web/portfolio/transactions", json=payload)).status_code == 422
    assert repository.list() == []


async def test_web_endpoints_require_engine_auth_and_news_is_read_only(web_client):
    client, _, _, _ = web_client
    for path in ("meta", "stocklist", "watchlist", "portfolio", "news", "symbols/THYAO"):
        assert (
            await client.get("/v1/web/" + path, headers={"Authorization": "wrong"})
        ).status_code == 401
    assert (await client.get("/v1/web/news")).json() == {"digests": []}
    assert (await client.get("/v1/web/news?days=100")).status_code == 422


@pytest.mark.parametrize("at,kind,opened,hours", [
    ("2026-10-28T11:00", "half_day", True, ("10:00", "12:30")),
    ("2026-10-28T12:30", "half_day", False, ("10:00", "12:30")),
    ("2026-10-29T11:00", "closed", False, (None, None)),
    ("2026-10-31T11:00", "closed", False, (None, None)),
    ("2027-01-01T11:00", "closed", False, (None, None)),
    ("2027-03-08T11:00", "half_day", True, ("10:00", "12:30")),
    ("2028-01-03T11:00", "unknown", None, (None, None)),
])
async def test_metadata_uses_effective_calendar(web_client, bist_calendar, at, kind, opened, hours):
    client, _, market, _ = web_client
    market.calendar = bist_calendar
    market.now = lambda: datetime.fromisoformat(at).replace(tzinfo=bist_calendar.timezone)
    meta = (await client.get("/v1/web/meta")).json()
    assert meta["day_kind"] == kind and meta["scheduled_open"] is opened
    assert (meta["session_start"], meta["session_end"]) == hours
    assert meta["reason"]
    if kind == "half_day":
        assert meta["daily_close"] == "12:40"
        expected_next = "2027-03-12T10:00" if at.startswith("2027") else "2026-10-30T10:00"
        assert meta["next_open"].startswith(expected_next)


async def test_quote_cache_refreshes_at_early_close_without_waiting_300_seconds(web_client, bist_calendar):
    client, runtime, market, _ = web_client
    market.calendar = bist_calendar
    at = datetime(2026, 10, 28, 12, 29, tzinfo=bist_calendar.timezone)
    market.now = lambda: at
    await client.get("/v1/web/watchlist")
    first = runtime.web._refresh_task
    await first
    at = at.replace(minute=30)
    response = await client.get("/v1/web/watchlist")
    second = runtime.web._refresh_task
    assert second is not first and response.json()["items"][0]["pending"]
    await second
    assert market.get_daily.await_count == 2


async def test_snapshot_remains_priced_after_close_but_live_pulse_is_absent(web_client):
    client, _, market, repository = web_client
    tz = market.calendar.timezone
    at = datetime(2026, 10, 6, 18, 20, tzinfo=tz)
    market.now = lambda: at
    market.get_today.return_value = SessionSnapshot("THYAO", OHLCV(at.replace(hour=10), 200, 205, 198, 202, None), at.replace(hour=17, minute=59))
    repository.buy("THYAO", 5, 100)
    quote = (await client.get("/v1/web/symbols/THYAO")).json()
    portfolio = (await client.get("/v1/web/portfolio")).json()
    agent_portfolio = await PortfolioTools(repository, market).list_portfolio()
    assert quote["price"] == portfolio["positions"][0]["current_price"] == 202
    assert quote["as_of"] == portfolio["positions"][0]["as_of"]
    assert quote["price_kind"] == "delayed" and quote["pulse"] is None
    assert portfolio["positions"] == agent_portfolio["positions"]
    assert "error" in (await AnalysisTools(market, SimpleNamespace(list=lambda: [], stocklist=lambda: [])).get_pulse(["THYAO"]))[0]


async def test_uncovered_intraday_is_503_and_daily_is_available(web_client):
    from trader_agent.engine.calendar import CalendarCoverageError
    client, _, market, _ = web_client
    market.get_intraday.side_effect = CalendarCoverageError("Takvim bu tarihi kapsamıyor: 2028-01-03")
    response = await client.get("/v1/web/symbols/THYAO/bars?timeframe=M5")
    assert response.status_code == 503
    assert "Takvim" in response.json()["detail"]
    assert (await client.get("/v1/web/symbols/THYAO/bars?timeframe=D1")).status_code == 200
