"""Ücretli, açıkça seçilen canlı eval. Deterministik suite'e dahil değildir.

Başarı: dört senaryoda tamamlanmış özet, gerekli olguların korunması ve yasak
çıktıların yokluğu. Bu küçük smoke eval semantik doğruluk garantisi vermez.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import anthropic
import pytest

from trader_agent.config.llm import LLMConfig
from trader_agent.config.news import NewsConfig
from trader_agent.engine.news.llm import AnthropicSummarizer
from trader_agent.engine.news.types import DigestInput, NewsItem

CASES = json.loads((Path(__file__).parents[1] / "fixtures/news/digest_eval.json").read_text())
pytestmark = pytest.mark.integration


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
async def test_live_digest_retains_facts_and_ignores_instructions(case):
    config = NewsConfig.load()
    model = LLMConfig.load().light_model
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    until = datetime(2026, 10, 9, tzinfo=timezone.utc)
    item = NewsItem("eval", since, case["title"], case["body"], "https://example.test/news")
    async with anthropic.AsyncAnthropic(timeout=config.llm_timeout, max_retries=0) as client:
        summarizer = AnthropicSummarizer(
            client, model=model, timeout=config.llm_timeout,
            max_output_tokens=config.max_output_tokens,
        )
        summary = await summarizer.summarize(
            DigestInput(case["symbol"], since, until, (item,)), f"eval-{case['id']}",
        )
    normalized = summary.text.casefold()
    for alternatives in case["required_any"]:
        assert any(value in normalized for value in alternatives), summary.text
    assert all(value not in normalized for value in case["forbidden"]), summary.text
