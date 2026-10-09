from __future__ import annotations

import asyncio
import json
from contextlib import aclosing
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Literal, TypedDict

import anthropic
from anthropic.types import TextBlockParam, ToolParam
from pydantic import ValidationError

from ...config.llm import LLMConfig
from ..llm import log_usage
from ..calendar import TradingCalendar
from ..repository.watchlist import WatchlistRepository
from ..tools.base import Tool
from ...utils.logging import get_logger
from .llm import serialize_assistant_content, text_block
from .context import build_context, build_system
from .memory import ConversationMemory, MemoryUpdate
from .prompts import STATE_PROMPT

_log = get_logger(__name__)

_UPDATE_STATE_TOOL = "update_state"
_MAX_TURN_ITERATIONS = 10


class TextEvent(TypedDict):
    type: Literal["text"]
    content: str


class ToolEvent(TypedDict):
    type: Literal["tool"]
    name: str


AgentEvent = TextEvent | ToolEvent


@dataclass(frozen=True)
class _ToolResult:
    content: str
    is_error: bool


@dataclass
class _InterruptedTurn:
    user_input: str
    write_results: dict[str, _ToolResult]


class AgentRunner:
    """Claude API ile streaming konuşma döngüsünü yöneten agent çalıştırıcısı.

    İki fazlı turn:
    1. Ana konuşma — text + analiz/portfolio tool'ları, kullanıcıya stream'lenir.
    2. State generation — ayrı (cheaper) API call, `update_state` forced tool_choice
       ile state ve summary'yi günceller; başarısızlığı açıkça bildirir.
    """

    def __init__(
        self,
        config: LLMConfig,
        watchlist: WatchlistRepository,
        calendar: TradingCalendar,
        exchange_name: str,
        tools: list[Tool],
        delay_minutes: int | None = None,
        output_format: str = "plain",
    ) -> None:
        self._config = config
        self._watchlist = watchlist
        self._calendar = calendar
        self._exchange_name = exchange_name
        self._delay_minutes = delay_minutes
        self._output_format = output_format
        self._tools = {t.name: t for t in tools}
        self._tool_schemas: list[ToolParam] = [
            {"name": t.name, "description": t.description, "input_schema": t.input_schema}
            for t in tools
        ]
        self._state_tool_schema: ToolParam = {
            "name": _UPDATE_STATE_TOOL,
            "description": "Konuşma hafızasını ve özetini güncelle.",
            "input_schema": MemoryUpdate.model_json_schema(),
        }
        self._memory = ConversationMemory()
        self._interrupted_turn: _InterruptedTurn | None = None
        self._client = anthropic.AsyncAnthropic(timeout=config.turn_timeout, max_retries=1)

    def reset(self) -> None:
        """State, summary ve recent turn'leri temizler."""
        self._memory = ConversationMemory()
        self._interrupted_turn = None

    def set_output_format(self, output_format: str) -> None:
        self._output_format = output_format

    async def aclose(self) -> None:
        await self._client.close()

    def memory_dump(self) -> dict:
        """Debug için: state + summary + recent turn'lerin tam içeriği."""
        return self._memory.dump()

    async def chat(self, user_input: str) -> AsyncGenerator[AgentEvent, None]:
        """Kullanıcı mesajına karşılık text ve tool bildirimi chunk'ları üretir.

        Phase 1 (stream'lenir): text yanıt + analiz/portfolio tool döngüsü.
        Phase 2 (silent): state-gen API call, hafıza güncellenir.
        """
        context = build_context(
            self._calendar, self._exchange_name, self._delay_minutes, self._now(),
        )
        framed_input = f"{context}\n\n{user_input}"
        turn_messages: list[dict] = [{"role": "user", "content": framed_input}]
        interrupted = self._interrupted_turn
        write_results = (
            interrupted.write_results
            if interrupted is not None and interrupted.user_input == user_input
            else {}
        )
        self._interrupted_turn = _InterruptedTurn(user_input, write_results)
        try:
            async with asyncio.timeout(self._config.turn_timeout):
                async with aclosing(self._stream_turn(turn_messages, write_results)) as events:
                    async for event in events:
                        yield event
                await self._generate_state(turn_messages)
                self._interrupted_turn = None
        except TimeoutError as exc:
            raise RuntimeError("Konuşma süre sınırına ulaştı; tamamlanamadı.") from exc
        finally:
            # Araçların kalıcı etkileri, yanıt veya state çağrısı kesilse de unutulmaz.
            self._memory.remember(turn_messages)

    async def _stream_turn(
        self, turn_messages: list[dict], write_results: dict[str, _ToolResult]
    ) -> AsyncGenerator[AgentEvent, None]:
        iterations = 0
        # Bir tool döngüsünde thinking imzalarının bağlandığı prefix sabit kalır.
        system = self._build_system_param()

        while iterations < _MAX_TURN_ITERATIONS:
            iterations += 1
            async with self._client.messages.stream(
                model=self._config.agent_model,
                system=system,
                messages=[{"role": message["role"], "content": message["content"]}
                          for message in self._memory.messages(turn_messages)],
                tools=self._tool_schemas,
                max_tokens=self._config.max_output_tokens,
                thinking={"type": "adaptive"},
                output_config={"effort": self._config.agent_effort},
            ) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and event.delta.type == "text_delta":
                        yield {"type": "text", "content": event.delta.text}
                final = await stream.get_final_message()

            log_usage(final, label="main")

            if final.stop_reason not in ("end_turn", "tool_use", "refusal"):
                raise RuntimeError(f"Model yanıtı tamamlanamadı: {final.stop_reason}")

            assistant_content = serialize_assistant_content(final.content)
            tool_use_blocks = [b for b in final.content if b.type == "tool_use"]
            if bool(tool_use_blocks) != (final.stop_reason == "tool_use") or not assistant_content:
                raise RuntimeError("Model yanıtı boş veya araç çağrıları tamamlanmamış.")
            if not tool_use_blocks and not any(
                block.type == "text" and block.text.strip() for block in final.content
            ):
                raise RuntimeError("Model okunabilir bir yanıt üretmedi.")
            turn_messages.append({"role": "assistant", "content": assistant_content})

            if not tool_use_blocks:
                break  # LLM cevabını verdi, ana faz tamam

            # Kesinti çoklu çağrının ortasında olsa da tüm tool_use blokları eşleşir.
            tool_results = [
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps({"error": "Konuşma kesildi; araç çalıştırılmadı."}),
                    "is_error": True,
                }
                for block in tool_use_blocks
            ]
            turn_messages.append({"role": "user", "content": tool_results})
            for block, tool_result in zip(tool_use_blocks, tool_results):
                yield {"type": "tool", "name": block.name}
                tool_result["content"] = json.dumps(
                    {"error": "Araç kesildi; sonucu doğrulanamadı. Kayıtları kontrol edin."}
                )
                result = await self._execute_tool(block.name, block.input, write_results)
                tool_result.update(content=result.content, is_error=result.is_error)
        else:
            _log.error(
                "chat turn %d iteration üst sınırına ulaştı; turn kapatılıyor",
                _MAX_TURN_ITERATIONS,
            )
            raise RuntimeError("Turn iteration üst sınırı aşıldı; konuşma tamamlanamadı.")

    async def _execute_tool(
        self, name: str, args: dict[str, object], write_results: dict[str, _ToolResult]
    ) -> _ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return _ToolResult(json.dumps({"error": f"Unknown tool: {name}"}), True)
        key = None
        try:
            if tool.mutates:
                key = json.dumps([name, args], sort_keys=True, allow_nan=False)
                if key in write_results:
                    return write_results[key]
                # İşlem await sırasında kesilirse sonucu bilinmeden tekrar yazılmaz.
                write_results[key] = _ToolResult(json.dumps({
                    "error": "Önceki işlem kesildi; tekrar uygulanmadı. Kayıtları kontrol edin."
                }, ensure_ascii=False), True)
            value = await tool.handler(**args)
            result = _ToolResult(
                json.dumps(value, ensure_ascii=False, allow_nan=False),
                isinstance(value, dict) and "error" in value,
            )
        except Exception as exc:
            result = _ToolResult(json.dumps({"error": str(exc)}, ensure_ascii=False), True)
        if key is not None:
            write_results[key] = result
        return result

    # ---- state generation -----------------------------------------------

    async def _generate_state(self, turn_messages: list[dict]) -> None:
        """Ayrı bir API çağrısı ile state ve summary'yi günceller."""
        try:
            response = await self._client.messages.create(
                model=self._config.light_model,
                # Breakpoint system'de → tools + system birlikte cache'lenir.
                system=[text_block(STATE_PROMPT, cache_ttl="1h")],
                messages=[{"role": "user", "content": self._memory.update_prompt(turn_messages)}],
                tools=[self._state_tool_schema],
                tool_choice={"type": "tool", "name": _UPDATE_STATE_TOOL},
                thinking={"type": "disabled"},
                max_tokens=self._config.max_output_tokens,
            )
        except anthropic.APIError as exc:
            raise RuntimeError("Yanıt üretildi ancak konuşma hafızası güncellenemedi.") from exc

        log_usage(response, label="state")
        updates = [block for block in response.content
                   if block.type == "tool_use" and block.name == _UPDATE_STATE_TOOL]
        if response.stop_reason != "tool_use" or len(updates) != 1:
            raise RuntimeError("Yanıt üretildi ancak hafıza güncellemesi tamamlanamadı.")
        try:
            self._memory.apply(updates[0].input)
        except ValidationError as exc:
            raise RuntimeError("Yanıt üretildi ancak hafıza güncellemesi geçersiz.") from exc

    def _build_system_param(self) -> list[TextBlockParam]:
        blocks: list[TextBlockParam] = [
            text_block(build_system(self._watchlist.list(), self._output_format), cache_ttl="1h"),
        ]
        memory = self._memory.system_context()
        if memory:
            blocks.append(text_block(memory, cache_ttl="5m"))
        return blocks

    def _now(self) -> datetime:
        return datetime.now(self._calendar.timezone)
