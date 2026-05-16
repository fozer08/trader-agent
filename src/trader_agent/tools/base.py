from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class Tool:
    """Claude API'ye sunulan bir aracın tanımı ve uygulama fonksiyonu.

    handler, Claude'un tool_use bloğundaki input dict'i keyword argüman olarak
    alır: ``await tool.handler(**input_dict)``.
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[Any]]

    def to_api_dict(self) -> dict:
        """Claude API'nin beklediği tool tanım sözlüğünü döndürür."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
