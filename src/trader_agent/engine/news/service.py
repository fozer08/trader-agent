from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import TypeVar
from uuid import uuid4
from zoneinfo import ZoneInfo

from ...utils.logging import get_logger
from .digest import select_items
from .matching import CompanyMatcher
from .types import (
    DigestInput, DigestStore, NewsDigest, NewsError, NewsItem, NewsProvider,
    RefreshResult, Summarizer,
)

_log = get_logger(__name__)
_T = TypeVar("_T")
_R = TypeVar("_R")


async def _parallel(
    items: Sequence[_T], operation: Callable[[_T], Awaitable[_R]], limit: int,
) -> list[_R]:
    semaphore = asyncio.Semaphore(limit)

    async def run(item: _T) -> _R:
        async with semaphore:
            return await operation(item)

    tasks = [asyncio.create_task(run(item)) for item in items]
    try:
        return list(await asyncio.gather(*tasks))
    finally:
        # Hata/cancellation sonrasında diğer dış çağrılar çalışmaya devam etmesin.
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class NewsService:
    """RSS → eşleştirme → sınırlı özet üretimi → tek transaction.

    Kaynak/model hatasında hiçbir özeti değiştirmez. Başarılı boş fetch hata değildir.
    Günlük kayıt, yayın tarihine göre arşiv değil yenileme gününün pencere özetidir.
    """

    def __init__(
        self, *, providers: Sequence[NewsProvider], summarizer: Summarizer,
        repo: DigestStore, matcher: CompanyMatcher, timezone: ZoneInfo,
        fetch_concurrency: int, digest_concurrency: int, job_timeout: float,
        max_input_chars: int,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._providers = providers
        self._summarizer = summarizer
        self._repo = repo
        self._matcher = matcher
        self._timezone = timezone
        self._fetch_concurrency = fetch_concurrency
        self._digest_concurrency = digest_concurrency
        self._job_timeout = job_timeout
        self._max_chars = max_input_chars
        self._now = now
        self._lock = asyncio.Lock()
        self._active: set[asyncio.Task[RefreshResult]] = set()
        self._closed = False

    async def refresh_daily_digest(
        self, *, symbols: set[str], since: datetime | None = None,
    ) -> RefreshResult:
        if self._closed:
            raise NewsError("Haber servisi kapalı.")
        task = asyncio.create_task(self._run_refresh(symbols=symbols, since=since))
        self._active.add(task)
        try:
            return await task
        finally:
            self._active.discard(task)

    async def aclose(self) -> None:
        self._closed = True
        tasks = list(self._active)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_refresh(
        self, *, symbols: set[str], since: datetime | None,
    ) -> RefreshResult:
        job_id = uuid4().hex
        started = monotonic()
        try:
            async with asyncio.timeout(self._job_timeout):
                async with self._lock:
                    result = await self._refresh(job_id, symbols, since)
        except TimeoutError as exc:
            _log.warning("news timeout job=%s elapsed=%.3f", job_id, monotonic() - started)
            raise NewsError(f"Haber yenileme süre sınırı aşıldı (job={job_id}).") from exc
        except NewsError as exc:
            _log.warning("news failed job=%s elapsed=%.3f reason=%s",
                         job_id, monotonic() - started, exc)
            raise NewsError(f"{exc} (job={job_id})") from exc
        _log.info("news finished job=%s status=%s items=%d written=%d omitted=%d elapsed=%.3f",
                  job_id, result.status, result.fetched_items, result.written_digests,
                  result.omitted_items, monotonic() - started)
        return result

    async def _refresh(
        self, job_id: str, symbols: set[str], since: datetime | None,
    ) -> RefreshResult:
        until = self._now()
        since = since if since is not None else until - timedelta(days=1)
        if since.tzinfo is None or since >= until:
            raise ValueError("Haber başlangıcı timezone içermeli ve geçmişte olmalıdır.")
        async def fetch(provider: NewsProvider) -> list[NewsItem]:
            return await provider.list_recent(since, until)

        batches = await _parallel(self._providers, fetch, self._fetch_concurrency)
        unique = {item.url: item for batch in batches for item in batch}
        buckets: dict[str | None, list[NewsItem]] = defaultdict(list)
        for item in unique.values():
            matches = self._matcher.match(item.title, item.body)
            if not matches:
                buckets[None].append(item)
            for symbol in matches:
                if symbol in symbols:
                    buckets[symbol].append(item)

        requests: list[DigestInput] = []
        omitted = 0
        for key, items in buckets.items():
            request, dropped = select_items(
                DigestInput(key, since, until, tuple(items)),
                max_chars=self._max_chars,
            )
            omitted += dropped
            if not request.items:
                raise NewsError(f"Haber girdisi boyut sınırına sığmıyor: {key or 'market'}")
            requests.append(request)

        async def summarize(request: DigestInput) -> NewsDigest:
            summary = await self._summarizer.summarize(request, job_id)
            return NewsDigest(
                scope_type="symbol" if request.scope_key else "market",
                scope_key=request.scope_key, digest_date=until.astimezone(self._timezone).date(),
                sources=sorted({item.source for item in request.items}),
                item_count=len(request.items), summary=summary.text,
                generated_at=self._now(), model_version=summary.model,
            )

        digests = await _parallel(requests, summarize, self._digest_concurrency)
        if digests:
            await self._repo.upsert_many(digests)
        return RefreshResult(
            job_id, "updated" if digests else "empty", since, until,
            len(unique), len(digests), omitted,
        )

    async def get_market_news(self, days: int = 1) -> list[NewsDigest]:
        today = self._now().astimezone(self._timezone).date()
        return await self._repo.recent_market(today - timedelta(days=days - 1), today)

    async def get_symbol_news(self, symbol: str, days: int = 7) -> list[NewsDigest]:
        today = self._now().astimezone(self._timezone).date()
        return await self._repo.recent_symbol(symbol, today - timedelta(days=days - 1), today)
