from datetime import timedelta

import httpx
import pytest

from trader_agent.engine.news.rss import RssNewsProvider
from trader_agent.engine.news.types import NewsError
from .conftest import NOW


def feed(title="Başlık", date="Fri, 09 Oct 2026 15:00:00 +0300"):
    return (f'<rss><channel><item><title>{title}</title>'
            '<description>&lt;b&gt;Haber&lt;/b&gt;</description>'
            '<link>https://example.test/news</link>'
            f'<pubDate>{date}</pubDate></item></channel></rss>').encode()


async def test_parses_rss_and_filters_dates():
    async with RssNewsProvider(
        "test", "https://example.test/rss", timeout=5, max_bytes=10000,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=feed())),
    ) as provider:
        items = await provider.list_recent(NOW - timedelta(days=1), NOW)
        assert len(items) == 1
        assert items[0].published_at == NOW
        assert items[0].body == "Haber"
        assert await provider.list_recent(NOW - timedelta(days=2), NOW - timedelta(days=1)) == []


@pytest.mark.parametrize("payload", [
    b'<html>not rss</html>', b'<rss>', feed(date='bad'), feed(title=''),
    b'<!DOCTYPE rss [<!ENTITY x "payload">]><rss><channel>&x;</channel></rss>',
    '<!DOCTYPE rss [<!ENTITY x "payload">]><rss><channel>&x;</channel></rss>'.encode('utf-16'),
    feed(date='Fri, 09 Oct 2026 15:00:00 -0000'),
])
async def test_malformed_feed_fails_explicitly(payload):
    async with RssNewsProvider(
        "test", "https://example.test/rss", timeout=5, max_bytes=10000,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=payload)),
    ) as provider:
        with pytest.raises(NewsError):
            await provider.list_recent(NOW - timedelta(days=1), NOW)


async def test_feed_size_limit():
    async with RssNewsProvider(
        "test", "https://example.test/rss", timeout=5, max_bytes=20,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=feed())),
    ) as provider:
        with pytest.raises(NewsError, match="boyut sınırı"):
            await provider.list_recent(NOW - timedelta(days=1), NOW)


@pytest.mark.parametrize("status,expected_calls", [(503, 2), (429, 2), (401, 1), (404, 1)])
async def test_only_transient_http_errors_are_retried(status, expected_calls, monkeypatch):
    calls = 0

    async def sleep(delay):
        assert delay == 0.25

    monkeypatch.setattr("trader_agent.engine.news.rss.asyncio.sleep", sleep)

    def handle(request):
        nonlocal calls
        calls += 1
        return httpx.Response(status)

    async with RssNewsProvider(
        "test", "https://example.test/rss?secret=private", timeout=5, max_bytes=10000,
        transport=httpx.MockTransport(handle),
    ) as provider:
        with pytest.raises(NewsError) as exc:
            await provider.list_recent(NOW - timedelta(days=1), NOW)
        assert calls == expected_calls
        assert "private" not in str(exc.value)
    assert provider._client.is_closed
