from __future__ import annotations

import json
from datetime import datetime, timedelta
from string import Template
from typing import Any, AsyncGenerator

import anthropic
from pydantic import BaseModel, Field, ValidationError

from ..config.llm import LLMConfig
from ..market.base import TradingSession
from ..market.helpers import is_weekday
from ..tools.base import Tool
from ..utils.logging import get_logger
from .prompts import CONTEXT_PROMPT, FORMAT_PROMPTS, STATE_PROMPT, SYSTEM_PROMPT
from .state import AgentState

_log = get_logger(__name__)

_UPDATE_STATE_TOOL = "update_state"
_RECENT_TURNS_LIMIT = 2
_MAX_TURN_ITERATIONS = 10  # ana konuşma döngüsü için runaway koruması
_TOOL_RESULT_TRUNCATE = 2000  # state-gen transcript'inde tool result başına byte sınırı


class _UpdateStateArgs(BaseModel):
    """update_state tool argümanlarını valide eden iç model."""

    state: AgentState
    summary: str = Field(max_length=2000)


class AgentRunner:
    """Claude API ile streaming konuşma döngüsünü yöneten agent çalıştırıcısı.

    İki fazlı turn:
    1. Ana konuşma — text + analiz/portfolio tool'ları, kullanıcıya stream'lenir.
    2. State generation — ayrı (cheaper) API call, `update_state` forced tool_choice
       ile state ve summary'yi günceller. Kullanıcıya yansımaz.
    """

    def __init__(
        self,
        config: LLMConfig,
        watchlist: list[dict],
        session: TradingSession,
        tools: list[Tool],
        delay_minutes: int | None = None,
        output_format: str = "plain",
    ) -> None:
        self._config = config
        self._watchlist = watchlist
        self._session = session
        self._delay_minutes = delay_minutes
        self._output_format = output_format
        self._tools = {t.name: t for t in tools}
        self._tool_schemas = _build_tool_schemas(tools)
        self._state_tool_schema = _update_state_tool_schema()
        self._state = AgentState()
        self._summary = ""
        self._recent_turns: list[list[dict]] = []
        self._client = anthropic.AsyncAnthropic(api_key=config.api_key)

    def reset(self) -> None:
        """State, summary ve recent turn'leri temizler."""
        self._state = AgentState()
        self._summary = ""
        self._recent_turns.clear()

    def memory_dump(self) -> dict:
        """Debug için: state + summary + recent turn'lerin tam içeriği."""
        return {
            "state": self._state.model_dump(mode="json"),
            "summary": self._summary,
            "recent_turns": self._recent_turns,
        }

    async def chat(self, user_input: str) -> AsyncGenerator[str, None]:
        """Kullanıcı mesajına karşılık text ve tool bildirimi chunk'ları üretir.

        Phase 1 (stream'lenir): text yanıt + analiz/portfolio tool döngüsü.
        Phase 2 (silent): state-gen API call, hafıza güncellenir.
        """
        framed_input = f"{self._build_context_prompt()}\n\n{user_input}"
        turn_messages: list[dict] = [{"role": "user", "content": framed_input}]
        iterations = 0

        while iterations < _MAX_TURN_ITERATIONS:
            iterations += 1
            params = self._main_message_params(turn_messages)

            async with self._client.messages.stream(**params) as stream:
                async for event in stream:
                    if event.type == "content_block_delta" and hasattr(event.delta, "text"):
                        yield event.delta.text
                final = await stream.get_final_message()

            _log_usage(final, label="main")

            assistant_content: list[dict] = []
            tool_use_blocks = []
            for block in final.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": block.id,
                            "name": block.name,
                            "input": block.input,
                        }
                    )
                    tool_use_blocks.append(block)
                elif block.type == "thinking":
                    assistant_content.append(
                        {
                            "type": "thinking",
                            "thinking": block.thinking,
                            "signature": block.signature,
                        }
                    )
                elif block.type == "redacted_thinking":
                    assistant_content.append(
                        {"type": "redacted_thinking", "data": block.data}
                    )

            turn_messages.append({"role": "assistant", "content": assistant_content})

            if not tool_use_blocks:
                break  # LLM cevabını verdi, ana faz tamam

            tool_results = []
            for block in tool_use_blocks:
                yield f"\x00TOOL:{block.name}\x00"
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

            turn_messages.append({"role": "user", "content": tool_results})
        else:
            _log.error(
                "chat turn %d iteration üst sınırına ulaştı; turn kapatılıyor",
                _MAX_TURN_ITERATIONS,
            )
            yield "\n\n[Sistem: turn iteration üst sınırı aşıldı.]"

        # Phase 2: state generation (silent, errors logged but tolerated)
        await self._generate_state(turn_messages)
        self._push_recent_turn(turn_messages)

    # ---- state generation -----------------------------------------------

    async def _generate_state(self, turn_messages: list[dict]) -> None:
        """Ayrı bir API çağrısı ile state ve summary'yi günceller."""
        prev_state_json = (
            self._state.model_dump_json(indent=2)
            if not self._state.is_empty()
            else "(boş)"
        )
        prev_summary = self._summary or "(boş)"
        transcript = _format_turn_transcript(turn_messages)
        user_msg = (
            f"## Mevcut State\n{prev_state_json}\n\n"
            f"## Geçmiş Özet\n{prev_summary}\n\n"
            f"## Son Turn Konuşması\n{transcript}\n\n"
            "---\nYukarıdaki konuşmaya göre state'i güncelle ve summary yaz. "
            "`update_state` tool'unu çağır."
        )

        try:
            response = await self._client.messages.create(
                model=self._config.light_model,
                system=[
                    {
                        "type": "text",
                        "text": STATE_PROMPT,
                        # Breakpoint system'de → tools + system birlikte cache'lenir.
                        "cache_control": {"type": "ephemeral", "ttl": "1h"},
                    }
                ],
                messages=[{"role": "user", "content": user_msg}],
                tools=[self._state_tool_schema],
                tool_choice={"type": "tool", "name": _UPDATE_STATE_TOOL},
                max_tokens=self._config.max_output_tokens,
            )
        except Exception as exc:
            _log.warning("state generation API call failed: %s", exc, exc_info=True)
            return

        _log_usage(response, label="state")

        for block in response.content:
            if block.type == "tool_use" and block.name == _UPDATE_STATE_TOOL:
                self._handle_state_update(block.input)
                return

        _log.warning("state generation: response içinde update_state tool_use yok")

    def _handle_state_update(self, args: dict) -> None:
        """update_state args'ını valide eder ve state'i günceller."""
        try:
            parsed = _UpdateStateArgs.model_validate(args)
        except ValidationError as exc:
            _log.warning("state-gen validation failed: %s", exc)
            return
        self._state = parsed.state
        self._summary = parsed.summary

    # ---- main message construction --------------------------------------

    def _main_message_params(self, turn_messages: list[dict]) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": self._config.agent_model,
            "system": self._build_system_param(),
            "messages": self._build_messages(turn_messages),
            "tools": self._tool_schemas,
            "max_tokens": self._config.max_output_tokens,
        }
        if self._config.use_extended_thinking:
            params["thinking"] = {"type": "adaptive"}
        return params

    def _build_messages(self, turn_messages: list[dict]) -> list[dict]:
        msgs: list[dict] = []
        for turn in self._recent_turns:
            msgs.extend(turn)
        msgs.extend(turn_messages)
        return msgs

    def _build_system_param(self) -> list[dict[str, Any]]:
        blocks: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": self._build_static_prompt(),
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            }
        ]
        memory = self._serialize_memory()
        if memory:
            blocks.append(
                {
                    "type": "text",
                    "text": memory,
                    "cache_control": {"type": "ephemeral", "ttl": "5m"},
                }
            )
        return blocks

    def _serialize_memory(self) -> str:
        """State + summary'yi system prompt'a injekte edilecek metne çevirir."""
        if self._state.is_empty() and not self._summary:
            return ""
        parts = []
        if not self._state.is_empty():
            parts.append("## Hafıza — State\n" + self._state.model_dump_json(indent=2))
        if self._summary:
            parts.append("## Hafıza — Geçmiş Özet\n" + self._summary)
        return "\n\n".join(parts)

    def _build_static_prompt(self) -> str:
        watchlist_lines = "\n".join(
            f"  {e['symbol']}: {e.get('name', e['symbol'])}"
            for e in self._watchlist
        )
        return _render_prompt(
            SYSTEM_PROMPT,
            watchlist_count=str(len(self._watchlist)),
            watchlist_lines=watchlist_lines,
            format_instructions=self._format_instructions(),
        )

    def _build_context_prompt(self) -> str:
        now = self._now()
        weekday = is_weekday(now)
        session_open = (
            weekday
            and now.time() >= self._session.start
            and now.time() < self._session.end
        )
        session_status = (
            f"AÇIK ({self._session.start.strftime('%H:%M')}–{self._session.end.strftime('%H:%M')})"
            if session_open
            else f"KAPALI (seans saatleri: {self._session.start.strftime('%H:%M')}–{self._session.end.strftime('%H:%M')})"
        )
        session_phase = _session_phase(now, self._session, weekday, session_open)
        session_timing = ""
        if session_open:
            session_end = datetime.combine(now.date(), self._session.end, tzinfo=now.tzinfo)
            session_timing = f"Seans Kapanışına Kalan: {_format_remaining(session_end - now)}"

        feed_delay = (
            f"Veri Gecikmesi: {self._delay_minutes} dakika"
            if self._delay_minutes and self._delay_minutes >= 5
            else ""
        )

        day_names = {0: "Pazartesi", 1: "Salı", 2: "Çarşamba", 3: "Perşembe", 4: "Cuma", 5: "Cumartesi", 6: "Pazar"}
        day_name = day_names[now.weekday()]

        return _render_prompt(
            CONTEXT_PROMPT,
            day_name=day_name,
            date=now.strftime("%d.%m.%Y"),
            time=now.strftime("%H:%M"),
            session_status=session_status,
            session_phase=session_phase,
            session_timing=session_timing,
            feed_delay=feed_delay,
        )

    # ---- recent turns ----------------------------------------------------

    def _push_recent_turn(self, turn_messages: list[dict]) -> None:
        self._recent_turns.append(turn_messages)
        if len(self._recent_turns) > _RECENT_TURNS_LIMIT:
            self._recent_turns = self._recent_turns[-_RECENT_TURNS_LIMIT:]

    def _now(self) -> datetime:
        return datetime.now(self._session.timezone)

    def _format_instructions(self) -> str:
        format_name = (
            self._output_format
            if self._output_format in {"plain", "markdown", "telegram_html"}
            else "plain"
        )
        return FORMAT_PROMPTS[format_name].strip()


# ---- helpers ----------------------------------------------------------------

def _format_turn_transcript(turn_messages: list[dict]) -> str:
    """turn_messages'ı state-gen LLM için okunabilir transcript'e çevirir.

    Tool result'lar büyük olabilir (örn. scan 30 sembol) — başına ``_TOOL_RESULT_TRUNCATE``
    byte sınırı koyuluyor. State çıkarımı için tipik olarak özet bilgi yeter.
    """
    parts = []
    for msg in turn_messages:
        role = msg.get("role", "?")
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(f"## {role.upper()}\n{content}")
            continue
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            t = block.get("type")
            if t == "text":
                parts.append(f"## {role.upper()} (text)\n{block.get('text', '')}")
            elif t == "tool_use":
                input_str = json.dumps(block.get("input", {}), ensure_ascii=False)
                parts.append(
                    f"## {role.upper()} (tool_use: {block.get('name')})\nInput: {input_str}"
                )
            elif t == "tool_result":
                raw = block.get("content", "")
                if isinstance(raw, str) and len(raw) > _TOOL_RESULT_TRUNCATE:
                    raw = raw[:_TOOL_RESULT_TRUNCATE] + "... [truncated]"
                parts.append(f"## TOOL_RESULT\n{raw}")
    return "\n\n".join(parts)


def _render_prompt(template: str, **values: str) -> str:
    return Template(template.strip()).safe_substitute(values)


def _build_tool_schemas(tools: list[Tool]) -> list[dict]:
    # Tools'a ayrı cache breakpoint koymuyoruz: ``_build_system_param``'daki
    # ilk system block'unun cache_control'ü zaten cumulative olarak tools'u kapsar
    # (cache prefix sırası: tools → system → messages).
    return [t.to_api_dict() for t in tools]


def _update_state_tool_schema() -> dict:
    """update_state tool şeması; AgentState'in $defs'i input_schema root'una taşınır."""
    state_schema = AgentState.model_json_schema()
    defs = state_schema.pop("$defs", {})
    return {
        "name": _UPDATE_STATE_TOOL,
        "description": (
            "Agent state'inin yeni tam halini ve son turn'ün narrative özetini sun."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "state": state_schema,
                "summary": {
                    "type": "string",
                    "maxLength": 2000,
                    "description": "Son turn'ün narrative özeti (Türkçe, max ~300 kelime).",
                },
            },
            "required": ["state", "summary"],
            "$defs": defs,
        },
    }


def _log_usage(message: Any, label: str = "") -> None:
    usage = getattr(message, "usage", None)
    if usage is None:
        return
    cc = getattr(usage, "cache_creation", None)
    write_5m = getattr(cc, "ephemeral_5m_input_tokens", 0) if cc else 0
    write_1h = getattr(cc, "ephemeral_1h_input_tokens", 0) if cc else 0
    _log.info(
        "usage[%s] input=%s output=%s cache_read=%s cache_write=%s (5m=%s 1h=%s)",
        label,
        getattr(usage, "input_tokens", 0),
        getattr(usage, "output_tokens", 0),
        getattr(usage, "cache_read_input_tokens", 0),
        getattr(usage, "cache_creation_input_tokens", 0),
        write_5m,
        write_1h,
    )


def _session_phase(now: datetime, session: TradingSession, weekday: bool, session_open: bool) -> str:
    """Şu anki anı seans fazına eşler.

    Faz etiketleri prompt'taki "Seans-Bilinçli Davranış" bölümüyle eşleşir.
    """
    if not weekday:
        return "hafta sonu"
    if not session_open:
        return "pre-market" if now.time() < session.start else "post-market"

    session_start = datetime.combine(now.date(), session.start, tzinfo=now.tzinfo)
    session_end = datetime.combine(now.date(), session.end, tzinfo=now.tzinfo)
    if now - session_start < timedelta(minutes=30):
        return "açılış (ilk 30 dk)"
    if session_end - now <= timedelta(hours=1):
        return "kapanışa yaklaşıyor (son 1 saat)"
    return "orta seans"


def _format_remaining(delta: timedelta) -> str:
    total_minutes = max(0, int(delta.total_seconds() // 60))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} saat {minutes} dakika"
    if hours:
        return f"{hours} saat"
    return f"{minutes} dakika"
