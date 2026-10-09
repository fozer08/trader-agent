import json
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from trader_agent.config.llm import LLMConfig
from trader_agent.engine.api import create_app
from trader_agent.engine.news.llm import AnthropicSummarizer
from trader_agent.engine.news.rss import RssNewsProvider
from trader_agent.engine.repository import (
    NewsRepository, create_db_engine, create_session_factory, init_schema,
)
from trader_agent.engine.runtime import EngineRuntime
from trader_agent.engine.tools.news import NewsTools
from .conftest import NOW
from .test_digest import response
from .test_rss import feed


@pytest.mark.parametrize("failure", [None, "rss", "llm", "truncated"])
async def test_rss_to_model_to_sqlite_to_api_and_tools(tmp_path, make_service, failure):
    requests = []
    failing = False

    def handle(request):
        requests.append(request)
        if request.url.host == "feed.test":
            if failing and failure == "rss":
                return httpx.Response(401)
            return httpx.Response(200, content=feed("ASELSAN yeni sözleşme imzaladı"))
        if failing and failure == "llm":
            return httpx.Response(401, json={"error": "unauthorized"})
        if failing and failure == "truncated":
            return httpx.Response(200, json=response("Yarım", "max_tokens"))
        return httpx.Response(200, json=response("Aselsan yeni sözleşme imzaladı."))

    engine = create_db_engine(tmp_path / "news.db")
    init_schema(engine)
    repo = NewsRepository(create_session_factory(engine))
    async with AsyncExitStack() as stack:
        stack.callback(engine.dispose)
        rss = await stack.enter_async_context(RssNewsProvider(
            "test", "https://feed.test/rss", timeout=5, max_bytes=10000,
            transport=httpx.MockTransport(handle),
        ))
        llm = await stack.enter_async_context(anthropic.AsyncAnthropic(
            api_key="test", max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ))
        news = make_service(
            providers=[rss], repo=repo,
            summarizer=AnthropicSummarizer(llm, model=LLMConfig().light_model, timeout=5,
                                          max_output_tokens=1024),
        )
        runtime = EngineRuntime(lambda _: None, news=news,
                                watchlist=SimpleNamespace(list=lambda: [{"symbol": "ASELS"}]))

        @asynccontextmanager
        async def factory():
            try:
                yield runtime
            finally:
                await runtime.aclose()

        app = create_app(factory)
        await stack.enter_async_context(app.router.lifespan_context(app))
        http = await stack.enter_async_context(httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://engine.test",
        ))
        payload = {"since": (NOW - timedelta(days=1)).isoformat()}
        initial = await http.post("/v1/jobs/daily-digest", json=payload)
        assert initial.status_code == 200
        assert initial.json()["status"] == "updated"
        assert initial.json()["written_digests"] == 1
        assert initial.json()["job_id"]
        tools = NewsTools(news)
        initial_read = await tools.get_symbol_news(["asels"], days=1)
        assert initial_read[0]["digests"][0]["summary"] == "Aselsan yeni sözleşme imzaladı."
        assert (await tools.get_market_news())["digests"] == []
        failing = True
        refreshed = await http.post("/v1/jobs/daily-digest", json=payload)
        assert refreshed.status_code == (502 if failure else 200)
        if failure:
            assert "job=" in refreshed.json()["detail"]
        assert await tools.get_symbol_news(["ASELS"], days=1) == initial_read
        prompt = next(json.loads(req.content) for req in requests if req.url.host != "feed.test")
        assert prompt["model"] == "claude-haiku-5-5"
        assert prompt["thinking"] == {"type": "disabled"}
        assert prompt["output_config"] == {"effort": "low"}
        assert json.loads(prompt["messages"][0]["content"])["scope"] == "ASELS"
