import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from trader_agent.engine.news.types import NewsError, Summary
from trader_agent.engine.news.matching import CompanyMatcher
from .conftest import NOW


async def test_multi_company_news_reaches_both_symbols_not_market(
    make_service, provider, article, repo,
):
    multi = replace(article, title="Türk Hava Yolları ve ASELSAN anlaşma imzaladı")
    provider.list_recent.return_value = [multi, multi]
    result = await make_service().refresh_daily_digest(symbols={"THYAO", "ASELS"})
    written = repo.upsert_many.call_args.args[0]
    assert {digest.scope_key for digest in written} == {"THYAO", "ASELS"}
    assert result.fetched_items == 1
    assert result.written_digests == 2
    assert result.status == "updated"
    assert all(digest.item_count == 1 for digest in written)


async def test_unwatched_company_is_not_misclassified_as_market(
    make_service, provider, article, summarizer, repo,
):
    provider.list_recent.return_value = [replace(article, title="ASELSAN sözleşme imzaladı")]
    result = await make_service().refresh_daily_digest(symbols={"THYAO"})
    assert result.status == "empty"
    summarizer.summarize.assert_not_awaited()
    repo.upsert_many.assert_not_awaited()


@pytest.mark.parametrize("failure", ["feed", "model"])
async def test_dependency_failure_never_writes(
    make_service, provider, summarizer, repo, failure,
):
    dependency = provider.list_recent if failure == "feed" else summarizer.summarize
    dependency.side_effect = NewsError("simulated failure")
    with pytest.raises(NewsError, match="simulated failure.*job="):
        await make_service().refresh_daily_digest(symbols=set())
    repo.upsert_many.assert_not_awaited()


async def test_failed_symbol_keeps_successful_market_uncommitted(
    make_service, provider, summarizer, repo, article,
):
    provider.list_recent.return_value = [article, replace(article, title="ASELSAN", url="http://x/2")]

    async def summarize(request, job_id):
        if request.scope_key:
            raise NewsError("symbol failed")
        return Summary("Market özeti", "test")

    summarizer.summarize.side_effect = summarize
    with pytest.raises(NewsError, match="symbol failed"):
        await make_service().refresh_daily_digest(symbols={"ASELS"})
    repo.upsert_many.assert_not_awaited()


async def test_empty_feed_is_success_without_model_call(make_service, provider, repo, summarizer):
    provider.list_recent.return_value = []
    result = await make_service().refresh_daily_digest(symbols=set())
    assert result.status == "empty"
    assert result.fetched_items == result.written_digests == 0
    summarizer.summarize.assert_not_awaited()
    repo.upsert_many.assert_not_awaited()


async def test_selection_is_bounded_and_reports_omissions(make_service, provider, article, repo):
    provider.list_recent.return_value = [
        replace(article, body="B" * 600, published_at=NOW - timedelta(minutes=i), url=f"http://x/{i}")
        for i in range(5)
    ]
    result = await make_service(max_input_chars=2000).refresh_daily_digest(symbols=set())
    assert result.omitted_items == 3
    assert repo.upsert_many.call_args.args[0][0].item_count == 2


async def test_symbol_calls_are_bounded_and_cancelled_on_shutdown(
    make_service, provider, article, summarizer, repo,
):
    names = {f"SYM{i}": f"Company{i}" for i in range(20)}
    provider.list_recent.return_value = [
        replace(article, title=sym, url=f"http://x/{sym}") for sym in names
    ]
    two_started = asyncio.Event()
    active = peak = 0

    async def summarize(request, job_id):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        if active == 2:
            two_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    summarizer.summarize.side_effect = summarize
    service = make_service(matcher=CompanyMatcher(names))
    task = asyncio.create_task(service.refresh_daily_digest(symbols=set(names)))
    await asyncio.wait_for(two_started.wait(), timeout=2)
    await service.aclose()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peak == 2
    assert active == 0
    repo.upsert_many.assert_not_awaited()
    with pytest.raises(NewsError, match="kapalı"):
        await service.refresh_daily_digest(symbols=set())


async def test_fetch_failure_cancels_sibling_task(make_service, repo):
    started, stopped = asyncio.Event(), asyncio.Event()

    async def wait(*args):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def fail(*args):
        await started.wait()
        raise NewsError("failed")

    service = make_service(providers=[
        AsyncMock(list_recent=AsyncMock(side_effect=wait)),
        AsyncMock(list_recent=AsyncMock(side_effect=fail)),
    ])
    with pytest.raises(NewsError):
        await service.refresh_daily_digest(symbols=set())
    assert stopped.is_set()
    repo.upsert_many.assert_not_awaited()


async def test_date_uses_exchange_timezone_and_read_uses_same_day(make_service, repo):
    now = datetime(2026, 10, 8, 22, tzinfo=timezone.utc)
    service = make_service(now=lambda: now)
    await service.refresh_daily_digest(symbols=set())
    assert repo.upsert_many.call_args.args[0][0].digest_date.isoformat() == "2026-10-09"
    await service.get_market_news()
    args = repo.recent_market.call_args.args
    assert tuple(day.isoformat() for day in args) == ("2026-10-09", "2026-10-09")


@pytest.mark.parametrize("since", [NOW, NOW + timedelta(days=1),
                                    NOW.replace(tzinfo=None)])
async def test_invalid_window_fails_before_io(make_service, provider, since):
    with pytest.raises(ValueError):
        await make_service().refresh_daily_digest(symbols=set(), since=since)
    provider.list_recent.assert_not_awaited()


async def test_job_timeout_does_not_commit(make_service, provider, repo):
    async def timeout(*args):
        raise TimeoutError("deadline")

    provider.list_recent.side_effect = timeout
    with pytest.raises(NewsError, match="süre sınırı"):
        await make_service().refresh_daily_digest(symbols=set())
    repo.upsert_many.assert_not_awaited()


async def test_old_since_only_filters_current_feed_without_extra_fetches(make_service, provider):
    since = NOW - timedelta(days=365)
    result = await make_service().refresh_daily_digest(symbols=set(), since=since)
    assert result.since == since
    provider.list_recent.assert_awaited_once_with(since, NOW)
