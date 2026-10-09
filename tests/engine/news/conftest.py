from datetime import datetime, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from trader_agent.engine.news.matching import CompanyMatcher
from trader_agent.engine.news.service import NewsService
from trader_agent.engine.news.types import NewsItem, Summary

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


@pytest.fixture
def article():
    return NewsItem("test", NOW, "Piyasa haberi", "İçerik", "https://test.example/1")


@pytest.fixture
def repo():
    return AsyncMock()


@pytest.fixture
def summarizer():
    return AsyncMock(summarize=AsyncMock(return_value=Summary("Özet.", "test-model")))


@pytest.fixture
def provider(article):
    return AsyncMock(list_recent=AsyncMock(return_value=[article]))


@pytest.fixture
def make_service(provider, summarizer, repo):
    def make(**overrides):
        options = dict(
            providers=[provider], summarizer=summarizer, repo=repo,
            matcher=CompanyMatcher({"THYAO": "Türk Hava Yolları", "ASELS": "Aselsan"}),
            timezone=ZoneInfo("Europe/Istanbul"), fetch_concurrency=2,
            digest_concurrency=2, job_timeout=30,
            max_input_chars=5000, now=lambda: NOW,
        )
        return NewsService(**(options | overrides))
    return make
