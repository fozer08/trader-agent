"""Deterministik girdi seçimi ve prompt serileştirmesi."""

import json
from dataclasses import replace

from .types import DigestInput, NewsItem

# Her haberde başlık ve gövde için ayrı sınır; toplam JSON ayrıca sınırlandırılır.
_TITLE_CHARS = 300
_BODY_CHARS = 600


def _article_data(item: NewsItem) -> dict[str, str]:
    return {
        "source": item.source, "published_at": item.published_at.isoformat(),
        "title": item.title, "body": item.body,
    }


def format_request(request: DigestInput) -> str:
    return json.dumps({
        "scope": request.scope_key or "market",
        "since": request.since.isoformat(),
        "until": request.until.isoformat(),
        "articles": [_article_data(item) for item in request.items],
    }, ensure_ascii=False)


def select_items(
    request: DigestInput, *, max_chars: int,
) -> tuple[DigestInput, int]:
    """En yeni haberleri tutar; raporlanan item_count modele giden sayıdır."""
    selected: list[NewsItem] = []
    size = len(format_request(replace(request, items=())))
    for item in sorted(
        request.items, key=lambda it: (it.published_at, it.source, it.url), reverse=True,
    ):
        candidate = replace(item, title=item.title[:_TITLE_CHARS], body=item.body[:_BODY_CHARS])
        # Her adımda tüm listeyi tekrar serileştirmek yerine yalnız yeni haberi say.
        size += len(json.dumps(_article_data(candidate), ensure_ascii=False))
        size += 2 if selected else 0  # JSON liste ayıracı: ", "
        if size > max_chars:
            break
        selected.append(candidate)
    return replace(request, items=tuple(selected)), len(request.items) - len(selected)
