"""Agent ve haber servisinin paylaştığı Anthropic yanıt yardımcıları."""

from __future__ import annotations

from anthropic.types import Message

from ..utils.logging import get_logger

_log = get_logger(__name__)


def log_usage(message: Message, label: str = "") -> None:
    """Model, yanıt kimliği ve token kullanımını payload olmadan loglar."""
    usage = message.usage
    cc = usage.cache_creation
    write_5m = cc.ephemeral_5m_input_tokens if cc else 0
    write_1h = cc.ephemeral_1h_input_tokens if cc else 0
    _log.info(
        "usage[%s] model=%s message=%s input=%s output=%s cache_read=%s cache_write=%s (5m=%s 1h=%s)",
        label, message.model, message.id,
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_read_input_tokens or 0,
        usage.cache_creation_input_tokens or 0,
        write_5m,
        write_1h,
    )
