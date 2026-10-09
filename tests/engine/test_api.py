from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from trader_agent.client import EngineClient
from trader_agent.engine.api import create_app
from trader_agent.engine.runtime import EngineRuntime
from trader_agent.engine.news.types import RefreshResult


class Runner:
    def __init__(self, output_format):
        self.messages = []
        self.output_format = output_format

    def set_output_format(self, output_format):
        self.output_format = output_format

    async def chat(self, message):
        self.messages.append(message)
        yield {"type": "tool", "name": "scan"}
        yield {"type": "text", "content": "Türkçe yanıt"}

    def reset(self):
        self.messages.clear()

    def memory_dump(self):
        return {"messages": self.messages.copy()}

    async def aclose(self):
        pass


@pytest.fixture
async def service():
    runtime = EngineRuntime(Runner)
    runtime.prefetch_market = AsyncMock(return_value={"THYAO": True})
    runtime.daily_digest = AsyncMock(return_value=RefreshResult(
        "test-job", "empty", datetime(2026, 10, 1, tzinfo=timezone.utc),
        datetime(2026, 10, 2, tzinfo=timezone.utc), 0, 0, 0,
    ))

    @asynccontextmanager
    async def factory():
        try:
            yield runtime
        finally:
            await runtime.aclose()

    app = create_app(factory, api_key="test-key")
    async with app.router.lifespan_context(app):
        yield app, runtime


def make_client(app, key="test-key"):
    return httpx.AsyncClient(
        base_url="http://remote-engine", headers={"Authorization": f"Bearer {key}"},
        transport=httpx.ASGITransport(app=app),
    )


async def test_streaming_reset_and_state_survive_client_reconnect(service):
    app, runtime = service
    async with make_client(app) as http:
        response = await http.post("/v1/chat", json={
            "session_id": "web:session", "message": "Listeyi tara", "output_format": "markdown",
        })
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/x-ndjson"
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events == [
            {"type": "tool", "name": "scan"},
            {"type": "text", "content": "Türkçe yanıt"},
        ]
    async with make_client(app) as reconnected:
        assert (await reconnected.get("/v1/status")).json() == {"sessions": ["web:session"]}
        params = {"session_id": "web:session"}
        assert (await reconnected.get("/v1/debug/state", params=params)).json() == {
            "messages": ["Listeyi tara"],
        }
        assert (await reconnected.post("/v1/reset", json=params)).json() == {"ok": True}
        assert (await reconnected.get("/v1/debug/state", params=params)).json() == {"messages": []}
    assert runtime.status() == {"sessions": ["web:session"]}


async def test_remote_api_key_is_required_for_client_requests(service):
    app, _ = service
    async with make_client(app, key="wrong-key") as http:
        assert (await http.get("/v1/status")).status_code == 401
        response = await http.post("/v1/chat", json={"session_id": "web:session", "message": "hello"})
        assert response.status_code == 401


async def test_non_ascii_authorization_header_returns_401(service):
    app, _ = service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://engine",
    ) as http:
        response = await http.get("/v1/status", headers={b"Authorization": b"Bearer \xff"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Unauthorized"}


async def test_health_is_available_without_authentication(service):
    app, _ = service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://engine"
    ) as http:
        response = await http.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


async def test_jobs_are_sent_to_running_engine(service):
    app, runtime = service
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    async with EngineClient(
        transport=httpx.ASGITransport(app=app),
        url="http://remote-engine", api_key="test-key",
    ) as client:
        assert await client.prefetch_market() == {"THYAO": True}
        result = await client.daily_digest(since)
        assert result["status"] == "empty"
        assert result["written_digests"] == 0
        assert result["job_id"] == "test-job"
    runtime.prefetch_market.assert_awaited_once()
    runtime.daily_digest.assert_awaited_once_with(since)


async def test_unknown_session_returns_404(service):
    app, _ = service
    async with make_client(app) as http:
        response = await http.get("/v1/debug/state", params={"session_id": "missing"})
        assert response.status_code == 404


@pytest.mark.parametrize("payload", [
    {"session_id": "", "message": "hello", "output_format": "plain"},
    {"session_id": "web:session", "message": "hello", "output_format": "html"},
])
async def test_invalid_request_does_not_create_session(service, payload):
    app, runtime = service
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://engine",
        headers={"Authorization": "Bearer test-key"},
    ) as http:
        response = await http.post("/v1/chat", json=payload)
        assert response.status_code == 422
        assert runtime.status() == {"sessions": []}


async def test_web_markdown_chat_uses_its_own_session(service):
    app, runtime = service
    async with make_client(app) as http:
        for session_id, message in [("web:first", "ilk"), ("web:second", "ikinci")]:
            response = await http.post("/v1/chat", json={
                "session_id": session_id, "message": message, "output_format": "markdown",
            })
            assert response.status_code == 200
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1] == {"type": "text", "content": "Türkçe yanıt"}
        assert await runtime.memory("web:first") == {"messages": ["ilk"]}
        assert await runtime.memory("web:second") == {"messages": ["ikinci"]}
    assert runtime._sessions["web:second"].runner.output_format == "markdown"
