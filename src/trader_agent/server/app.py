from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from ..agent.runner import AgentRunner
from ..config.main import MainConfig
from ..env import load_env
from ..market.provider import IsYatirimProvider
from ..repository import (
    PositionRepository,
    create_db_engine,
    create_session_factory,
    init_schema,
)
from ..tools.analysis import AnalysisTools
from ..tools.portfolio import PortfolioTools


# ---- State -------------------------------------------------------------------

_MAX_SESSIONS = 100
_runner_factory: _RunnerFactory | None = None
_sessions: dict[str, AgentRunner] = {}


class _RunnerFactory:
    def __init__(
        self,
        cfg: MainConfig,
        watchlist: list[dict],
        provider: IsYatirimProvider,
        position_repo: PositionRepository,
    ) -> None:
        self._cfg = cfg
        self._watchlist = watchlist
        self._tools = [
            *AnalysisTools(provider=provider, watchlist=watchlist).as_tool_list(),
            *PortfolioTools(repository=position_repo).as_tool_list(),
        ]
        self._session = cfg.market.exchanges["bist"].trading_session("equities")

    def make(self, output_format: str = "plain") -> AgentRunner:
        return AgentRunner(
            config=self._cfg.llm,
            watchlist=self._watchlist,
            session=self._session,
            tools=self._tools,
            output_format=output_format,
        )


# ---- Lifespan ----------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _runner_factory, _sessions
    load_env()
    cfg = MainConfig.load()
    if "bist" not in cfg.market.exchanges:
        raise RuntimeError("Config'de 'bist' exchange tanımlı değil.")
    watchlist_path = cfg.market.watchlist_path()
    if not watchlist_path.exists():
        raise RuntimeError(f"Watchlist dosyası bulunamadı: {watchlist_path}")
    try:
        watchlist: list[dict] = json.loads(watchlist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Watchlist JSON parse hatası: {exc}") from exc
    engine = create_db_engine(cfg.database_path())
    init_schema(engine)
    position_repo = PositionRepository(create_session_factory(engine))
    async with IsYatirimProvider(
        session=cfg.market.exchanges["bist"].trading_session("equities")
    ) as provider:
        _runner_factory = _RunnerFactory(cfg, watchlist, provider, position_repo)
        _sessions = {}
        try:
            yield
        finally:
            _runner_factory = None
            _sessions = {}
            engine.dispose()


# ---- App ---------------------------------------------------------------------

app = FastAPI(title="trader-agent", lifespan=lifespan)


# ---- Schemas -----------------------------------------------------------------

class ChatRequest(BaseModel):
    session_id: str
    message: str
    output_format: str = "plain"


class ChatResponse(BaseModel):
    response: str
    tools_used: list[str]


class ResetRequest(BaseModel):
    session_id: str


# ---- Routes ------------------------------------------------------------------

@app.post("/v1/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    if _runner_factory is None:
        raise HTTPException(status_code=503, detail="Server not ready")

    if req.session_id not in _sessions:
        if len(_sessions) >= _MAX_SESSIONS:
            oldest = next(iter(_sessions))
            del _sessions[oldest]
        _sessions[req.session_id] = _runner_factory.make(req.output_format)

    runner = _sessions[req.session_id]
    text_chunks: list[str] = []
    tools_used: list[str] = []

    async for chunk in runner.chat(req.message):
        if chunk.startswith("\x00TOOL:") and chunk.endswith("\x00"):
            tools_used.append(chunk[6:-1])
        else:
            text_chunks.append(chunk)

    return ChatResponse(response="".join(text_chunks), tools_used=tools_used)


@app.post("/v1/reset")
async def reset(req: ResetRequest) -> dict:
    if req.session_id in _sessions:
        _sessions[req.session_id].reset()
    return {"ok": True}


@app.get("/v1/status")
async def status() -> dict:
    return {"sessions": list(_sessions.keys())}
