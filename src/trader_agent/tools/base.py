from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable


@dataclass
class Tool:
    """Claude API'ye sunulan bir aracın tanımı ve uygulama fonksiyonu.

    handler, Claude'un tool_use bloğundaki input dict'i keyword argüman olarak
    alır: ``await tool.handler(**input_dict)``.

    keep_in_history=True yapılan tool'un tool_use/tool_result blokları tur
    sonunda history'de bırakılır (sonraki turlarda referans verilebilsin
    diye). Default False — çoğu tool çıktısı asistanın metnine yansıdığı için
    history'de tutmak gereksizdir ve cache prefix'ini bozar.
    """

    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[Any]]
    keep_in_history: bool = False

    def to_api_dict(self) -> dict:
        """Claude API'nin beklediği tool tanım sözlüğünü döndürür."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }
