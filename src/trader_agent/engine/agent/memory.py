"""Konuşma hafızası; model güncellemesini doğrular, sınırlı geçmişi saklar."""

from copy import deepcopy
import json

from pydantic import BaseModel, ConfigDict, Field

from .state import AgentState

_RECENT_TURNS_LIMIT = 2
_TOOL_RESULT_TRUNCATE = 2000


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: AgentState
    summary: str = Field(max_length=2000)


class ConversationMemory:
    def __init__(self) -> None:
        self.state = AgentState()
        self.summary = ""
        self.recent_turns: list[list[dict]] = []

    def dump(self) -> dict:
        return {
            "state": self.state.model_dump(mode="json"),
            "summary": self.summary,
            "recent_turns": deepcopy(self.recent_turns),
        }

    def messages(self, turn: list[dict]) -> list[dict]:
        return [message for previous in self.recent_turns for message in previous] + turn

    def system_context(self) -> str:
        parts = []
        if not self.state.is_empty():
            parts.append("## Hafıza — State\n" + self.state.model_dump_json(indent=2))
        if self.summary:
            parts.append("## Hafıza — Geçmiş Özet\n" + self.summary)
        return "\n\n".join(parts)

    def update_prompt(self, turn: list[dict]) -> str:
        state = self.state.model_dump_json(indent=2)
        # Önceki güncelleme başarısızsa eldeki yakın konuşmalar da hesaba katılır.
        transcript = format_transcript(self.messages(turn))
        return (
            f"## Mevcut State\n{state}\n\n"
            f"## Geçmiş Özet\n{self.summary or '(boş)'}\n\n"
            f"## Yakın Konuşmalar\n{transcript}\n\n"
            "Konuşmalara göre state'i güncelle ve summary yaz. `update_state` tool'unu çağır."
        )

    def apply(self, value: object) -> None:
        update = MemoryUpdate.model_validate(value)
        self.state, self.summary = update.state, update.summary

    def remember(self, turn: list[dict]) -> None:
        recent = []
        for message in turn:
            content = message["content"]
            if isinstance(content, list):
                # İmzalı bloklar aynı araç döngüsünde korunur; yeni prefix'e taşınmaz.
                content = [block for block in content
                           if block["type"] not in ("thinking", "redacted_thinking")]
                if not content:
                    continue
            recent.append({**message, "content": content})
        self.recent_turns.append(recent)
        self.recent_turns = self.recent_turns[-_RECENT_TURNS_LIMIT:]


def format_transcript(turn_messages: list[dict]) -> str:
    """turn_messages'ı state-gen LLM için okunabilir transcript'e çevirir.

    Tool result'lar büyük olabilir (örn. scan 30 sembol) — başına ``_TOOL_RESULT_TRUNCATE``
    karakter sınırı koyuluyor. State çıkarımı için tipik olarak özet bilgi yeter.
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


