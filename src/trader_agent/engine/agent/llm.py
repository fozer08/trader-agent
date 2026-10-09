"""Agent'ın Anthropic içerik bloklarını oluşturma ve serileştirme yardımcıları."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from anthropic.types import ContentBlock, TextBlockParam

# Anthropic prompt caching breakpoint TTL'leri.
CacheTTL = Literal["5m", "1h"]


# ---- Block construction ------------------------------------------------------

def text_block(text: str, *, cache_ttl: CacheTTL | None = None) -> TextBlockParam:
    """Anthropic system veya content text bloğu üretir; opsiyonel cache breakpoint'i ekler.

    Cache prefix sırası: tools → system → messages. cache_ttl bir breakpoint
    yaratır ve önceki tüm prefix'i o TTL ile cache'ler.

    Örnek:
        text_block(static_prompt, cache_ttl="1h")
        text_block(memory_text, cache_ttl="5m")
    """
    block: TextBlockParam = {"type": "text", "text": text}
    if cache_ttl is not None:
        block["cache_control"] = {"type": "ephemeral", "ttl": cache_ttl}
    return block


def serialize_assistant_content(content_blocks: Sequence[ContentBlock]) -> list[dict[str, object]]:
    """Anthropic response content block'larını multi-turn history için dict listesine çevirir.

    SDK'nın ``final.content`` veya ``response.content`` objelerini turn_messages'a
    geri besleyebileceğin form'a indirger. text / tool_use / thinking /
    Desteklenmeyen bloklar eksik geçmiş oluşturmamak için reddedilir.
    """
    out: list[dict[str, object]] = []
    for block in content_blocks:
        if block.type == "text":
            out.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            out.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        elif block.type == "thinking":
            out.append({
                "type": "thinking",
                "thinking": block.thinking,
                "signature": block.signature,
            })
        elif block.type == "redacted_thinking":
            out.append({"type": "redacted_thinking", "data": block.data})
        else:
            raise RuntimeError(f"Desteklenmeyen model içerik bloğu: {block.type}")
    return out
