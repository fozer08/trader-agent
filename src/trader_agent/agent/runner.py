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
from ..utils.logging import get_logger
from .prompts import CONTEXT_PROMPT, FORMAT_PROMPTS, SYSTEM_PROMPT

_log = get_logger(__name__)


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
        self._tool_schemas = _build_tool_schemas(tools)
        self._history: list[dict] = []
        self._turn_user_times: list[datetime] = []
        self._turn_end_indices: list[int] = []
        self._last_chat_at: datetime | None = None
        self._client = anthropic.AsyncAnthropic(api_key=config.api_key)

    def reset(self) -> None:
        """Konuşma geçmişini temizler."""
        self._history.clear()
        self._turn_user_times.clear()
        self._turn_end_indices.clear()
        self._last_chat_at = None

    async def chat(self, user_input: str) -> AsyncGenerator[str, None]:
        """Kullanıcı mesajına karşılık text ve tool bildirimi chunk'ları üretir.

        Yields:
            str: Claude'dan gelen text delta'ları veya ``\\x00TOOL:name\\x00``
                 formatında tool çağrı bildirimleri.
        """
        now = self._now()
        if self._should_reset(now):
            self._trim_at_latest_gap()
        self._last_chat_at = now

        # Dinamik bağlam (saat, seans) user mesajına gömülür; system statik
        # kalır → 1h cache prefix'i dakika değişiminden bozulmaz. Eski turlar
        # history'de donmuş timestamp'leriyle kalır → 5m prefix de stabil.
        context_prompt = self._build_context_prompt()
        framed_input = f"{context_prompt}\n\n{user_input}" if context_prompt else user_input
        self._history.append({"role": "user", "content": framed_input})
        self._trim_history()
        user_idx = len(self._history) - 1

        while True:
            async with self._client.messages.stream(
                **self._message_params(self._config.heavy_model),
            ) as stream:
                async for event in stream:
                    if (
                        event.type == "content_block_delta"
                        and hasattr(event.delta, "text")
                    ):
                        yield event.delta.text

                final = await stream.get_final_message()

            _log_usage(final)

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

        self._prune_ephemeral_tools(user_idx)
        self._turn_user_times.append(now)
        self._turn_end_indices.append(len(self._history))

    # ---- private -------------------------------------------------------------

    def _should_reset(self, now: datetime) -> bool:
        threshold = self._config.history_trim_minutes
        if not threshold or self._last_chat_at is None:
            return False
        return (now - self._last_chat_at).total_seconds() >= threshold * 60

    def _message_params(self, model: str) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "system": self._build_system_param(),
            "messages": self._messages_with_cache_marker(),
            "tools": self._tool_schemas,
            "max_tokens": self._config.max_output_tokens,
        }
        if self._config.use_extended_thinking:
            params["thinking"] = {"type": "adaptive"}
        return params

    def _messages_with_cache_marker(self) -> list[dict]:
        """Son mesajın son içerik bloğuna 5m cache_control işaretler. Her
        request'te garanti bir 5m breakpoint olur; bir sonraki request
        (aynı session içinde) bu prefix'i cache'den okur."""
        if not self._history:
            return self._history
        last = self._history[-1]
        content = last["content"]
        marker = {"type": "ephemeral", "ttl": "5m"}
        if isinstance(content, str):
            new_content = [{"type": "text", "text": content, "cache_control": marker}]
        elif isinstance(content, list) and content:
            new_content = list(content)
            new_content[-1] = {**new_content[-1], "cache_control": marker}
        else:
            return self._history
        return [*self._history[:-1], {**last, "content": new_content}]

    def _build_system_param(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "text",
                "text": self._build_static_prompt(),
                "cache_control": {"type": "ephemeral", "ttl": "1h"},
            },
        ]

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
        )

    def _prune_ephemeral_tools(self, user_idx: int) -> None:
        """keep_in_history=False olan tool'ların tool_use ve karşılık gelen
        tool_result bloklarını history'den temizler → turlar arası cache
        prefix stabil kalır."""
        prune_ids: set[str] = set()
        for msg in self._history[user_idx:]:
            if msg["role"] != "assistant" or not isinstance(msg["content"], list):
                continue
            for block in msg["content"]:
                if block.get("type") != "tool_use":
                    continue
                tool = self._tools.get(block["name"])
                if tool is None or not tool.keep_in_history:
                    prune_ids.add(block["id"])

        if not prune_ids:
            return

        def is_pruned(block: dict) -> bool:
            if block.get("type") == "tool_use":
                return block.get("id") in prune_ids
            if block.get("type") == "tool_result":
                return block.get("tool_use_id") in prune_ids
            return False

        new_history = list(self._history[: user_idx + 1])
        for msg in self._history[user_idx + 1 :]:
            content = msg["content"]
            if not isinstance(content, list):
                new_history.append(msg)
                continue
            filtered = [b for b in content if not is_pruned(b)]
            if filtered:
                new_history.append({**msg, "content": filtered})

        self._history = new_history

    def _now(self) -> datetime:
        return datetime.now(self._session.timezone)

    def _format_instructions(self) -> str:
        format_name = (
            self._output_format
            if self._output_format in {"plain", "markdown", "telegram_html"}
            else "plain"
        )
        return FORMAT_PROMPTS[format_name].strip()

    def _trim_at_latest_gap(self) -> None:
        """Reset trigger çaldığında: history'deki en son ``keep_history_after_pause_minutes``
        üstü iç gap'in öncesini at → son tutarlı parça context olarak kalır.
        İç gap yoksa tüm history atılır."""
        gap_min = timedelta(minutes=self._config.keep_history_after_pause_minutes)
        times = self._turn_user_times
        ends = self._turn_end_indices

        cut_after: int | None = None
        for i in range(len(times) - 1, 0, -1):
            if (times[i] - times[i - 1]) >= gap_min:
                cut_after = i - 1
                break

        if cut_after is None:
            self._drop_turns(len(times))
            return
        self._drop_turns(cut_after + 1)

    def _trim_history(self) -> None:
        """Backstop: token bütçesini aşarsa en eski turun TÜM mesajlarını at
        (orphan tool_use/tool_result kalmaması için tek seferde tur bütünü).
        Pruning + gap-trim history'i küçük tuttuğu için pratikte tetiklenmez."""
        max_tokens = self._config.max_history_tokens
        if max_tokens <= 0:
            return
        while self._turn_end_indices and _estimate_tokens(self._history) > max_tokens:
            self._drop_turns(1)

    def _drop_turns(self, count: int) -> None:
        """En eski ``count`` turu (mesajları ve timestamp'leri) atar."""
        if count <= 0 or count > len(self._turn_end_indices):
            count = len(self._turn_end_indices)
        if count == 0:
            return
        drop_count = self._turn_end_indices[count - 1]
        self._history = self._history[drop_count:]
        self._turn_user_times = self._turn_user_times[count:]
        self._turn_end_indices = [idx - drop_count for idx in self._turn_end_indices[count:]]


def _render_prompt(template: str, **values: str) -> str:
    return Template(template.strip()).safe_substitute(values)


def _estimate_tokens(messages: list[dict]) -> int:
    """Kaba char/4 tahmini; sadece backstop trim eşiği için kullanılır."""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict):
                    text = block.get("text") or block.get("content") or ""
                    if isinstance(text, str):
                        total += len(text) // 4
    return total


def _build_tool_schemas(tools: list[Tool]) -> list[dict]:
    schemas = [t.to_api_dict() for t in tools]
    if schemas:
        schemas[-1]["cache_control"] = {"type": "ephemeral", "ttl": "1h"}
    return schemas


def _log_usage(message: Any) -> None:
    usage = getattr(message, "usage", None)
    if usage is None:
        return
    cc = getattr(usage, "cache_creation", None)
    write_5m = getattr(cc, "ephemeral_5m_input_tokens", 0) if cc else 0
    write_1h = getattr(cc, "ephemeral_1h_input_tokens", 0) if cc else 0
    _log.info(
        "usage input=%s output=%s cache_read=%s cache_write=%s (5m=%s 1h=%s)",
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
