from __future__ import annotations

import json
import asyncio
from dataclasses import asdict
from contextlib import aclosing, asynccontextmanager
from datetime import datetime, timezone
from secrets import compare_digest
from typing import Callable, Literal
from uuid import UUID

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from .analysis import Horizon, RiskLevel
from .positions import Source, preview_position
from .tools.recommendations import RecommendationInput
from .tools.portfolio import _position_to_dict
from .agent.preferences import ConversationPreferences
from .runtime import EngineRuntime, open_runtime
from .news.types import NewsError
from .web import WebService
from .marketdata.base import ProviderError, ProviderTimeoutError
from .calendar import CalendarCoverageError


class PreferencesRequest(BaseModel):
    """Arayüzden gelen tur tercihlerini doğrular; null belirtilmemiş seçimi temsil eder."""

    model_config = {"extra": "forbid"}
    horizon: Horizon | None = None
    risk_level: RiskLevel | None = None


class ChatRequest(BaseModel):
    """Mesaj ve gönderim anındaki tercihleri aynı HTTP isteğinde taşır."""

    session_id: str = Field(min_length=1, max_length=200)
    message: str = Field(min_length=1)
    output_format: Literal["plain", "markdown"] = "plain"
    preferences: PreferencesRequest | None = None


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=200)


class DigestRequest(BaseModel):
    since: datetime | None = None


class TransactionRequest(BaseModel):
    """Gerçekleşmiş işlem girdisini ve yeni pozisyonun planını HTTP sınırında doğrular."""

    model_config = {"extra": "forbid"}
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9]+$")
    position_id: str | None = None
    operation_id: str = Field(min_length=1, max_length=100)
    opened_at: datetime | None = None
    kind: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0, strict=True)
    price: float = Field(gt=0, allow_inf_nan=False, strict=True)
    stop_loss: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    target: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    note: str | None = Field(default=None, max_length=400)
    horizon: Horizon | None = None


class AcceptRecommendationRequest(BaseModel):
    """Karttan gelen miktar ve fiyat; kaynak ve plan istemciye bırakılmaz."""
    model_config = {"extra": "forbid"}
    quantity: int = Field(gt=0, strict=True)
    price: float = Field(gt=0, allow_inf_nan=False, strict=True)
    operation_id: str = Field(min_length=1, max_length=100)


class ModelRecommendationRequest(RecommendationInput):
    """Servis kimliğiyle çalışan bağımsız üreticinin sürümlü plan girdisi."""
    producer_version: str = Field(min_length=1, max_length=200)


class PreviewRequest(BaseModel):
    """Kayıttan önce miktar ve fiyatın deterministik tutar önizlemesi."""
    model_config = {"extra": "forbid"}
    quantity: int = Field(gt=0, strict=True)
    price: float = Field(gt=0, allow_inf_nan=False, strict=True)
    stop_loss: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    target: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    avg_cost: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class PlanRequest(BaseModel):
    """Pozisyonun fiyat seviyeleri, vadesi ve notu için kısmi güncelleme girdisidir."""
    model_config = {"extra": "forbid"}

    stop_loss: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    target: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    note: str | None = Field(default=None, max_length=400)
    horizon: Horizon | None = None


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

    def user_identity(x_user_id: str | None = Header(default=None)) -> str | None:
        if x_user_id is None:
            return None
        if not key:
            raise HTTPException(403, "Kullanıcı erişimi için engine anahtarı gerekli.")
        try:
            return str(UUID(x_user_id))
        except ValueError:
            raise HTTPException(400, "Geçersiz kullanıcı kimliği.") from None

    async def get_web(runtime: EngineRuntime = Depends(get_runtime),
                      user_id: str | None = Depends(user_identity)) -> WebService:
        if user_id:
            return await runtime.web_for(user_id)
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

    @app.get("/v1/web/preferences", dependencies=protected)
    def preferences(web: WebService = Depends(get_web)) -> dict:
        if web.preferences is None:
            raise HTTPException(400, "Kullanıcı profili gerekli.")
        return asdict(web.preferences.get())

    @app.put("/v1/web/preferences", dependencies=protected)
    def save_preferences(req: PreferencesRequest, web: WebService = Depends(get_web)) -> dict:
        if web.preferences is None:
            raise HTTPException(400, "Kullanıcı profili gerekli.")
        return asdict(web.preferences.save(ConversationPreferences(req.horizon, req.risk_level)))

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
    async def web_symbol(
        symbol: str, horizon: Horizon = "swing", web: WebService = Depends(get_web),
    ) -> dict:
        return await web.symbol_view(symbol, horizon)

    @app.get("/v1/web/symbols/{symbol}/bars", dependencies=protected)
    async def web_bars(symbol: str, timeframe: Literal["D1", "M5", "M15"] = "D1",
                       web: WebService = Depends(get_web)) -> dict:
        return await web.bars_view(symbol, timeframe)

    @app.get("/v1/web/recommendations", dependencies=protected)
    async def web_recommendations(web: WebService = Depends(get_web)) -> dict:
        items = await asyncio.to_thread(web.recommendations.repository.list, web.recommendations.now())
        return {"items": [r.to_dict() for r in items]}

    @app.get("/v1/web/recommendations/{recommendation_id}", dependencies=protected)
    async def web_recommendation(recommendation_id: str, web: WebService = Depends(get_web)) -> dict:
        item = await asyncio.to_thread(web.recommendations.repository.get, recommendation_id,
                                      web.recommendations.now())
        return item.to_dict()

    @app.post("/v1/web/recommendations", dependencies=protected)
    async def publish_model_recommendation(req: ModelRecommendationRequest,
                                          web: WebService = Depends(get_web)) -> dict:
        item = await web.recommendations.publish(**req.model_dump(), producer="model")
        return item.to_dict()

    @app.post("/v1/web/recommendations/{recommendation_id}/accept", dependencies=protected)
    async def web_accept_recommendation(recommendation_id: str, req: AcceptRecommendationRequest,
                                       web: WebService = Depends(get_web)) -> dict:
        return result_or_error(await web.portfolio_tools.accept_recommendation(
            recommendation_id, req.quantity, req.price, req.operation_id))

    @app.get("/v1/web/portfolio", dependencies=protected)
    async def web_portfolio(source: Source | None = None, web: WebService = Depends(get_web)) -> dict:
        return await web.portfolio_view(source)

    @app.get("/v1/web/portfolio/closed", dependencies=protected)
    async def web_closed(source: Source | None = None, web: WebService = Depends(get_web)) -> dict:
        return result_or_error(await web.portfolio_tools.list_closed(source=source))

    @app.get("/v1/web/portfolio/performance", dependencies=protected)
    async def web_performance(source: Source | None = None, since: datetime | None = None,
                              until: datetime | None = None, web: WebService = Depends(get_web)) -> dict:
        if any(d is not None and d.tzinfo is None for d in (since, until)):
            raise HTTPException(422, "Dönem sınırlarında saat dilimi gerekli.")
        if since and until and since >= until:
            raise HTTPException(422, "Başlangıç bitişten önce olmalı.")
        result = await asyncio.to_thread(web.repository.performance, source, since, until)
        result["current"] = (await web.portfolio_view(source))["summary"]
        return result

    @app.get("/v1/web/portfolio/{position_id}/transactions", dependencies=protected)
    async def web_transactions(position_id: str, web: WebService = Depends(get_web)) -> dict:
        return result_or_error(await web.portfolio_tools.get_position_tx(position_id))

    @app.post("/v1/web/portfolio/preview", dependencies=protected)
    async def web_preview(req: PreviewRequest) -> dict:
        return preview_position(**req.model_dump())

    @app.get("/v1/web/portfolio/{position_id}", dependencies=protected)
    async def web_position(position_id: str, web: WebService = Depends(get_web)) -> dict:
        position = await asyncio.to_thread(web.repository.get, position_id)
        if position is None:
            raise HTTPException(404, "Pozisyon bulunamadı.")
        return _position_to_dict(position)

    @app.post("/v1/web/portfolio/transactions", dependencies=protected)
    async def web_transaction(req: TransactionRequest, web: WebService = Depends(get_web)) -> dict:
        if req.kind == "BUY":
            symbol = web.require_symbol(req.symbol)
            result = await web.portfolio_tools.buy_position(
                symbol, req.quantity, req.price, req.stop_loss, req.target, req.note,
                req.horizon, req.position_id, req.opened_at, req.operation_id)
        else:
            if not req.position_id:
                raise HTTPException(422, "Kapatılacak pozisyonu seçin.")
            if any(x is not None for x in (req.stop_loss, req.target, req.note, req.horizon, req.opened_at)):
                raise HTTPException(422, "Kapatırken plan değiştirilemez.")
            result = await web.portfolio_tools.sell_position(req.position_id, req.quantity, req.price, req.operation_id)
        return result_or_error(result)

    @app.patch("/v1/web/portfolio/{position_id}", dependencies=protected)
    async def web_plan(position_id: str, req: PlanRequest, web: WebService = Depends(get_web)) -> dict:
        return result_or_error(await web.portfolio_tools.set_levels(
            position_id, req.stop_loss, req.target, req.note, req.horizon))

    @app.get("/v1/web/news", dependencies=protected)
    async def web_news(symbol: str | None = None, days: int = Query(default=7, ge=1, le=14),
                       web: WebService = Depends(get_web)) -> dict:
        return await web.news_view(symbol, days)

    @app.get("/health")
    async def health(runtime: EngineRuntime = Depends(get_runtime)) -> dict:
        return {"status": "ok"}

    @app.post("/v1/chat", dependencies=protected)
    async def chat(req: ChatRequest, runtime: EngineRuntime = Depends(get_runtime),
                   user_id: str | None = Depends(user_identity)):
        preferences = ConversationPreferences(
            horizon=req.preferences.horizon, risk_level=req.preferences.risk_level,
        ) if req.preferences is not None else None

        session_id = f"{user_id}:{req.session_id}" if user_id else req.session_id
        if user_id:
            web = await runtime.web_for(user_id)
            if web.preferences and req.preferences is None:
                preferences = await asyncio.to_thread(web.preferences.get)

        async def events():
            async with aclosing(
                runtime.chat(session_id, req.message, req.output_format, preferences, **({"user_id": user_id} if user_id else {}))
            ) as stream:
                async for event in stream:
                    yield json.dumps(event, ensure_ascii=False) + "\n"

        return StreamingResponse(events(), media_type="application/x-ndjson")

    @app.post("/v1/reset", dependencies=protected)
    async def reset(req: ResetRequest, runtime: EngineRuntime = Depends(get_runtime),
                    user_id: str | None = Depends(user_identity)) -> dict:
        await runtime.reset(f"{user_id}:{req.session_id}" if user_id else req.session_id)
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
