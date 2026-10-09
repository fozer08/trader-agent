from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Protocol


class NewsError(Exception):
    """Kaynak veya model sınırında oluşan, kullanıcıya gösterilebilir hata."""


@dataclass(frozen=True)
class NewsItem:
    source: str
    published_at: datetime
    title: str
    body: str
    url: str


@dataclass(frozen=True)
class DigestInput:
    scope_key: str | None
    since: datetime
    until: datetime
    items: tuple[NewsItem, ...]


@dataclass(frozen=True)
class Summary:
    text: str
    model: str


@dataclass(frozen=True)
class NewsDigest:
    scope_type: Literal["market", "symbol"]
    scope_key: str | None
    digest_date: date
    sources: list[str]
    item_count: int
    summary: str
    generated_at: datetime
    model_version: str


@dataclass(frozen=True)
class RefreshResult:
    job_id: str
    status: Literal["updated", "empty"]
    since: datetime
    until: datetime
    fetched_items: int
    written_digests: int
    omitted_items: int


class NewsProvider(Protocol):
    async def list_recent(self, since: datetime, until: datetime) -> list[NewsItem]: ...


class Summarizer(Protocol):
    async def summarize(self, request: DigestInput, job_id: str) -> Summary: ...


class DigestStore(Protocol):
    async def upsert_many(self, digests: list[NewsDigest]) -> None: ...

    async def recent_market(self, since: date, until: date) -> list[NewsDigest]: ...

    async def recent_symbol(
        self, symbol: str, since: date, until: date,
    ) -> list[NewsDigest]: ...
