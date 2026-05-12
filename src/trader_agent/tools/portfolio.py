from __future__ import annotations

from ..repository.portfolio import Position, PositionRepository
from .base import Tool


class PortfolioTools:
    """Portföy yönetimi araçları; tüm hatalar ``{"error": "..."}`` olarak döner."""

    def __init__(self, repository: PositionRepository) -> None:
        self._repo = repository

    # ---- Tool handlers -------------------------------------------------------

    async def add_position(self, symbol: str, quantity: int, avg_cost: float) -> dict:
        try:
            position, previous = self._repo.add(symbol, quantity, avg_cost)
            return {
                "position": _position_to_dict(position),
                "previous": _position_to_dict(previous) if previous else None,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def remove_position(self, symbol: str) -> dict:
        try:
            removed = self._repo.remove(symbol)
            return {"symbol": symbol.strip().upper(), "removed": removed}
        except Exception as exc:
            return {"error": str(exc)}

    async def clear_portfolio(self) -> dict:
        try:
            return {"removed_count": self._repo.clear()}
        except Exception as exc:
            return {"error": str(exc)}

    async def list_portfolio(self) -> dict:
        try:
            return {"positions": [_position_to_dict(p) for p in self._repo.list()]}
        except Exception as exc:
            return {"error": str(exc)}

    def as_tool_list(self) -> list[Tool]:
        return [
            Tool(
                name="add_position",
                description=(
                    "Portföye pozisyon ekler. "
                    "Mevcut pozisyon varsa ağırlıklı ortalama ile günceller. "
                    "Return: Son pozisyon [ve önceki pozisyon]"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü, örn: THYAO"},
                        "quantity": {"type": "integer", "description": "Eklenecek lot adedi (pozitif tam sayı)"},
                        "avg_cost": {"type": "number", "description": "Eklenen alımın hisse başına TL maliyeti"},
                    },
                    "required": ["symbol", "quantity", "avg_cost"],
                },
                handler=self.add_position,
            ),
            Tool(
                name="remove_position",
                description="Portföyden tek bir sembolü tamamen siler.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Silinecek BIST ticker sembolü"},
                    },
                    "required": ["symbol"],
                },
                handler=self.remove_position,
            ),
            Tool(
                name="clear_portfolio",
                description="Portföydeki tüm pozisyonları siler.",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=self.clear_portfolio,
            ),
            Tool(
                name="list_portfolio",
                description="Portföydeki tüm pozisyonları döner.",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=self.list_portfolio,
            ),
        ]


def _position_to_dict(position: Position) -> dict:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "avg_cost": position.avg_cost,
        "updated_at": position.updated_at.isoformat(),
    }
