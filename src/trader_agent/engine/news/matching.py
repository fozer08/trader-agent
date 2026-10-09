"""Deterministik, çoklu şirket eşleştirmesi; provider veya SDK bağımlılığı yoktur."""

import re
import unicodedata
from collections.abc import Mapping


def _normalize(text: str) -> str:
    text = text.casefold().replace("ı", "i")
    text = "".join(
        char for char in unicodedata.normalize("NFKD", text)
        if not unicodedata.combining(char)
    )
    return " ".join(re.findall(r"\w+", text))


class CompanyMatcher:
    def __init__(self, names: Mapping[str, str]) -> None:
        self._patterns = {
            symbol: tuple(
                re.compile(rf"(?<!\w){re.escape(term)}(?!\w)")
                for term in sorted({_normalize(name) for name in (symbol, name)})
                if term
            )
            for symbol, name in sorted(names.items())
        }

    def match(self, title: str, body: str) -> tuple[str, ...]:
        text = _normalize(f"{title} {body}")
        return tuple(
            symbol for symbol, patterns in self._patterns.items()
            if any(pattern.search(text) for pattern in patterns)
        )
