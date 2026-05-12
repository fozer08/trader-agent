from __future__ import annotations

import asyncio

from ..market.provider_base import MarketDataProvider
from ..repository.portfolio import Position, PositionRepository
from .base import Tool


class PortfolioTools:
    """Portföy araçları; tüm hatalar ``{"error": "..."}`` olarak döner.

    list_portfolio canlı fiyat + P/L + stop/hedef uzaklığı ile zenginleştirilir;
    provider hatasında ilgili pozisyonun canlı alanları yer almaz.
    """

    def __init__(self, repository: PositionRepository, provider: MarketDataProvider) -> None:
        self._repo = repository
        self._provider = provider
        self._delay_minutes = getattr(provider, "delay_minutes", None)

    # ---- Tool handlers -------------------------------------------------------

    async def add_position(
        self,
        symbol: str,
        quantity: int,
        avg_cost: float,
        stop_loss: float | None = None,
        target: float | None = None,
    ) -> dict:
        try:
            position, previous = self._repo.add(symbol, quantity, avg_cost, stop_loss, target)
            return {
                "position": _position_to_dict(position),
                "previous": _position_to_dict(previous) if previous else None,
            }
        except Exception as exc:
            return {"error": str(exc)}

    async def set_position_levels(
        self,
        symbol: str,
        stop_loss: float | None = None,
        target: float | None = None,
    ) -> dict:
        try:
            position = self._repo.set_levels(symbol, stop_loss=stop_loss, target=target)
            return {"position": _position_to_dict(position)}
        except KeyError:
            return {"error": f"Pozisyon bulunamadı: {symbol}"}
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
            positions = self._repo.list()
        except Exception as exc:
            return {"error": str(exc)}

        if not positions:
            return {"positions": []}

        prices = await asyncio.gather(
            *[self._safe_price(p.symbol) for p in positions],
            return_exceptions=False,
        )
        enriched = [_enriched_position(p, price) for p, price in zip(positions, prices)]
        result: dict = {"positions": enriched}
        if self._delay_minutes is not None:
            result["delay_minutes"] = self._delay_minutes
        return result

    def as_tool_list(self) -> list[Tool]:
        return [
            Tool(
                name="add_position",
                description=(
                    "Portföye pozisyon ekler veya mevcut pozisyonu weighted-average ile günceller. "
                    "stop_loss ve target opsiyoneldir; verilirse mevcut değerin üzerine yazılır, verilmezse korunur. "
                    "Sadece seviyeleri güncellemek için set_position_levels kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü, örn: THYAO"},
                        "quantity": {"type": "integer", "description": "Eklenecek lot adedi (pozitif tam sayı)"},
                        "avg_cost": {"type": "number", "description": "Eklenen alımın hisse başına TL maliyeti"},
                        "stop_loss": {"type": "number", "description": "Pozisyon için stop-loss seviyesi (TL)"},
                        "target": {"type": "number", "description": "Pozisyon için kar al hedef seviyesi (TL)"},
                    },
                    "required": ["symbol", "quantity", "avg_cost"],
                },
                handler=self.add_position,
            ),
            Tool(
                name="set_position_levels",
                description=(
                    "Mevcut bir pozisyonun stop_loss ve/veya target seviyesini günceller; "
                    "quantity ve avg_cost'a dokunmaz. En az birini geçmek zorunludur."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü"},
                        "stop_loss": {"type": "number", "description": "Yeni stop-loss seviyesi (TL)"},
                        "target": {"type": "number", "description": "Yeni kar al hedef seviyesi (TL)"},
                    },
                    "required": ["symbol"],
                },
                handler=self.set_position_levels,
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
                description="Portföydeki tüm pozisyonları siler. Yıkıcı işlem; önce kullanıcıdan onay al.",
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=self.clear_portfolio,
            ),
            Tool(
                name="list_portfolio",
                description=(
                    "Portföydeki tüm pozisyonları döner. Her pozisyon için: "
                    "symbol, quantity, avg_cost, stop_loss, target, current_price, "
                    "pnl_pct (avg_cost'a göre %), distance_to_stop_pct, distance_to_target_pct. "
                    "Canlı fiyat alınamayan pozisyonda current_price ve türev alanlar yer almaz."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=self.list_portfolio,
            ),
        ]

    # ---- private -------------------------------------------------------------

    async def _safe_price(self, symbol: str) -> float | None:
        try:
            snapshot = await self._provider.get_today(symbol)
        except Exception:
            return None
        return snapshot.close if snapshot is not None else None


# ---- helpers -----------------------------------------------------------------

def _position_to_dict(position: Position) -> dict:
    d: dict = {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "avg_cost": position.avg_cost,
        "updated_at": position.updated_at.isoformat(),
    }
    if position.stop_loss is not None:
        d["stop_loss"] = position.stop_loss
    if position.target is not None:
        d["target"] = position.target
    return d


def _enriched_position(position: Position, current_price: float | None) -> dict:
    d = _position_to_dict(position)
    if current_price is None or position.avg_cost <= 0:
        return d
    d["current_price"] = current_price
    d["pnl_pct"] = round((current_price - position.avg_cost) / position.avg_cost * 100, 2)
    if position.stop_loss is not None:
        d["distance_to_stop_pct"] = round((current_price - position.stop_loss) / current_price * 100, 2)
    if position.target is not None:
        d["distance_to_target_pct"] = round((position.target - current_price) / current_price * 100, 2)
    return d
