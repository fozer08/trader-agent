from __future__ import annotations

import json
from dataclasses import asdict
from contextlib import aclosing, asynccontextmanager
from datetime import datetime, timezone
from secrets import compare_digest
from typing import Callable, Literal

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .runtime import EngineRuntime, open_runtime
from .news.types import NewsError
from .web import WebService
from .marketdata.base import ProviderError, ProviderTimeoutError
from .calendar import CalendarCoverageError


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    output_format: Literal["plain", "markdown"] = "plain"


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)


class DigestRequest(BaseModel):
    since: datetime | None = None


class TransactionRequest(BaseModel):
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    kind: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, strict=True)
    price: float = Field(gt=0, allow_inf_nan=False, strict=True)
    stop_loss: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    target: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    note: str | None = Field(default=None, max_length=400)


class PlanRequest(BaseModel):
    stop_loss: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    target: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    note: str | None = Field(default=None, max_length=400)


class WatchlistRequest(BaseModel):
    model_config = {"extra": "forbid"}
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")


def create_app(runtime_factory: Callable = open_runtime, *, api_key: str = "") -> FastAPI:
    key = api_key
    expected_authorization = f"Bearer {key}".encode("utf-8")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with runtime_factory() as runtime:
            app.state.runtime = runtime
            try:
                yield
            finally:
                app.state.runtime = None

    async def authorize(authorization: str | None = Header(default=None)) -> None:
        if key and not compare_digest((authorization or "").encode("utf-8"), expected_authorization):
            raise HTTPException(status_code=401, detail="Unauthorized")

    def get_runtime(request: Request) -> EngineRuntime:
        runtime = request.app.state.runtime
        if runtime is None:
            raise HTTPException(status_code=503, detail="Engine not ready")
        return runtime

    app = FastAPI(title="trader-agent engine", lifespan=lifespan)
    app.state.runtime = None
    protected = [Depends(authorize)]

    def get_web(runtime: EngineRuntime = Depends(get_runtime)) -> WebService:
        if runtime.web is None:
            raise HTTPException(status_code=503, detail="Web servisleri hazır değil.")
        return runtime.web

    def result_or_error(result: dict) -> dict:
        if "error" in result:
            raise HTTPException(status_code=400, detail=result["error"])
        return result

    @app.exception_handler(NewsError)
    async def news_unavailable(request: Request, exc: NewsError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.exception_handler(KeyError)
    async def not_found(request: Request, exc: KeyError):
        return JSONResponse(status_code=404, content={"detail": str(exc.args[0])})

    @app.exception_handler(ValueError)
    async def invalid_value(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(CalendarCoverageError)
    async def missing_calendar(request: Request, exc: CalendarCoverageError):
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @app.exception_handler(httpx.TimeoutException)
    @app.exception_handler(ProviderTimeoutError)
    async def provider_timeout(request: Request, exc: httpx.TimeoutException | ProviderTimeoutError):
        return JSONResponse(
            status_code=504,
            content={
                "detail": "Veri sağlayıcısı zamanında yanıt vermedi. Lütfen tekrar deneyin."
            },
        )

    @app.exception_handler(ProviderError)
    @app.exception_handler(httpx.HTTPError)
    async def provider_unavailable(request: Request, exc: httpx.HTTPError | ProviderError):
        return JSONResponse(
            status_code=502,
            content={"detail": "Veri sağlayıcısından veri alınamadı. Lütfen tekrar deneyin."},
        )

    @app.get("/v1/web/meta", dependencies=protected)
    async def web_meta(web: WebService = Depends(get_web)) -> dict:
        return web.metadata()

    @app.get("/v1/web/stocklist", dependencies=protected)
    async def web_stocklist(web: WebService = Depends(get_web)) -> dict:
        return web.stocklist_view()

    @app.get("/v1/web/watchlist", dependencies=protected)
    async def web_watchlist(web: WebService = Depends(get_web)) -> dict:
        return await web.watchlist_view()

    @app.post("/v1/web/watchlist", dependencies=protected)
    async def web_watchlist_add(req: WatchlistRequest, web: WebService = Depends(get_web)) -> dict:
        return await web.add_to_watchlist(req.symbol)

    @app.delete("/v1/web/watchlist/{symbol}", dependencies=protected)
    async def web_watchlist_remove(symbol: str, web: WebService = Depends(get_web)) -> dict:
        return await web.remove_from_watchlist(symbol)

    @app.get("/v1/web/symbols/{symbol}", dependencies=protected)
    async def web_symbol(symbol: str, web: WebService = Depends(get_web)) -> dict:
        return await web.symbol_view(symbol)

    @app.get("/v1/web/symbols/{symbol}/bars", dependencies=protected)
    async def web_bars(symbol: str, timeframe: Literal["D1", "M5", "M15"] = "D1",
                       web: WebService = Depends(get_web)) -> dict:
        return await web.bars_view(symbol, timeframe)

    @app.get("/v1/web/portfolio", dependencies=protected)
    async def web_portfolio(web: WebService = Depends(get_web)) -> dict:
        return await web.portfolio_view()

    @app.get("/v1/web/portfolio/closed", dependencies=protected)
    async def web_closed(web: WebService = Depends(get_web)) -> dict:
        return result_or_error(await web.portfolio_tools.list_closed())

    @app.get("/v1/web/portfolio/{symbol}/transactions", dependencies=protected)
    async def web_transactions(symbol: str, web: WebService = Depends(get_web)) -> dict:
        return result_or_error(await web.portfolio_tools.get_position_tx(symbol))

    @app.post("/v1/web/portfolio/transactions", dependencies=protected)
    async def web_transaction(req: TransactionRequest, web: WebService = Depends(get_web)) -> dict:
        if req.kind == "BUY":
            symbol = web.require_symbol(req.symbol)
            result = await web.portfolio_tools.buy_position(
                symbol, req.quantity, req.price, req.stop_loss, req.target, req.note,
            )
        else:
            result = await web.portfolio_tools.sell_position(req.symbol, req.quantity, req.price)
        return result_or_error(result)

    @app.patch("/v1/web/portfolio/{symbol}", dependencies=protected)
    async def web_plan(symbol: str, req: PlanRequest, web: WebService = Depends(get_web)) -> dict:
        return result_or_error(await web.portfolio_tools.set_levels(
            symbol, req.stop_loss, req.target, req.note,
        ))

    @app.get("/v1/web/news", dependencies=protected)
    async def web_news(symbol: str | None = None, days: int = Query(default=7, ge=1, le=14),
                       web: WebService = Depends(get_web)) -> dict:
        return await web.news_view(symbol, days)

    @app.get("/health")
    async def health(runtime: EngineRuntime = Depends(get_runtime)) -> dict:
        return {"status": "ok"}

    @app.post("/v1/chat", dependencies=protected)
    async def chat(req: ChatRequest, runtime: EngineRuntime = Depends(get_runtime)):
        async def events():
            async with aclosing(
                runtime.chat(req.session_id, req.message, req.output_format)
            ) as stream:
                async for event in stream:
                    yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/v1/reset", dependencies=protected)
    async def reset(req: ResetRequest, runtime: EngineRuntime = Depends(get_runtime)) -> dict:
        await runtime.reset(req.session_id)
        return {"ok": True}

    @app.get("/v1/status", dependencies=protected)
    async def status(runtime: EngineRuntime = Depends(get_runtime)) -> dict:
        return runtime.status()

    @app.get("/v1/debug/state", dependencies=protected)
    async def memory(session_id: str, runtime: EngineRuntime = Depends(get_runtime)) -> dict:
        try:
            return await runtime.memory(session_id)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")

    @app.post("/v1/jobs/prefetch-market", dependencies=protected)
    async def prefetch(runtime: EngineRuntime = Depends(get_runtime)) -> dict:
        return await runtime.prefetch_market()

    @app.post("/v1/jobs/daily-digest", dependencies=protected)
    async def digest(req: DigestRequest, runtime: EngineRuntime = Depends(get_runtime)) -> dict:
        since = req.since
        if since is not None:
            since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since
        return asdict(await runtime.daily_digest(since))

    return app
