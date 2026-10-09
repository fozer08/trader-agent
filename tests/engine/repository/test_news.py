import asyncio
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import func, select

from trader_agent.engine.news.types import NewsDigest, NewsError
from trader_agent.engine.repository import (
    NewsRepository, create_db_engine, create_session_factory, init_schema,
)
from trader_agent.engine.repository.news import NewsDigestORM


@pytest.fixture
def store(tmp_path):
    engine = create_db_engine(tmp_path / "test.db")
    init_schema(engine)
    factory = create_session_factory(engine)
    yield NewsRepository(factory), factory
    engine.dispose()


@pytest.fixture
def digest():
    return NewsDigest("market", None, date(2026, 10, 9), ["test"], 1, "Özet.",
                      datetime(2026, 10, 9, 12, tzinfo=timezone.utc), "test-model")


async def test_atomic_batch_rollback_preserves_previous_summary(store, digest):
    repo, _ = store
    await repo.upsert_many([digest])
    with pytest.raises(NewsError):
        await repo.upsert_many([
            replace(digest, summary="Değiştirildi"),
            replace(digest, scope_type="symbol", scope_key=None),
        ])
    assert await repo.recent_market(digest.digest_date, digest.digest_date) == [digest]


async def test_concurrent_market_upserts_leave_one_record(store, digest):
    repo, factory = store
    await asyncio.gather(*(repo.upsert_many([digest]) for _ in range(10)))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(NewsDigestORM)) == 1
    assert await repo.recent_market(digest.digest_date, digest.digest_date) == [digest]


async def test_read_date_bounds_symbol_normalization_and_order(store, digest):
    repo, _ = store
    await repo.upsert_many([
        replace(digest, digest_date=date(2026, 10, day), scope_type="symbol", scope_key="thyao")
        for day in (7, 8, 9, 10)
    ])
    items = await repo.recent_symbol(" THYAO ", date(2026, 10, 8), date(2026, 10, 9))
    assert [item.digest_date.day for item in items] == [9, 8]
    assert all(item.scope_key == "THYAO" for item in items)
    assert await repo.recent_market(date(2026, 10, 1), date(2026, 10, 10)) == []
