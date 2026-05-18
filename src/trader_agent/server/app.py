from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ..agent.runner import AgentRunner
from ..config.main import MainConfig
from ..env import load_env
from ..market.provider import IsYatirimProvider
from ..repository import (
    PortfolioRepository,
    create_db_engine,
    create_session_factory,
    init_schema,
)
from ..tools.analysis import AnalysisTools
from ..tools.portfolio import PortfolioTools
from ..utils.logging import get_logger

_log = get_logger(__name__)


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
        portfolio_repo: PortfolioRepository,
    ) -> None:
        self._cfg = cfg
        self._watchlist = watchlist
        self._tools = [
            *AnalysisTools(provider=provider, watchlist=watchlist).as_tool_list(),
            *PortfolioTools(repository=portfolio_repo, provider=provider).as_tool_list(),
        ]
        self._trading_session = cfg.market.exchange.trading_session()
        self._exchange_name = cfg.market.exchange.name
        self._delay_minutes = getattr(provider, "delay_minutes", None)

    def make(self, output_format: str = "plain") -> AgentRunner:
        return AgentRunner(
            config=self._cfg.llm,
            watchlist=self._watchlist,
            session=self._trading_session,
            exchange_name=self._exchange_name,
            tools=self._tools,
            delay_minutes=self._delay_minutes,
            output_format=output_format,
        )


# ---- Lifespan ----------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _runner_factory, _sessions
    load_env()
    cfg = MainConfig.load()
    watchlist_path = cfg.market.watchlist_path()
    if not watchlist_path.exists():
        raise RuntimeError(f"Watchlist dosyası bulunamadı: {watchlist_path}")
    try:
        watchlist: list[dict] = json.loads(watchlist_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Watchlist JSON parse hatası: {exc}") from exc
    engine = create_db_engine(cfg.database_path())
    init_schema(engine)
    session_factory = create_session_factory(engine)
    portfolio_repo = PortfolioRepository(session_factory)
    async with IsYatirimProvider(
        session=cfg.market.exchange.trading_session()
    ) as provider:
        _runner_factory = _RunnerFactory(cfg, watchlist, provider, portfolio_repo)
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


class ResetRequest(BaseModel):
    session_id: str


# ---- Routes ------------------------------------------------------------------

@app.post("/v1/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    """ndjson stream: her satır bir event — {type: "text"|"tool"|"error", ...}."""
    if _runner_factory is None:
        raise HTTPException(status_code=503, detail="Server not ready")

    if req.session_id not in _sessions:
        if len(_sessions) >= _MAX_SESSIONS:
            oldest = next(iter(_sessions))
            del _sessions[oldest]
        _sessions[req.session_id] = _runner_factory.make(req.output_format)

    runner = _sessions[req.session_id]

    async def event_stream():
        try:
            async for chunk in runner.chat(req.message):
                if chunk.startswith("\x00TOOL:") and chunk.endswith("\x00"):
                    event = {"type": "tool", "name": chunk[6:-1]}
                else:
                    event = {"type": "text", "content": chunk}
                yield json.dumps(event, ensure_ascii=False) + "\n"
        except Exception as exc:
            _log.exception("chat stream failed")
            err = {"type": "error", "message": str(exc) or type(exc).__name__}
            yield json.dumps(err, ensure_ascii=False) + "\n"

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")


@app.post("/v1/reset")
async def reset(req: ResetRequest) -> dict:
    if req.session_id in _sessions:
        _sessions[req.session_id].reset()
    return {"ok": True}


@app.get("/v1/status")
async def status() -> dict:
    return {"sessions": list(_sessions.keys())}


@app.get("/v1/debug/state")
async def debug_state(session_id: str) -> dict:
    runner = _sessions.get(session_id)
    if runner is None:
        raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
    return runner.memory_dump()
