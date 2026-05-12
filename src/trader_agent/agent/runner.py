from __future__ import annotations

import json
from datetime import datetime, timedelta
from string import Template
from typing import Any, AsyncGenerator

import anthropic

from ..config.llm import LLMConfig
from ..market.helpers import is_weekday
from ..market.types import TradingSession
from ..tools.base import Tool
from .prompts import CONTEXT_PROMPT, FORMAT_PROMPTS, SYSTEM_PROMPT


class AgentRunner:
    """Claude API ile streaming konuşma döngüsünü yöneten agent çalıştırıcısı.

    Tool use bloklarını yakalar, ilgili handler'ı çalıştırır ve sonucu
    Claude'a geri göndererek döngüyü tamamlar. ``chat()`` bir async
    generator döndürür; text chunk'ları ve ``[tool_name]`` bildirimleri
    karışık olarak yield edilir.
    """

    def __init__(
        self,
        config: LLMConfig,
        watchlist: list[dict],
        session: TradingSession,
        tools: list[Tool],
        output_format: str = "plain",
    ) -> None:
        self._config = config
        self._watchlist = watchlist
        self._session = session
        self._output_format = output_format
        self._tools = {t.name: t for t in tools}
        self._tool_schemas = [t.to_api_dict() for t in tools]
        self._history: list[dict] = []
        self._client = anthropic.AsyncAnthropic(api_key=config.api_key)

    def reset(self) -> None:
        """Konuşma geçmişini temizler."""
        self._history.clear()

    async def chat(self, user_input: str) -> AsyncGenerator[str, None]:
        """Kullanıcı mesajına karşılık text ve tool bildirimi chunk'ları üretir.

        Yields:
            str: Claude'dan gelen text delta'ları veya ``\\x00TOOL:name\\x00``
                 formatında tool çağrı bildirimleri.
        """
        self._history.append({"role": "user", "content": user_input})
        self._trim_history()

        tool_calls_made = False
        while True:
            model = self._config.heavy_model if tool_calls_made else self._config.light_model
            async with self._client.messages.stream(
                **self._message_params(model),
            ) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and hasattr(event.delta, "text")
                    ):
                        yield event.delta.text

                final = await stream.get_final_message()

            assistant_content = []
            tool_use_blocks = []
            for block in final.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    yield f"\x00TOOL:{block.name}\x00"
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    tool_use_blocks.append(block)

            self._history.append({"role": "assistant", "content": assistant_content})

            if final.stop_reason != "tool_use":
                break

            tool_calls_made = True
            tool_results = []
            for block in tool_use_blocks:
                tool = self._tools.get(block.name)
                if tool is None:
                    result = {"error": f"Unknown tool: {block.name}"}
                else:
                    try:
                        result = await tool.handler(**block.input)
                    except Exception as exc:
                        result = {"error": str(exc)}
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )

            self._history.append({"role": "user", "content": tool_results})

    # ---- private -------------------------------------------------------------

    def _message_params(self, model: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "system": self._build_system_param(),
            "messages": self._history,
            "tools": self._tool_schemas,
            "max_tokens": self._config.max_tokens,
        }
        return params

    def _build_system_prompt(self) -> str:
        return "\n\n".join(self._build_system_text_parts())

    def _build_system_param(self) -> str | list[dict[str, Any]]:
        static_prompt, context_prompt = self._build_system_text_parts()
        if not self._config.use_prompt_caching:
            return "\n\n".join([static_prompt, context_prompt])
        return [
            {
                "type": "text",
                "text": static_prompt,
                "cache_control": {"type": "ephemeral"},
            },
            {
                "type": "text",
                "text": context_prompt,
            },
        ]

    def _build_system_text_parts(self) -> tuple[str, str]:
        now = self._now()
        session_open = (
            is_weekday(now)
            and now.time() >= self._session.start
            and now.time() < self._session.end
        )
        session_timing = ""
        session_status = (
            f"AÇIK ({self._session.start.strftime('%H:%M')}–{self._session.end.strftime('%H:%M')})"
            if session_open
            else f"KAPALI (seans saatleri: {self._session.start.strftime('%H:%M')}–{self._session.end.strftime('%H:%M')})"
        )
        if session_open:
            session_end = datetime.combine(now.date(), self._session.end, tzinfo=now.tzinfo)
            session_timing = f"Seans Kapanışına Kalan: {_format_remaining(session_end - now)}"

        day_names = {0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe", 4: "Cuma", 5: "Cumartesi", 6: "Pazar"}
        day_name = day_names[now.weekday()]

        watchlist_lines = "\n".join(
            f"  {e['symbol']}: {e.get('name', e['symbol'])}"
            for e in self._watchlist
        )

        static_prompt = _render_prompt(
            SYSTEM_PROMPT,
            watchlist_count=str(len(self._watchlist)),
            watchlist_lines=watchlist_lines,
            format_instructions=self._format_instructions(),
        )
        context_prompt = _render_prompt(
            CONTEXT_PROMPT,
            day_name=day_name,
            date=now.strftime("%d.%m.%Y"),
            time=now.strftime("%H:%M"),
            session_status=session_status,
            session_timing=session_timing,
        )
        return static_prompt, context_prompt

    def _now(self) -> datetime:
        return datetime.now(self._session.timezone)

    def _format_instructions(self) -> str:
        format_name = (
            self._output_format
            if self._output_format in {"plain", "markdown", "telegram_html"}
            else "plain"
        )
        return FORMAT_PROMPTS[format_name].strip()

    def _trim_history(self) -> None:
        max_n = self._config.max_conversation_history
        if len(self._history) > max_n:
            self._history = self._history[-max_n:]
        # Tool result mesajları da role="user" taşır ama content bir liste olur.
        # API konuşmanın gerçek bir kullanıcı metin mesajıyla başlamasını gerektirir.
        while self._history:
            first = self._history[0]
            if first["role"] == "user" and isinstance(first["content"], str):
                break
            self._history.pop(0)


def _render_prompt(template: str, **values: str) -> str:
    return Template(template.strip()).safe_substitute(values)


def _format_remaining(delta: timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} saat {minutes} dakika"
    if hours:
        return f"{hours} saat"
    return f"{minutes} dakika"
