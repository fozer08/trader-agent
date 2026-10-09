from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from collections.abc import Callable
from typing import Literal, TypeVar

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    String,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Mapped, Session, mapped_column

from ..news.types import NewsDigest, NewsError
from .helpers import normalize_symbol
from .base import Base


# ---- ORM ---------------------------------------------------------------------

class NewsDigestORM(Base):
    __tablename__ = "news_digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope_type: Mapped[Literal["market", "symbol"]] = mapped_column(String, nullable=False)
    scope_key: Mapped[str | None] = mapped_column(String, nullable=True)
    digest_date: Mapped[date] = mapped_column(Date, nullable=False)
    sources: Mapped[str] = mapped_column(String, nullable=False)  # JSON array
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[str] = mapped_column(String, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_version: Mapped[str] = mapped_column(String, nullable=False, default="")

    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('market', 'symbol')",
            name="ck_news_scope_type",
        ),
        # market scope: scope_key NULL; symbol scope: scope_key NOT NULL
        CheckConstraint(
            "(scope_type = 'market' AND scope_key IS NULL) OR "
            "(scope_type = 'symbol' AND scope_key IS NOT NULL)",
            name="ck_news_scope_consistency",
        ),
        # Aynı scope+gün için tek digest. scope_key NULL'lar arasında SQLite
        # UNIQUE NULL'ları farklı kabul eder; market için bunu uygulamada upsert
        # logic'i ile garantiliyoruz.
        UniqueConstraint("scope_type", "scope_key", "digest_date", name="uq_news_scope_date"),
    )


# ---- Repository --------------------------------------------------------------

_T = TypeVar("_T")


async def _run_db(operation: Callable[[], _T]) -> _T:
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # SQLite thread'i iptal edilemez; cleanup/dispose öncesi bitmesini bekle.
        try:
            await task
        except SQLAlchemyError:
            pass
        raise
    except SQLAlchemyError as exc:
        raise NewsError("Haber veritabanı işlemi başarısız.") from exc


class NewsRepository:
    """SQLite erişimi event loop dışında; bir yenileme tek transaction'dır."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    async def upsert_many(self, digests: list[NewsDigest]) -> None:
        await _run_db(lambda: self._upsert_many(digests))

    def _upsert_many(self, digests: list[NewsDigest]) -> None:
        with self._session_factory() as session:
            # SELECT öncesinde yazıcı kilidi: NULL market anahtarında da yarış olmaz.
            session.execute(text("BEGIN IMMEDIATE"))
            for digest in digests:
                scope_key = normalize_symbol(digest.scope_key) if digest.scope_key else None
                row = session.scalar(select(NewsDigestORM).where(
                    NewsDigestORM.scope_type == digest.scope_type,
                    NewsDigestORM.scope_key == scope_key,
                    NewsDigestORM.digest_date == digest.digest_date,
                ))
                if row is None:
                    row = NewsDigestORM(
                        scope_type=digest.scope_type, scope_key=scope_key,
                        digest_date=digest.digest_date,
                    )
                    session.add(row)
                row.sources = json.dumps(digest.sources, ensure_ascii=False)
                row.item_count = digest.item_count
                row.summary = digest.summary
                row.generated_at = digest.generated_at
                row.model_version = digest.model_version
            session.commit()

    async def recent_market(self, since: date, until: date) -> list[NewsDigest]:
        return await _run_db(lambda: self._recent(None, since, until))

    async def recent_symbol(self, symbol: str, since: date, until: date) -> list[NewsDigest]:
        symbol = normalize_symbol(symbol)
        return await _run_db(lambda: self._recent(symbol, since, until))

    def _recent(self, symbol: str | None, since: date, until: date) -> list[NewsDigest]:
        with self._session_factory() as session:
            rows = session.scalars(select(NewsDigestORM).where(
                NewsDigestORM.scope_type == ("symbol" if symbol else "market"),
                NewsDigestORM.scope_key == symbol,
                NewsDigestORM.digest_date.between(since, until),
            ).order_by(NewsDigestORM.digest_date.desc()))
            return [_to_digest(row) for row in rows]


def _to_digest(row: NewsDigestORM) -> NewsDigest:
    return NewsDigest(
        scope_type=row.scope_type,
        scope_key=row.scope_key, digest_date=row.digest_date,
        sources=json.loads(row.sources), item_count=row.item_count, summary=row.summary,
        generated_at=row.generated_at.replace(tzinfo=timezone.utc),
        model_version=row.model_version,
    )
