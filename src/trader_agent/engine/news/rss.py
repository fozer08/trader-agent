from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree import ElementTree as ET

import httpx

from .types import NewsError, NewsItem

_HTML_TAG_RE = re.compile(r"<[^>]+>")


class _RssTreeBuilder(ET.TreeBuilder):
    def doctype(self, name: str, pubid: str | None, system: str | None) -> None:
        raise NewsError("RSS içinde DTD/entity tanımı kabul edilmez.")


class RssNewsProvider:
    """Sınırlı RSS 2.0 okuması; sınıflandırma ve özetleme servise aittir."""

    def __init__(
        self, name: str, url: str, *, timeout: float, max_bytes: int,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.name = name
        self._url = url
        self._timeout = timeout
        self._max_bytes = max_bytes
        self._client = httpx.AsyncClient(
            headers={"User-Agent": "trader-agent/1.0"}, timeout=timeout,
            follow_redirects=True, transport=transport,
        )

    async def __aenter__(self) -> RssNewsProvider:
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def list_recent(self, since: datetime, until: datetime) -> list[NewsItem]:
        try:
            async with asyncio.timeout(self._timeout):
                payload = await self._fetch()
        except (httpx.HTTPError, TimeoutError) as exc:
            # URL, query parametreleri veya response body log/istemciye sızmaz.
            raise NewsError(f"RSS kaynağına erişilemedi: {self.name}") from exc
        try:
            root = ET.fromstring(payload, parser=ET.XMLParser(target=_RssTreeBuilder()))
        except ET.ParseError as exc:
            raise NewsError(f"RSS XML geçersiz: {self.name}") from exc
        channel = root.find("channel")
        if root.tag != "rss" or channel is None:
            raise NewsError(f"RSS 2.0 bekleniyordu: {self.name}")
        items = [self._parse_item(item) for item in channel.findall("item")]
        return [item for item in items if since <= item.published_at <= until]

    async def _fetch(self) -> bytes:
        for attempt in range(2):
            try:
                async with self._client.stream("GET", self._url) as response:
                    response.raise_for_status()
                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > self._max_bytes:
                            raise NewsError(f"RSS boyut sınırı aşıldı: {self.name}")
                    return bytes(body)
            except httpx.HTTPStatusError as exc:
                if attempt or (exc.response.status_code != 429
                               and exc.response.status_code < 500):
                    raise
            except httpx.TransportError:
                if attempt:
                    raise
            await asyncio.sleep(0.25)
        raise AssertionError("unreachable")

    def _parse_item(self, element: ET.Element) -> NewsItem:
        title = _clean(element.findtext("title", ""))
        body = _clean(element.findtext("description", ""))
        link = element.findtext("link", "").strip()
        raw_date = element.findtext("pubDate", "").strip()
        try:
            published = parsedate_to_datetime(raw_date)
        except (ValueError, TypeError, OverflowError) as exc:
            raise NewsError(f"RSS haber tarihi geçersiz: {self.name}") from exc
        if published.tzinfo is None:
            raise NewsError(f"RSS haber tarihinde saat dilimi eksik: {self.name}")
        if not title or not link.startswith(("https://", "http://")):
            raise NewsError(f"RSS haber başlığı veya bağlantısı geçersiz: {self.name}")
        return NewsItem(
            source=self.name, title=title, body=body, url=link,
            published_at=published.astimezone(timezone.utc),
        )


def _clean(value: str) -> str:
    return _HTML_TAG_RE.sub("", unescape(value)).strip()
