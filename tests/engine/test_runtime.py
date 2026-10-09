from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from trader_agent.engine.runtime import EngineRuntime
from trader_agent.engine.repository import (
    WatchlistRepository, create_db_engine, create_session_factory, init_schema,
)


class FakeRunner:
    def __init__(self, output_format="plain"):
        self.output_format = output_format
        self.messages = []
        self.closed = False

    def set_output_format(self, output_format):
        self.output_format = output_format

    async def chat(self, message):
        self.messages.append(message)
        yield {"type": "tool", "name": "get_levels"}
        yield {"type": "text", "content": f"Yanıt: {message}"}

    def reset(self):
        self.messages.clear()

    def memory_dump(self):
        return {"messages": list(self.messages), "output_format": self.output_format}

    async def aclose(self):
        self.closed = True


async def test_web_sessions_share_runtime_and_keep_independent_conversations():
    runtime = EngineRuntime(FakeRunner)
    first = [event async for event in runtime.chat("web:first", "ilk mesaj", "markdown")]
    assert first == [
        {"type": "tool", "name": "get_levels"},
        {"type": "text", "content": "Yanıt: ilk mesaj"},
    ]
    _ = [event async for event in runtime.chat("web:second", "ikinci mesaj", "markdown")]
    assert (await runtime.memory("web:first"))["messages"] == ["ilk mesaj"]
    assert (await runtime.memory("web:second"))["messages"] == ["ikinci mesaj"]
    await runtime.reset("web:first")
    assert (await runtime.memory("web:first"))["messages"] == []
    assert (await runtime.memory("web:second"))["messages"] == ["ikinci mesaj"]
    await runtime.aclose()


async def test_format_change_preserves_conversation():
    runtime = EngineRuntime(FakeRunner)
    _ = [event async for event in runtime.chat("shared", "ilk mesaj", "plain")]
    _ = [event async for event in runtime.chat("shared", "ikinci mesaj", "markdown")]
    assert await runtime.memory("shared") == {
        "messages": ["ilk mesaj", "ikinci mesaj"], "output_format": "markdown",
    }
    await runtime.aclose()


async def test_session_eviction_closes_old_model_client():
    runners = []

    def factory(output_format):
        runner = FakeRunner(output_format)
        runners.append(runner)
        return runner

    runtime = EngineRuntime(factory, max_sessions=1)
    _ = [event async for event in runtime.chat("first", "a")]
    _ = [event async for event in runtime.chat("second", "b")]
    assert runners[0].closed
    assert runtime.status() == {"sessions": ["second"]}
    await runtime.aclose()
    assert runners[1].closed


async def test_busy_session_is_not_evicted():
    started = asyncio.Event()
    finish = asyncio.Event()

    class SlowRunner(FakeRunner):
        async def chat(self, message):
            started.set()
            await finish.wait()
            yield {"type": "text", "content": "tamam"}

    runtime = EngineRuntime(SlowRunner, max_sessions=1)

    async def consume():
        return [event async for event in runtime.chat("busy", "a")]

    task = asyncio.create_task(consume())
    await started.wait()
    rejected = [event async for event in runtime.chat("other", "b")]
    assert rejected[0]["type"] == "error"
    assert runtime.status() == {"sessions": ["busy"]}
    finish.set()
    await task
    await runtime.aclose()


async def test_same_session_requests_are_serialized():
    active = 0
    maximum = 0

    class Runner(FakeRunner):
        async def chat(self, message):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0)
            yield {"type": "text", "content": message}
            active -= 1

    runtime = EngineRuntime(Runner)

    async def consume(message):
        return [event async for event in runtime.chat("same", message)]

    await asyncio.gather(consume("a"), consume("b"))
    assert maximum == 1
    await runtime.aclose()


@pytest.mark.parametrize("operation", ["chat", "reset", "memory"])
async def test_session_with_waiting_request_is_not_evicted(operation):
    runtime = EngineRuntime(FakeRunner, max_sessions=1)

    async def consume(session_id, message):
        return [event async for event in runtime.chat(session_id, message)]

    await consume("first", "initial")
    session = runtime._sessions["first"]
    await session.lock.acquire()
    request = (
        consume("first", "queued") if operation == "chat"
        else getattr(runtime, operation)("first")
    )
    waiting = asyncio.create_task(request)
    await asyncio.sleep(0)
    incoming = asyncio.create_task(consume("second", "new session"))
    # Yeni oturum görevi, kilidi devralacak bekleyen istekten önce çalışır.
    session.lock.release()
    try:
        rejected, _ = await asyncio.gather(incoming, waiting)
        assert rejected[0]["type"] == "error"
        assert not session.runner.closed
        assert runtime.status() == {"sessions": ["first"]}
    finally:
        await runtime.aclose()


async def test_cancelled_waiting_request_does_not_prevent_eviction():
    runtime = EngineRuntime(FakeRunner, max_sessions=1)

    async def consume(session_id):
        return [event async for event in runtime.chat(session_id, "message")]

    await consume("first")
    session = runtime._sessions["first"]
    await session.lock.acquire()
    waiting = asyncio.create_task(consume("first"))
    await asyncio.sleep(0)
    waiting.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await waiting
    finally:
        session.lock.release()
    try:
        events = await consume("second")
        assert events[-1]["type"] == "text"
        assert session.runner.closed
    finally:
        await runtime.aclose()


@pytest.mark.parametrize("failing_resource", ["web", "first", "second"])
async def test_close_failure_does_not_skip_other_resources_or_session_cleanup(failing_resource):
    runners = []

    def factory(output_format):
        runner = FakeRunner(output_format)
        runners.append(runner)
        return runner

    runtime = EngineRuntime(factory)
    for session_id in ("first", "second"):
        _ = [event async for event in runtime.chat(session_id, "message")]
    resources = {
        "web": SimpleNamespace(aclose=AsyncMock()),
        "first": runners[0],
        "second": runners[1],
    }
    runtime.web = resources["web"]
    for name, resource in resources.items():
        resource.aclose = AsyncMock(
            side_effect=RuntimeError("close failed") if name == failing_resource else None,
        )

    with pytest.raises(RuntimeError, match="close failed"):
        await runtime.aclose()

    for resource in resources.values():
        resource.aclose.assert_awaited_once()
    assert runtime.status() == {"sessions": []}


async def test_close_waits_for_other_resources_before_raising_failure():
    runtime = EngineRuntime(FakeRunner)
    for session_id in ("first", "second"):
        _ = [event async for event in runtime.chat(session_id, "message")]
    started = asyncio.Event()
    finish = asyncio.Event()
    completed = asyncio.Event()

    async def slow_close():
        started.set()
        await finish.wait()
        completed.set()

    runtime._sessions["first"].runner.aclose = AsyncMock(side_effect=RuntimeError("close failed"))
    runtime._sessions["second"].runner.aclose = slow_close
    closing = asyncio.create_task(runtime.aclose())
    try:
        await started.wait()
        await asyncio.sleep(0)
        assert not closing.done()
    finally:
        finish.set()
        with pytest.raises(RuntimeError, match="close failed"):
            await closing
    assert completed.is_set()
    assert runtime.status() == {"sessions": []}


async def test_jobs_use_the_runtimes_shared_services():
    market = SimpleNamespace(get_daily=AsyncMock(return_value=[object()]))
    news = SimpleNamespace(refresh_daily_digest=AsyncMock(), aclose=AsyncMock())
    watchlist = [{"symbol": "THYAO"}]
    runtime = EngineRuntime(
        FakeRunner, market=market, news=news, watchlist=SimpleNamespace(list=lambda: watchlist),
    )
    assert await runtime.prefetch_market() == {"THYAO": True}
    market.get_daily.assert_awaited_once_with("THYAO", period=200)
    await runtime.daily_digest()
    news.refresh_daily_digest.assert_awaited_once_with(symbols={"THYAO"}, since=None)
    await runtime.aclose()


async def test_jobs_read_updated_database_watchlist(tmp_path):
    database = create_db_engine(tmp_path / "data.db")
    init_schema(database)
    watchlist = WatchlistRepository(create_session_factory(database), [
        {"symbol": "THYAO", "name": "Türk Hava Yolları"},
        {"symbol": "ASELS", "name": "Aselsan"},
    ])
    watchlist.add("THYAO")
    market = SimpleNamespace(get_daily=AsyncMock(return_value=[object()]))
    news = SimpleNamespace(refresh_daily_digest=AsyncMock(), aclose=AsyncMock())
    runtime = EngineRuntime(FakeRunner, market=market, news=news, watchlist=watchlist)
    try:
        assert await runtime.prefetch_market() == {"THYAO": True}
        watchlist.add("ASELS")
        watchlist.remove("THYAO")

        assert await runtime.prefetch_market() == {"ASELS": True}
        await runtime.daily_digest()
        news.refresh_daily_digest.assert_awaited_once_with(
            symbols={"ASELS"}, since=None,
        )
    finally:
        await runtime.aclose()
        database.dispose()
