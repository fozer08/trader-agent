"""Anthropic sınırı; servis ve sınıflandırma SDK'yı bilmez."""

import asyncio
from time import monotonic

import anthropic

from ...utils.logging import get_logger
from .digest import format_request
from .prompts import DIGEST_SYSTEM
from .types import DigestInput, NewsError, Summary

_log = get_logger(__name__)


class AnthropicSummarizer:
    def __init__(
        self, client: anthropic.AsyncAnthropic, *, model: str, timeout: float,
        max_output_tokens: int,
    ) -> None:
        self._client = client
        self._model = model
        self._timeout = timeout
        self._max_output_tokens = max_output_tokens

    async def summarize(self, request: DigestInput, job_id: str) -> Summary:
        started = monotonic()
        scope = request.scope_key or "market"
        try:
            async with asyncio.timeout(self._timeout):
                response = await self._client.messages.create(
                    model=self._model, system=DIGEST_SYSTEM,
                    messages=[{"role": "user", "content": format_request(request)}],
                    max_tokens=self._max_output_tokens,
                    thinking={"type": "disabled"},
                    output_config={"effort": "low"},
                )
        except (anthropic.APIError, TimeoutError) as exc:
            _log.warning(
                "news LLM failed job=%s scope=%s model=%s error=%s elapsed=%.3f",
                job_id, scope, self._model, type(exc).__name__, monotonic() - started,
            )
            raise NewsError(f"Haber özeti üretilemedi: {scope}") from exc
        _log.info(
            "news LLM job=%s scope=%s model=%s input=%d output=%d "
            "elapsed=%.3f stop=%s",
            job_id, scope, response.model, response.usage.input_tokens,
            response.usage.output_tokens, monotonic() - started, response.stop_reason,
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if response.stop_reason != "end_turn" or not text:
            raise NewsError(f"Haber özeti boş veya tamamlanmamış: {scope}")
        return Summary(text=text, model=response.model)
