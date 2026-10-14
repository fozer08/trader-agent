from uuid import uuid4
from contextlib import asynccontextmanager
from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest

from trader_agent.engine.api import create_app
from trader_agent.engine.web import WebService
from trader_agent.engine.recommendations import RecommendationService
from trader_agent.engine.repository.recommendations import RecommendationRepository
from trader_agent.engine.repository.preferences import TradingPreferencesRepository
from trader_agent.engine.repository import (
    PortfolioRepository,
    WatchlistRepository,
    create_db_engine,
    create_session_factory,
    init_schema,
)
from trader_agent.engine.runtime import EngineRuntime
from trader_agent.engine.analysis.service import AnalysisService
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
        analysis=AnalysisService(market),
        portfolio=repository,
        watchlist=watchlist,
        web_factory=lambda user: WebService(
            market, PortfolioRepository(session_factory, user), None,
            WatchlistRepository(session_factory, watchlist.stocklist(), user),
            AnalysisService(market), RecommendationService(RecommendationRepository(session_factory, user),
                calendar, lambda symbol: watchlist.stock(symbol)["symbol"], lambda: market.now()),
            TradingPreferencesRepository(session_factory, user),
        ),
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
        "symbol": "THYAO", "kind": "BUY", "operation_id": str(uuid4()), "quantity": 1, "price": 100, field: value,
     "horizon": "swing"})
    assert response.status_code == 422
    assert repository.get("THYAO") is None
    assert repository.list() == []


async def test_position_horizon_is_shared_by_web_and_tools(web_client):
    client, runtime, _, repository = web_client
    payload = {"symbol": "THYAO", "kind": "BUY", "operation_id": str(uuid4()), "quantity": 10, "price": 100}
    response = await client.post("/v1/web/portfolio/transactions", json=payload)
    assert response.status_code == 400
    assert repository.list() == []
    response = await client.post("/v1/web/portfolio/transactions", json={**payload, "horizon": "position"})
    position_id = response.json()["position"]["id"]
    assert response.json()["position"]["horizon"] == "position"
    changed = await client.patch(f"/v1/web/portfolio/{position_id}", json={"horizon": "intraday"})
    assert changed.json()["position"]["horizon"] == "intraday"
    api_positions = (await client.get("/v1/web/portfolio")).json()["positions"]
    tool_positions = (await runtime.web.portfolio_tools.list_portfolio())["positions"]
    assert api_positions == tool_positions
    assert api_positions[0]["horizon"] == "intraday"
    assert len(repository.transactions(repository.list()[0].id)) == 1
    await client.post("/v1/web/portfolio/transactions", json=payload)
    assert repository.get(position_id).horizon == "intraday"


@pytest.mark.parametrize("value", ["forever", True, 42])
async def test_api_rejects_invalid_horizon_before_portfolio_write(web_client, value):
    client, _, _, repository = web_client
    response = await client.post("/v1/web/portfolio/transactions", json={
        "symbol": "THYAO", "kind": "BUY", "operation_id": str(uuid4()), "quantity": 1, "price": 100, "horizon": value,
    })
    assert response.status_code == 422
    assert repository.list() == []


@pytest.mark.parametrize("value", [True, "100", "Infinity"])
@pytest.mark.parametrize("field", ["stop_loss", "target"])
async def test_plan_price_fields_reject_non_numeric_json(web_client, field, value):
    client, _, _, repository = web_client
    original = repository.buy("THYAO", 1, 100, 90, 120, horizon="swing")
    response = await client.patch(f"/v1/web/portfolio/{original.id}", json={field: value})
    assert response.status_code == 422
    assert repository.get(original.id) == original
    assert len(repository.transactions(repository.list()[0].id)) == 1


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
    repository.buy("THYAO", quantity=10, price=100, horizon="swing")
    await client.delete("/v1/web/watchlist/THYAO")

    assert repository.list()[0].quantity == 10
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
    tools = AnalysisTools(runtime.web.analysis, runtime.web.watchlist_repository)
    assert [entry["symbol"] for entry in await tools.scan("swing")] == ["THYAO"]

    await client.post("/v1/web/watchlist", json={"symbol": "ASELS"})
    await client.delete("/v1/web/watchlist/THYAO")

    results = await tools.scan("swing")
    assert [entry["symbol"] for entry in results] == ["ASELS"]
    assert results[0]["name"] == "Aselsan"


async def test_symbol_charts_and_scope_validation(web_client):
    client, _, _, _ = web_client
    details = await client.get("/v1/web/symbols/thyao")
    assert details.status_code == 200
    assert "rolling_20_high" in details.json()["daily"]["levels"]
    assert details.json()["horizon"] == "swing"
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
            "kind": "BUY", "operation_id": str(uuid4()),
            "quantity": 10,
            "price": 100,
            "stop_loss": 90,
            "target": 220,
            "note": "deneme",
         "horizon": "swing"},
    )
    assert buy.status_code == 200
    position_id = buy.json()["position"]["id"]
    plan = await client.patch(base + f"/{position_id}", json={"stop_loss": 95, "note": "yeni"})
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
            "kind": "SELL", "operation_id": str(uuid4()), "position_id": position_id,
            "quantity": 11,
            "price": 120,
        },
    )
    assert oversell.status_code == 400
    assert repository.list()[0].quantity == 10
    for quantity in (4, 6):
        sell = await client.post(
            base + "/transactions",
            json={
                "symbol": "THYAO",
                "kind": "SELL", "operation_id": str(uuid4()), "position_id": position_id,
                "quantity": quantity,
                "price": 120,
            },
        )
        assert sell.status_code == 200
    assert sell.json()["closed"]
    assert (await client.get(base)).json()["positions"] == []
    closed = (await client.get(base + "/closed")).json()["closed"]
    assert closed[0]["realized_pnl"] == 200
    assert len((await client.get(base + f"/{position_id}/transactions")).json()["transactions"]) == 3


async def test_missing_prices_do_not_produce_misleading_totals(web_client):
    client, _, market, repository = web_client
    repository.buy("THYAO", 10, 100, horizon="swing")
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
    repository.buy("THYAO", 5, 100, horizon="swing")
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
    payload = {"symbol": "THYAO", "kind": "BUY", "operation_id": str(uuid4()), "quantity": 1, "price": 100, field: value, "horizon": "swing"}
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


async def test_snapshot_remains_priced_after_close_and_session_is_marked_closed(web_client):
    client, _, market, repository = web_client
    tz = market.calendar.timezone
    at = datetime(2026, 10, 6, 18, 20, tzinfo=tz)
    market.now = lambda: at
    market.get_today.return_value = SessionSnapshot("THYAO", OHLCV(at.replace(hour=10), 200, 205, 198, 202, None), at.replace(hour=17, minute=59))
    repository.buy("THYAO", 5, 100, horizon="swing")
    quote = (await client.get("/v1/web/symbols/THYAO")).json()
    portfolio = (await client.get("/v1/web/portfolio")).json()
    agent_portfolio = await PortfolioTools(repository, market).list_portfolio()
    assert quote["price"] == portfolio["positions"][0]["current_price"] == 202
    assert quote["as_of"] == portfolio["positions"][0]["as_of"]
    assert quote["price_kind"] == "delayed" and quote["session_open"] is False
    assert quote["session"]["price"] == 202
    assert portfolio["positions"] == agent_portfolio["positions"]
    assert portfolio["summary"] == agent_portfolio["summary"]


async def test_uncovered_intraday_is_503_and_daily_is_available(web_client):
    from trader_agent.engine.calendar import CalendarCoverageError
    client, _, market, _ = web_client
    market.get_intraday.side_effect = CalendarCoverageError("Takvim bu tarihi kapsamıyor: 2028-01-03")
    response = await client.get("/v1/web/symbols/THYAO/bars?timeframe=M5")
    assert response.status_code == 503
    assert "Takvim" in response.json()["detail"]
    assert (await client.get("/v1/web/symbols/THYAO/bars?timeframe=D1")).status_code == 200


@pytest.mark.parametrize("horizon,period", [("intraday", 105), ("swing", 250), ("position", 300)])
async def test_web_horizon_matches_agent_analysis_without_intraday_indicators(web_client, horizon, period):
    client, runtime, market, _ = web_client
    response = await client.get(f"/v1/web/symbols/THYAO?horizon={horizon}")
    assert response.status_code == 200
    tools = AnalysisTools(runtime.web.analysis, runtime.web.watchlist_repository)
    agent = (await tools.analyze(["THYAO"], horizon))[0]
    assert response.json()["daily"] == agent["daily"]
    assert response.json()["horizon"] == horizon
    assert all(call.args[1] == period for call in market.get_daily.await_args_list)
    market.get_intraday.assert_not_called()


async def test_unknown_horizon_is_rejected_before_market_io(web_client):
    client, _, market, _ = web_client
    assert (await client.get("/v1/web/symbols/THYAO?horizon=forever")).status_code == 422
    market.get_daily.assert_not_called()


async def test_missing_ohlc_uses_line_chart_not_fabricated_candles(web_client):
    from dataclasses import replace
    client, _, market, _ = web_client
    data = market.get_daily.return_value
    data.bars[-1] = replace(data.bars[-1], ohlc_kind="close_only")
    response = await client.get("/v1/web/symbols/THYAO/bars")
    assert response.json()["chart_type"] == "line"
    assert response.json()["bars"][-1]["ohlc_kind"] == "close_only"


async def test_authenticated_user_scope_reaches_web_and_agent_tools(web_client):
    client, runtime, _, _ = web_client
    alice = "9e0315b6-fc2e-43de-9e3b-1c87c0e378c4"
    bob = "4a686eca-98f0-426d-a7e9-47b08d83d2f9"
    headers = {"X-User-Id": alice}
    assert (await client.put("/v1/web/preferences", headers=headers, json={
        "horizon": "position", "risk_level": "low",
    })).status_code == 200
    assert (await client.get("/v1/web/preferences", headers={"X-User-Id": bob})).json() == {
        "horizon": None, "risk_level": None,
    }
    assert (await client.post("/v1/web/watchlist", headers=headers, json={"symbol": "THYAO"})).status_code == 200
    assert (await client.get("/v1/web/watchlist", headers={"X-User-Id": bob})).json()["items"] == []
    assert (await client.post("/v1/web/portfolio/transactions", headers=headers, json={
        "symbol": "THYAO", "kind": "BUY", "operation_id": str(uuid4()), "quantity": 2, "price": 100, "horizon": "position",
    })).status_code == 200
    bob_web = await runtime.web_for(bob)
    assert (await bob_web.portfolio_tools.list_portfolio())["positions"] == []
    assert (await client.get("/v1/web/portfolio/THYAO/transactions", headers={"X-User-Id": bob})).status_code == 400
    result = await client.get("/v1/web/portfolio", headers={"X-User-Id": "../../other"})
    assert result.status_code == 400
    result = await client.get("/v1/web/portfolio", headers={"X-User-Id": alice, "Authorization": "Bearer wrong"})
    assert result.status_code == 401


async def test_recommendation_to_portfolio_and_performance_flow(web_client):
    client, runtime, market, repository = web_client
    repository._now = market.now
    body = dict(symbol="THYAO", entry_min=100, entry_max=105, stop_loss=95, target=120,
                horizon="swing", risk_level="low", rationale="Günlük plan",
                producer_version="test-rule-v1", data_as_of="2026-10-06T14:45:00+03:00")
    published = await client.post("/v1/web/recommendations", json=body)
    assert published.status_code == 200, published.text
    r = published.json()
    assert r["expires_at"] == "2026-10-06T21:00:00+00:00"
    assert (await client.post("/v1/web/recommendations", json=body)).json()["id"] == r["id"]
    assert len((await client.get("/v1/web/recommendations")).json()["items"]) == 1
    assert (await client.get("/v1/web/portfolio/performance")).json()["closed_count"] == 0
    endpoint = f'/v1/web/recommendations/{r["id"]}/accept'
    for price in (99.99, 105.01):
        response = await client.post(endpoint, json=dict(quantity=10, price=price, operation_id=str(uuid4())))
        assert response.status_code == 400
        assert "aralığında" in response.json()["detail"]
    assert repository.list() == []
    payload = dict(quantity=10, price=102, operation_id=str(uuid4()))
    opened = await client.post(endpoint, json=payload)
    p = opened.json()["position"]
    assert p["source"] == "system" and p["producer"] == "model"
    assert (await client.post(endpoint, json=payload)).json()["position"]["id"] == p["id"]
    assert len(repository.transactions(p["id"])) == 1
    assert (await client.post(endpoint, json={**payload, "source": "manual"})).status_code == 422
    assert (await client.get(f'/v1/web/recommendations/{r["id"]}')).json()["position_id"] == p["id"]
    manual = await client.post('/v1/web/portfolio/transactions', json=dict(symbol='THYAO', kind='BUY', quantity=3, price=90, horizon='position', operation_id=str(uuid4())))
    assert manual.status_code == 200
    assert len((await client.get('/v1/web/portfolio')).json()['positions']) == 2
    assert len((await client.get('/v1/web/portfolio?source=manual')).json()['positions']) == 1
    for qty in (4, 6):
        close = await client.post('/v1/web/portfolio/transactions', json=dict(symbol='THYAO', kind='SELL', position_id=p['id'], quantity=qty, price=110, operation_id=str(uuid4())))
        assert close.status_code == 200, close.text
    perf = (await client.get('/v1/web/portfolio/performance?source=system')).json()
    assert perf['realized_pnl'] == 80 and perf['wins'] == 1 and perf['closed_count'] == 1
    assert perf['current']['market_value'] == 0
    assert (await client.get('/v1/web/portfolio/performance?source=manual')).json()['closed_count'] == 0
    market.now = lambda: datetime(2026, 10, 7, 0, tzinfo=ZoneInfo('Europe/Istanbul'))
    assert (await client.get('/v1/web/recommendations')).json()['items'] == []
    old = (await client.get(f'/v1/web/recommendations/{r["id"]}')).json()
    assert not old['active'] and old['position_id'] == p['id']
    assert (await client.get(f'/v1/web/portfolio/{p["id"]}')).json()['initial_plan']['entry_min'] == 100


async def test_recommendation_expiry_and_user_scope_are_checked_on_write(web_client):
    client, runtime, market, _ = web_client
    alice, bob = str(uuid4()), str(uuid4())
    headers = {'X-User-Id': alice}
    web = await runtime.web_for(alice)
    web.repository._now = lambda: market.now()
    body = dict(symbol='THYAO', entry_min=100, entry_max=105, stop_loss=95, target=120,
                horizon='intraday', rationale='Seans planı', producer_version='test-v1',
                data_as_of='2026-10-06T14:45:00+03:00')
    r = (await client.post('/v1/web/recommendations', headers=headers, json=body)).json()
    assert r['expires_at'] == '2026-10-06T15:00:00+00:00'
    assert (await client.get('/v1/web/recommendations', headers={'X-User-Id': bob})).json()['items'] == []
    assert (await client.get(f'/v1/web/recommendations/{r["id"]}', headers={'X-User-Id': bob})).status_code == 404
    payload = dict(quantity=10, price=102, operation_id=str(uuid4()))
    assert (await client.post(f'/v1/web/recommendations/{r["id"]}/accept', headers={'X-User-Id': bob}, json=payload)).status_code == 400
    market.now = lambda: datetime(2026, 10, 6, 18, tzinfo=ZoneInfo('Europe/Istanbul'))
    expired = await client.post(f'/v1/web/recommendations/{r["id"]}/accept', headers=headers, json=payload)
    assert expired.status_code == 400 and 'süresi' in expired.json()['detail']
    assert web.repository.list() == []


async def test_invalid_recommendations_and_preview(web_client):
    client, _, _, _ = web_client
    base = dict(symbol='THYAO', entry_min=100, entry_max=105, stop_loss=95, target=120,
                horizon='swing', rationale='Plan', producer_version='test',
                data_as_of='2026-10-06T14:45:00+03:00')
    for update in [dict(entry_max=90), dict(stop_loss=102), dict(target=101), dict(symbol='UNKNOWN'), dict(data_as_of='2027-01-01T00:00:00Z')]:
        response = await client.post('/v1/web/recommendations', json={**base, **update})
        assert response.status_code in (400, 404)
    assert (await client.get('/v1/web/recommendations')).json()['items'] == []
    preview = (await client.post('/v1/web/portfolio/preview', json=dict(quantity=10, price=102, stop_loss=95, target=120))).json()
    assert preview == dict(amount=1020, planned_loss=70, planned_gain=180, realized_pnl=None)
