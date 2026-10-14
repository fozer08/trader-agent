from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Callable
from zoneinfo import ZoneInfo

import anthropic

from ..config.base import data_dir
from ..config.llm import LLMConfig
from ..config.market import MarketConfig
from ..config.news import NewsConfig
from ..utils.logging import get_logger
from .agent.runner import AgentRunner
from .agent.memory import ConversationMemory
from .jobs.prefetch_market import run as prefetch_market
from .marketdata.cache import MarketBarCache
from .calendar.loader import load_calendar
from .marketdata.provider import IsYatirimProvider
from .marketdata.service import MarketService
from .news.rss import RssNewsProvider
from .news.llm import AnthropicSummarizer
from .news.matching import CompanyMatcher
from .news.types import RefreshResult
from .news.service import NewsService
from .repository.conversations import ConversationRepository
from .repository.preferences import TradingPreferencesRepository
from .repository.watchlist import WatchlistORM
from sqlalchemy import select
from .repository import (
    NewsRepository,
    PortfolioRepository,
    WatchlistRepository,
    create_db_engine,
    create_session_factory,
    init_schema,
)
from .analysis.service import AnalysisService
from .tools.analysis import AnalysisTools
from .tools.news import NewsTools
from .tools.recommendations import RecommendationTools
from .recommendations import RecommendationService
from .repository.recommendations import RecommendationRepository
from .tools.portfolio import PortfolioTools
from .web import WebService
from .agent.preferences import ConversationPreferences

_log = get_logger(__name__)


@dataclass
class _Session:
    """Tek sohbetin eşzamanlı turlarını ve kapanışını koordine eder."""
    runner: AgentRunner
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    users: int = 0

    @asynccontextmanager
    async def use(self) -> AsyncIterator[AgentRunner]:
        """Kilit bekleyen ve çalışan istekler boyunca runner'ı kullanımda tutar."""
        self.users += 1
        try:
            async with self.lock:
                yield self.runner
        finally:
            self.users -= 1


class EngineRuntime:
    """Paylaşılan servisler ve sohbet oturumları; HTTP ve arayüzlerden bağımsızdır."""

    def __init__(
        self,
        runner_factory: Callable[[str], AgentRunner],
        *,
        market: MarketService | None = None,
        news: NewsService | None = None,
        portfolio: PortfolioRepository | None = None,
        watchlist: WatchlistRepository | None = None,
        analysis: AnalysisService | None = None,
        max_sessions: int = 100,
        conversations: ConversationRepository | None = None,
        user_runner_factory: Callable[[str, str], AgentRunner] | None = None,
        web_factory: Callable[[str], WebService] | None = None,
        watched_symbols: Callable[[], list[dict]] | None = None,
    ) -> None:
        self._conversations = conversations
        self._runner_factory = runner_factory
        self._user_runner_factory = user_runner_factory
        self._web_factory = web_factory
        self._user_web: dict[str, WebService] = {}
        self._web_guard = asyncio.Lock()
        self._watched_symbols = watched_symbols
        self._market = market
        self._news = news
        self._watchlist = watchlist
        self._max_sessions = max_sessions
        self._sessions: dict[str, _Session] = {}
        self._session_guard = asyncio.Lock()
        self.web: WebService | None = None
        if market is not None and portfolio is not None and watchlist is not None:
            if analysis is None:
                raise ValueError("Web runtime requires a shared AnalysisService.")
            self.web = WebService(market, portfolio, news, watchlist, analysis,
                RecommendationService(RecommendationRepository(portfolio._session_factory, portfolio.user_id),
                    market.calendar, lambda symbol: watchlist.stock(symbol)["symbol"], lambda: market.now()))

    async def _session(self, session_id: str, output_format: str, user_id: str | None = None) -> _Session:
        async with self._session_guard:
            if session_id in self._sessions:
                return self._sessions[session_id]
            if len(self._sessions) >= self._max_sessions:
                oldest = next(
                    (key for key, session in self._sessions.items() if session.users == 0),
                    None,
                )
                if oldest is None:
                    raise RuntimeError("Tüm sohbet oturumları meşgul; tekrar deneyin.")
                removed = self._sessions.pop(oldest)
                await removed.runner.aclose()
            session = _Session(self._user_runner_factory(output_format, user_id)
                               if user_id and self._user_runner_factory
                               else self._runner_factory(output_format))
            if self._conversations:
                try:
                    snapshot = await asyncio.to_thread(self._conversations.get, session_id)
                    if snapshot is not None:
                        session.runner.restore_memory(snapshot)
                except BaseException:
                    await session.runner.aclose()
                    raise
            self._sessions[session_id] = session
            return session

    async def chat(
        self, session_id: str, message: str, output_format: str = "plain",
        preferences: ConversationPreferences | None = None,
        user_id: str | None = None,
    ) -> AsyncIterator[dict]:
        try:
            session = await self._session(session_id, output_format, user_id)
            async with session.use() as runner:
                runner.set_output_format(output_format)
                try:
                    async with aclosing(runner.chat(message, preferences=preferences)) as chunks:
                        async for event in chunks:
                            yield dict(event)
                finally:
                    if self._conversations:
                        await asyncio.to_thread(self._conversations.save, session_id, runner.memory_dump())
        except Exception as exc:
            _log.exception("chat failed session=%s", session_id)
            yield {"type": "error", "message": str(exc) or type(exc).__name__}

    async def web_for(self, user_id: str) -> WebService:
        """Kullanıcıya bağlı adaptörler ortak piyasa ve analiz servislerini paylaşır."""
        if self._web_factory is None:
            raise RuntimeError("User services are not configured")
        async with self._web_guard:
            if user_id not in self._user_web:
                if len(self._user_web) >= self._max_sessions:
                    oldest = next(iter(self._user_web))
                    await self._user_web.pop(oldest).aclose()
                self._user_web[user_id] = self._web_factory(user_id)
            return self._user_web[user_id]

    async def reset(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if session is None and self._conversations:
            await asyncio.to_thread(self._conversations.save, session_id, ConversationMemory().dump())
        if session is not None:
            async with session.use() as runner:
                runner.reset()
                if self._conversations:
                    await asyncio.to_thread(self._conversations.save, session_id, runner.memory_dump())

    def status(self) -> dict:
        return {"sessions": list(self._sessions)}

    async def memory(self, session_id: str) -> dict:
        session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        async with session.use() as runner:
            return runner.memory_dump()

    async def prefetch_market(self) -> dict[str, bool]:
        if self._market is None:
            raise RuntimeError("Market service is not configured.")
        entries = (self._watched_symbols() if self._watched_symbols else
                   self._watchlist.list() if self._watchlist is not None else [])
        return await prefetch_market(self._market, entries)

    async def daily_digest(self, since: datetime | None = None) -> RefreshResult:
        if self._news is None:
            raise RuntimeError("News service is not configured.")
        entries = (self._watched_symbols() if self._watched_symbols else
                   self._watchlist.list() if self._watchlist is not None else [])
        return await self._news.refresh_daily_digest(
            symbols={entry["symbol"] for entry in entries}, since=since,
        )

    async def aclose(self) -> None:
        """Kapatma hatalarında da tüm kaynakları kapatır ve oturumları temizler."""
        async with AsyncExitStack() as stack:
            stack.callback(self._sessions.clear)
            for session in self._sessions.values():
                stack.push_async_callback(session.runner.aclose)
            if self._news is not None:
                stack.push_async_callback(self._news.aclose)
            if self.web is not None:
                stack.push_async_callback(self.web.aclose)
            for web in self._user_web.values():
                stack.push_async_callback(web.aclose)
            stack.callback(self._user_web.clear)


def _load_stocklist(config: MarketConfig) -> list[dict]:
    path = config.stocklist_path()
    if not path.exists():
        raise RuntimeError(f"Stocklist dosyası bulunamadı: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Stocklist JSON parse hatası: {exc}") from exc


@asynccontextmanager
async def open_runtime() -> AsyncIterator[EngineRuntime]:
    """Engine'in tüm kaynaklarını bir kez kurar ve kapanışta serbest bırakır."""
    llm_config = LLMConfig.load()
    market_config = MarketConfig.load()
    news_config = NewsConfig.load()
    calendar = load_calendar(market_config)
    stocklist = _load_stocklist(market_config)
    async with AsyncExitStack() as stack:
        database = create_db_engine(data_dir() / "engine.db")
        stack.callback(database.dispose)
        init_schema(database)
        session_factory = create_session_factory(database)
        watchlist = WatchlistRepository(session_factory, stocklist)
        portfolio = PortfolioRepository(session_factory)
        news_repo = NewsRepository(session_factory)
        llm = await stack.enter_async_context(anthropic.AsyncAnthropic(
            timeout=news_config.llm_timeout, max_retries=1,
        ))
        bar_store = MarketBarCache()
        provider = await stack.enter_async_context(
            IsYatirimProvider(
                calendar=calendar,
                store=bar_store,
            )
        )
        market = MarketService(provider=provider, store=bar_store)
        rss = [
            await stack.enter_async_context(RssNewsProvider(
                name=feed.name, url=str(feed.url), timeout=news_config.rss_timeout,
                max_bytes=news_config.max_feed_bytes,
            ))
            for feed in news_config.feeds
        ]
        news = NewsService(
            providers=rss,
            summarizer=AnthropicSummarizer(
                llm, model=llm_config.light_model, timeout=news_config.llm_timeout,
                max_output_tokens=news_config.max_output_tokens,
            ),
            repo=news_repo,
            matcher=CompanyMatcher({
                entry["symbol"]: entry["name"] for entry in watchlist.stocklist()
            }),
            timezone=ZoneInfo(market_config.exchange.timezone),
            fetch_concurrency=news_config.fetch_concurrency,
            digest_concurrency=news_config.digest_concurrency,
            job_timeout=news_config.job_timeout,
            max_input_chars=news_config.max_input_chars,
        )
        analysis = AnalysisService(market)
        def user_repositories(user_id: str) -> tuple[WatchlistRepository, PortfolioRepository]:
            return (WatchlistRepository(session_factory, stocklist, user_id),
                    PortfolioRepository(session_factory, user_id))

        def make_runner(output_format: str, user_id: str = "system") -> AgentRunner:
            user_watchlist, user_portfolio = user_repositories(user_id)
            tools = [
                *AnalysisTools(analysis=analysis, watchlist=user_watchlist).as_tool_list(),
                *PortfolioTools(repository=user_portfolio, market=market).as_tool_list(),
                *RecommendationTools(RecommendationService(
                    RecommendationRepository(session_factory, user_id), calendar,
                    lambda symbol: user_watchlist.stock(symbol)["symbol"], market.now),
                    llm_config.agent_model).as_tool_list(),
                *NewsTools(service=news).as_tool_list(),
            ]
            return AgentRunner(
                config=llm_config, watchlist=user_watchlist, calendar=calendar,
                exchange_name=market_config.exchange.name, tools=tools,
                delay_minutes=provider.delay_minutes, output_format=output_format,
            )

        def make_web(user_id: str) -> WebService:
            user_watchlist, user_portfolio = user_repositories(user_id)
            return WebService(market, user_portfolio, news, user_watchlist, analysis,
                RecommendationService(RecommendationRepository(session_factory, user_id), calendar,
                    lambda symbol: user_watchlist.stock(symbol)["symbol"], market.now),
                TradingPreferencesRepository(session_factory, user_id))

        def watched_symbols() -> list[dict]:
            with session_factory() as session:
                symbols = set(session.scalars(select(WatchlistORM.symbol).distinct()))
            return [entry for entry in stocklist if entry["symbol"] in symbols]

        runtime = EngineRuntime(make_runner, market=market, news=news, portfolio=portfolio,
                                watchlist=watchlist, analysis=analysis,
                                user_runner_factory=make_runner, web_factory=make_web,
                                watched_symbols=watched_symbols,
                                conversations=ConversationRepository(session_factory))
        stack.push_async_callback(runtime.aclose)
        yield runtime
