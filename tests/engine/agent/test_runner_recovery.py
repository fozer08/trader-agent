from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from typing import AsyncIterator
from unittest.mock import AsyncMock, Mock

import anthropic
import httpx
import pytest
from anthropic.types import (
    Message, RedactedThinkingBlock, ServerToolUseBlock, TextBlock, ThinkingBlock, ToolUseBlock, Usage,
)

from trader_agent.config.llm import LLMConfig
from trader_agent.engine.agent.runner import AgentRunner
from trader_agent.engine.api import create_app
from trader_agent.engine.repository import (
    PortfolioRepository, create_db_engine, create_session_factory, init_schema,
)
from trader_agent.engine.runtime import EngineRuntime
from trader_agent.engine.tools.portfolio import PortfolioTools
from trader_agent.engine.tools.recommendations import RecommendationTools
from trader_agent.engine.recommendations import RecommendationService
from trader_agent.engine.repository.recommendations import RecommendationRepository
from trader_agent.engine.agent.preferences import ConversationPreferences


def _message(*blocks: TextBlock | ThinkingBlock | RedactedThinkingBlock | ToolUseBlock) -> Message:
    return Message(
        id="test-message", model="test-model", role="assistant", type="message",
        content=list(blocks), stop_sequence=None,
        stop_reason="tool_use" if any(block.type == "tool_use" for block in blocks) else "end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _buy(call_id: str = "buy", symbol: str = "THYAO") -> ToolUseBlock:
    return ToolUseBlock(
        id=call_id, type="tool_use", name="buy_position",
        input={"symbol": symbol, "quantity": 1, "price": 100.0, "horizon": "swing"},
    )


class _Stream:
    def __init__(self, *blocks: TextBlock | ThinkingBlock | RedactedThinkingBlock | ToolUseBlock) -> None:
        self.message = _message(*blocks)
        self.closed = False

    async def __aenter__(self) -> _Stream:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.closed = True

    async def __aiter__(self) -> AsyncIterator[SimpleNamespace]:
        for block in self.message.content:
            if block.type == "text":
                yield SimpleNamespace(
                    type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=block.text),
                )

    async def get_final_message(self) -> Message:
        return self.message


def _answer() -> _Stream:
    return _Stream(TextBlock(type="text", text="Kayıt tamamlandı."))


@pytest.fixture
async def agent(tmp_path, monkeypatch, bist_calendar):
    database = create_db_engine(tmp_path / "portfolio.db")
    init_schema(database)
    repo = PortfolioRepository(create_session_factory(database))
    tools = PortfolioTools(repo, SimpleNamespace())
    client = SimpleNamespace(
        messages=SimpleNamespace(
            stream=Mock(),
            create=AsyncMock(return_value=_message(ToolUseBlock(
                id="state", type="tool_use", name="update_state",
                input={"state": {}, "summary": ""},
            ))),
        ),
        close=AsyncMock(),
    )
    monkeypatch.setattr("trader_agent.engine.agent.runner.anthropic.AsyncAnthropic", lambda **kwargs: client)
    recommendations = RecommendationRepository(create_session_factory(database))
    recommendation_tools = RecommendationTools(RecommendationService(
        recommendations, bist_calendar, lambda symbol: symbol,
        lambda: datetime(2026, 10, 7, 12, tzinfo=bist_calendar.timezone)), "test-model")
    runner = AgentRunner(
        config=LLMConfig(), watchlist=SimpleNamespace(list=lambda: []),
        calendar=bist_calendar, exchange_name="Test", tools=[*tools.as_tool_list(), *recommendation_tools.as_tool_list()],
    )
    monkeypatch.setattr(runner, "_now", lambda: datetime(2026, 10, 7, 12, tzinfo=bist_calendar.timezone))
    runtime = EngineRuntime(lambda _: runner)
    try:
        yield SimpleNamespace(runner=runner, repo=repo, client=client, runtime=runtime, recommendations=recommendations)
    finally:
        await runtime.aclose()
        await runner.aclose()
        database.dispose()


async def _consume(agent, text: str = "Bir lot alım kaydet") -> list[dict]:
    return [event async for event in agent.runtime.chat("session", text)]


async def test_chat_preferences_change_without_rewriting_history_or_losing_session(agent):
    agent.client.messages.stream.side_effect = [_answer(), _answer(), _answer()]
    # Hafıza modeli eski tercihi döndürse bile HTTP'deki yeni seçim korunmalı.
    agent.client.messages.create.return_value = _message(ToolUseBlock(
        id="state", type="tool_use", name="update_state",
        input={"state": {"user_context": {"horizon": "position", "risk_level": "low"}}, "summary": "Özet"},
    ))

    @asynccontextmanager
    async def factory():
        yield agent.runtime

    app = create_app(factory)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://engine",
    ) as http:
        first = {"horizon": "intraday", "risk_level": "high"}
        second = {"horizon": "position", "risk_level": "moderate"}
        for text, preferences in [("İlk değerlendirme", first), ("Şimdi tekrar değerlendir", second)]:
            response = await http.post("/v1/chat", json={
                "session_id": "session", "message": text, "preferences": preferences,
            })
            assert response.status_code == 200
            assert not any(json.loads(line)["type"] == "error" for line in response.text.splitlines())
        memory = await agent.runtime.memory("session")
        assert memory["state"]["user_context"]["risk_level"] == "moderate"
        assert memory["recent_turns"][0][0]["preferences"] == first
        assert memory["recent_turns"][1][0]["preferences"] == second
        assert memory["recent_turns"][0][0]["at"] == "2026-10-07T12:00:00+03:00"
        for call in agent.client.messages.stream.call_args_list:
            assert all(set(message) == {"role", "content"} for message in call.kwargs["messages"])
        last_messages = agent.client.messages.stream.call_args_list[1].kwargs["messages"]
        assert '"risk_level": "high"' in last_messages[0]["content"]
        assert '"risk_level": "moderate"' in last_messages[-1]["content"]

        cleared = {"horizon": None, "risk_level": None}
        await http.post("/v1/chat", json={
            "session_id": "session", "message": "Tercihsiz görünüm", "preferences": cleared,
        })
        context = agent.runner.memory_dump()["state"]["user_context"]
        assert context["horizon"] is None and context["risk_level"] is None
        await http.post("/v1/reset", json={"session_id": "session"})
        assert agent.runner.memory_dump()["recent_turns"] == []
        assert agent.runner._memory.state.is_empty()


async def test_failed_turn_retains_its_preferences_and_cannot_mutate_prior_snapshot(agent):
    agent.client.messages.stream.side_effect = [_answer(), RuntimeError("model unavailable")]
    for preferences in [ConversationPreferences("intraday", "high"), ConversationPreferences("swing", "low")]:
        _ = [event async for event in agent.runtime.chat("session", "İncele", preferences=preferences)]
    memory = agent.runner.memory_dump()
    assert memory["recent_turns"][0][0]["preferences"]["horizon"] == "intraday"
    assert memory["recent_turns"][1][0]["preferences"]["horizon"] == "swing"
    memory["recent_turns"][0][0]["preferences"]["horizon"] = "position"
    assert agent.runner.memory_dump()["recent_turns"][0][0]["preferences"]["horizon"] == "intraday"


async def test_invalid_memory_gets_one_correction_without_repeating_portfolio_write(agent):
    agent.client.messages.stream.side_effect = [_Stream(_buy()), _answer()]
    invalid = _message(ToolUseBlock(
        id="state", type="tool_use", name="update_state",
        input={"state": {}, "summary": "x" * 2001},
    ))
    valid = _message(ToolUseBlock(
        id="state", type="tool_use", name="update_state",
        input={"state": {}, "summary": "Alım kaydedildi."},
    ))
    agent.client.messages.create.side_effect = [invalid, valid]
    events = [event async for event in agent.runtime.chat(
        "session", "Bir lot alım kaydet", preferences=ConversationPreferences("swing", "low"),
    )]
    assert not any(event["type"] == "error" for event in events)
    assert _find_position(agent.repo, "THYAO").quantity == 1
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1
    assert agent.runner.memory_dump()["summary"] == "Alım kaydedildi."
    assert agent.runner.memory_dump()["state"]["user_context"]["risk_level"] == "low"
    assert agent.client.messages.create.await_count == 2
    correction = agent.client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "string_too_long" in correction


async def test_memory_correction_is_bounded_and_preserves_previous_state(agent):
    agent.runner._memory.apply({"state": {}, "summary": "Önceki geçerli hafıza"})
    agent.client.messages.stream.return_value = _answer()
    agent.client.messages.create.return_value = _message(ToolUseBlock(
        id="state", type="tool_use", name="update_state",
        input={"state": {}, "summary": "x" * 2001},
    ))
    events = await _consume(agent)
    assert events[-1]["type"] == "error"
    assert agent.client.messages.create.await_count == 2
    assert agent.runner.memory_dump()["summary"] == "Önceki geçerli hafıza"


async def test_models_and_thinking_survive_tool_loop_and_memory_update(agent):
    thinking = ThinkingBlock(type="thinking", thinking="", signature="signed-context")
    redacted = RedactedThinkingBlock(type="redacted_thinking", data="opaque")
    watchlist = []
    agent.runner._watchlist = SimpleNamespace(list=lambda: watchlist)

    async def read_and_refresh_watchlist(**kwargs):
        watchlist.append({"symbol": "ASELS", "name": "Aselsan"})
        return {"positions": []}

    agent.runner._tools["list_portfolio"].handler = read_and_refresh_watchlist
    call = ToolUseBlock(id="read", type="tool_use", name="list_portfolio", input={})
    agent.client.messages.stream.side_effect = [
        _Stream(thinking, redacted, call), _answer(), _answer(),
    ]
    agent.client.messages.create.return_value = _message(ToolUseBlock(
        id="state", type="tool_use", name="update_state",
        input={"state": {}, "summary": "Portföy incelendi."},
    ))
    await _consume(agent, "Portföyü göster")
    await _consume(agent, "Devam et")

    first, continuation, next_turn = [
        call.kwargs for call in agent.client.messages.stream.call_args_list
    ]
    assert first["model"] == "claude-sonnet-5-5"
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "medium"}
    assert continuation["system"] == first["system"]
    assert continuation["messages"][1]["content"][:2] == [
        thinking.model_dump(), redacted.model_dump(),
    ]
    assert "ASELS: Aselsan" in next_turn["system"][0]["text"]
    assert "Portföy incelendi." in next_turn["system"][1]["text"]
    historical_blocks = [
        block for message in next_turn["messages"]
        if isinstance(message["content"], list) for block in message["content"]
    ]
    assert {block["type"] for block in historical_blocks} == {"text", "tool_use", "tool_result"}
    state_request = agent.client.messages.create.call_args.kwargs
    assert state_request["model"] == "claude-haiku-5-5"
    assert state_request["thinking"] == {"type": "disabled"}
    assert state_request["tool_choice"] == {"type": "tool", "name": "update_state"}


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded", "pause_turn"])
async def test_incomplete_main_response_does_not_execute_or_replay_partial_tool(agent, stop_reason):
    partial = _Stream(_buy())
    partial.message.stop_reason = stop_reason
    agent.client.messages.stream.side_effect = [partial, _answer()]
    events = await _consume(agent)
    assert events[-1]["type"] == "error"
    assert _find_position(agent.repo, "THYAO") is None
    agent.client.messages.create.assert_not_awaited()
    await _consume(agent)
    messages = agent.client.messages.stream.call_args.kwargs["messages"]
    assert all(message["role"] == "user" for message in messages)


async def test_incomplete_state_response_preserves_existing_memory(agent):
    agent.runner._memory.summary = "Önceki hafıza"
    partial = _message(ToolUseBlock(
        id="state", type="tool_use", name="update_state",
        input={"state": {}, "summary": "Yarım güncelleme"},
    ))
    partial.stop_reason = "max_tokens"
    agent.client.messages.create.return_value = partial
    agent.client.messages.stream.return_value = _answer()
    events = await _consume(agent)
    assert events[-1]["type"] == "error"
    assert "hafıza" in events[-1]["message"]
    assert agent.runner.memory_dump()["summary"] == "Önceki hafıza"


async def test_http_retry_keeps_committed_result_and_does_not_buy_twice(agent):
    agent.client.messages.stream.side_effect = [
        _Stream(_buy()), ConnectionError("reply lost"),
        _Stream(_buy("new-id")), _answer(),
    ]

    @asynccontextmanager
    async def factory():
        yield agent.runtime

    app = create_app(factory)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test",
        ) as client:
            payload = {"session_id": "session", "message": "Bir lot alım kaydet"}
            first = await client.post("/v1/chat", json=payload)
            assert json.loads(first.text.splitlines()[-1])["type"] == "error"
            memory = await agent.runtime.memory("session")
            result = memory["recent_turns"][0][-1]["content"][0]
            assert json.loads(result["content"])["position"]["quantity"] == 1
            second = await client.post("/v1/chat", json=payload)
            assert json.loads(second.text.splitlines()[-1])["type"] == "text"

    assert _find_position(agent.repo, "THYAO").quantity == 1
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1
    retry_messages = agent.client.messages.stream.call_args_list[2].kwargs["messages"]
    assert retry_messages[2]["content"][0]["tool_use_id"] == "buy"


async def test_closing_stream_preserves_results_and_marks_unexecuted_calls(agent):
    agent.client.messages.stream.side_effect = [
        _Stream(_buy("first"), _buy("second", "ASELS")),
        _Stream(_buy("retry-first"), _buy("retry-second", "ASELS")), _answer(),
    ]
    stream = agent.runtime.chat("session", "Bir lot alım kaydet")
    assert (await anext(stream))["type"] == "tool"
    assert (await anext(stream))["type"] == "tool"
    await stream.aclose()

    memory = await agent.runtime.memory("session")
    results = memory["recent_turns"][0][-1]["content"]
    assert [result["tool_use_id"] for result in results] == ["first", "second"]
    assert [result["is_error"] for result in results] == [False, True]
    assert _find_position(agent.repo, "THYAO").quantity == 1
    assert _find_position(agent.repo, "ASELS") is None
    await _consume(agent)
    assert _find_position(agent.repo, "THYAO").quantity == _find_position(agent.repo, "ASELS").quantity == 1


async def test_cancellation_during_state_generation_keeps_write_and_propagates(agent):
    started = asyncio.Event()

    async def state(**kwargs):
        started.set()
        await asyncio.Event().wait()

    agent.client.messages.create.side_effect = state
    agent.client.messages.stream.side_effect = [_Stream(_buy()), _answer()]
    task = asyncio.create_task(_consume(agent))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert (await agent.runtime.memory("session"))["recent_turns"]
    agent.client.messages.create.side_effect = None
    agent.client.messages.stream.side_effect = [_Stream(_buy("retry")), _answer()]
    await _consume(agent)
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1


@pytest.mark.parametrize("new_intent", ["success", "different-message", "reset"])
async def test_new_intent_can_repeat_identical_trade(agent, new_intent):
    agent.client.messages.stream.side_effect = [
        _Stream(_buy()), _answer() if new_intent == "success" else ConnectionError("lost"),
    ]
    await _consume(agent)
    if new_intent == "reset":
        await agent.runtime.reset("session")
    agent.client.messages.stream.side_effect = [_Stream(_buy()), _answer()]
    await _consume(agent, "Bir lot daha alım kaydet" if new_intent == "different-message"
                   else "Bir lot alım kaydet")
    assert sum(p.quantity for p in agent.repo.list() if p.symbol == "THYAO") == 2


async def test_duplicate_write_in_one_turn_runs_once(agent):
    agent.client.messages.stream.side_effect = [
        _Stream(_buy("first"), _buy("duplicate")), _answer(),
    ]
    await _consume(agent)
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1


async def test_iteration_limit_keeps_retry_protection(agent, monkeypatch):
    monkeypatch.setattr("trader_agent.engine.agent.runner._MAX_TURN_ITERATIONS", 1)
    agent.client.messages.stream.side_effect = [_Stream(_buy()), _Stream(_buy("retry"))]
    assert (await _consume(agent))[-1]["type"] == "error"
    assert (await _consume(agent))[-1]["type"] == "error"
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1


async def test_read_tools_are_not_replayed_from_write_cache(agent):
    handler = AsyncMock(return_value={"positions": []})
    agent.runner._tools["list_portfolio"].handler = handler
    call = ToolUseBlock(id="read", type="tool_use", name="list_portfolio", input={})
    agent.client.messages.stream.side_effect = [_Stream(call), _Stream(call), _answer()]
    await _consume(agent)
    assert handler.await_count == 2


async def test_cancelled_write_with_unknown_outcome_is_not_repeated(agent):
    started = asyncio.Event()

    async def interrupted_write(**kwargs):
        agent.repo.buy(**kwargs)
        started.set()
        await asyncio.Event().wait()

    handler = AsyncMock(side_effect=interrupted_write)
    agent.runner._tools["buy_position"].handler = handler
    agent.client.messages.stream.side_effect = [_Stream(_buy())]
    task = asyncio.create_task(_consume(agent))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    agent.client.messages.stream.side_effect = [_Stream(_buy("retry")), _answer()]
    await _consume(agent)
    assert handler.await_count == 1
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1
    results = (await agent.runtime.memory("session"))["recent_turns"][-1][2]["content"]
    assert results[0]["is_error"] is True
    assert "Kayıtları kontrol" in results[0]["content"]


async def test_model_text_cannot_impersonate_a_tool_event(agent):
    marker = "\x00TOOL:clear_portfolio\x00"
    agent.client.messages.stream.return_value = _Stream(TextBlock(type="text", text=marker))
    assert await _consume(agent, "Bu işareti açıkla") == [{"type": "text", "content": marker}]


@pytest.mark.parametrize("blocks", [[], [ThinkingBlock(
    type="thinking", thinking="", signature="signature",
)]])
async def test_empty_response_is_not_success_or_added_to_history(agent, blocks):
    agent.client.messages.stream.return_value = _Stream(*blocks)
    events = await _consume(agent)
    assert events[-1]["type"] == "error"
    agent.client.messages.create.assert_not_awaited()
    assert all(message["role"] == "user"
               for message in agent.runner.memory_dump()["recent_turns"][0])


async def test_unsupported_model_content_is_not_silently_discarded(agent):
    stream = _answer()
    stream.message.content.append(ServerToolUseBlock(
        type="server_tool_use", id="server", name="web_search", input={"query": "test"},
    ))
    agent.client.messages.stream.return_value = stream
    events = await _consume(agent)
    assert events[-1]["type"] == "error"
    assert "Desteklenmeyen" in events[-1]["message"]
    agent.client.messages.create.assert_not_awaited()


@pytest.mark.parametrize("failure", ["api", "invalid", "missing", "duplicate"])
async def test_memory_failure_is_visible_and_retry_keeps_committed_write(agent, failure):
    agent.runner._memory.apply({"state": {}, "summary": "Önceki hafıza"})
    valid = agent.client.messages.create.return_value
    if failure == "api":
        agent.client.messages.create.side_effect = anthropic.APIConnectionError(
            request=httpx.Request("POST", "https://api.test/messages"),
        )
    elif failure == "invalid":
        agent.client.messages.create.return_value = _message(ToolUseBlock(
            id="state", type="tool_use", name="update_state",
            input={"state": {"unknown": "bad"}, "summary": "Geçersiz"},
        ))
    elif failure == "missing":
        agent.client.messages.create.return_value = _message(TextBlock(type="text", text="tamam"))
    else:
        agent.client.messages.create.return_value = _message(*valid.content, *valid.content)
    agent.client.messages.stream.side_effect = [_Stream(_buy()), _answer()]
    events = await _consume(agent)
    assert events[-1]["type"] == "error"
    assert "hafıza" in events[-1]["message"]
    assert agent.runner.memory_dump()["summary"] == "Önceki hafıza"

    agent.client.messages.create.side_effect = None
    agent.client.messages.create.return_value = valid
    agent.client.messages.stream.side_effect = [_Stream(_buy("retry")), _answer()]
    await _consume(agent)
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1
    prompt = agent.client.messages.create.call_args.kwargs["messages"][0]["content"]
    assert "Kayıt tamamlandı." in prompt


async def test_turn_deadline_cancels_tool_and_retains_retry_protection(agent, monkeypatch):
    async def blocked_write(**kwargs):
        agent.repo.buy(**kwargs)
        await asyncio.Event().wait()

    handler = AsyncMock(side_effect=blocked_write)
    agent.runner._tools["buy_position"].handler = handler
    stream = _Stream(_buy())
    agent.client.messages.stream.side_effect = [stream]
    timeout = asyncio.timeout
    with monkeypatch.context() as patch:
        # Sıfır süre: ilk gerçek await'te deterministik olarak iptal edilir.
        patch.setattr("trader_agent.engine.agent.runner.asyncio.timeout", lambda _: timeout(0))
        events = await _consume(agent)
    assert events[-1]["type"] == "error"
    assert "süre sınırı" in events[-1]["message"]
    assert stream.closed
    agent.client.messages.create.assert_not_awaited()
    agent.client.messages.stream.side_effect = [_Stream(_buy("retry")), _answer()]
    await _consume(agent)
    assert handler.await_count == 1
    assert len(agent.repo.transactions(_find_position(agent.repo, "THYAO").id)) == 1


async def test_memory_dump_does_not_expose_mutable_internal_history(agent):
    agent.client.messages.stream.side_effect = [_Stream(_buy()), _answer(), _answer()]
    await _consume(agent)
    snapshot = await agent.runtime.memory("session")
    snapshot["recent_turns"][0][1]["content"][0]["name"] = "clear_portfolio"
    snapshot["recent_turns"].clear()
    await _consume(agent, "Devam")
    messages = agent.client.messages.stream.call_args.kwargs["messages"]
    assert messages[1]["content"][0]["name"] == "buy_position"


@pytest.mark.parametrize("suffix", ["", " Sonraki metin."])
async def test_stream_preserves_initial_text_and_deltas_once_across_tool_calls(agent, suffix):
    prefix = "Veri 15 dakika gecikmeli."

    class PrefilledStream(_Stream):
        """Bazı stream yanıtlarının dolu text başlangıcını taklit ederek kayıp metni yakalar."""

        async def __aiter__(self):
            yield SimpleNamespace(
                type="content_block_start", content_block=TextBlock(type="text", text=prefix),
            )
            if suffix:
                yield SimpleNamespace(
                    type="content_block_delta", delta=SimpleNamespace(type="text_delta", text=suffix),
                )

    call = ToolUseBlock(id="read", type="tool_use", name="list_portfolio", input={})
    agent.client.messages.stream.side_effect = [
        PrefilledStream(TextBlock(type="text", text=prefix + suffix), call), _answer(),
    ]
    events = await _consume(agent, "Portföyümü göster")
    actual = "".join(event["content"] for event in events if event["type"] == "text")
    assert actual == prefix + suffix + "Kayıt tamamlandı."


def _find_position(repo, symbol):
    return next((p for p in repo.list() if p.symbol == symbol), None)


@pytest.mark.parametrize("change, valid", [
    ({}, True),
    ({"stop_loss": None}, False),
    ({"entry_max": 200}, False),
    ({"data_as_of": "2026-10-08T12:00:00+03:00"}, False),
    ({"user_id": "another-user", "producer": "model"}, False),
])
async def test_recommendation_stream_only_emits_validated_persisted_cards(agent, change, valid):
    payload = {"symbol": "THYAO", "entry_min": 100, "entry_max": 105,
               "stop_loss": 95, "target": 120, "horizon": "swing",
               "rationale": "Destek bölgesinden giriş planı.",
               "data_as_of": "2026-10-07T11:45:00+03:00", **change}
    agent.client.messages.stream.side_effect = [
        _Stream(ToolUseBlock(id="rec", type="tool_use", name="publish_recommendation", input=payload)),
        _answer(),
    ]
    events = await _consume(agent, "Uygun bir giriş planı öner.")
    cards = [event for event in events if event["type"] == "recommendation"]
    saved = agent.recommendations.list(agent.runner._now())
    assert len(cards) == len(saved) == int(valid)
    assert agent.repo.list() == []
    if valid:
        assert cards[0]["recommendation_id"] == saved[0].id
        assert saved[0].producer == "assistant"
        assert saved[0].entry_max == 105
