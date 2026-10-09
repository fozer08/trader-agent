from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from ..marketdata.service import MarketService
from ..marketdata.types import PriceQuote
from ..repository import (
    ClosedPosition,
    PortfolioRepository,
    Position,
    PositionTransaction,
    normalize_symbol,
)
from ...utils.logging import get_logger
from .base import Tool

_log = get_logger(__name__)


class PortfolioTools:
    """Portföy araçları; tüm hatalar ``{"error": "..."}`` olarak döner.

    ``list_portfolio`` canlı fiyat + unrealized P/L + stop/target uzaklığı ile
    zenginleştirilir; market service hatasında ilgili pozisyonun canlı alanları
    yer almaz.
    """

    def __init__(self, repository: PortfolioRepository, market: MarketService) -> None:
        self._repo = repository
        self._market = market

    # ---- Tool handlers -------------------------------------------------------

    async def buy_position(
        self,
        symbol: str,
        quantity: int,
        price: float,
        stop_loss: float | None = None,
        target: float | None = None,
        note: str | None = None,
    ) -> dict:
        try:
            position = self._repo.buy(symbol, quantity, price, stop_loss, target, note)
            return {"position": _position_to_dict(position)}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            _log.warning("buy_position failed for %s: %s", symbol, exc, exc_info=True)
            return {"error": str(exc)}

    async def sell_position(self, symbol: str, quantity: int, price: float) -> dict:
        try:
            position = self._repo.sell(symbol, quantity, price)
        except KeyError:
            return {"error": f"Pozisyon bulunamadı: {symbol}"}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            _log.warning("sell_position failed for %s: %s", symbol, exc, exc_info=True)
            return {"error": str(exc)}

        if position is None:
            closed_list = self._repo.list_closed(symbol=symbol)
            archive = _closed_to_dict(closed_list[0]) if closed_list else None
            return {"closed": True, "position": None, "archived": archive}
        return {"closed": False, "position": _position_to_dict(position), "archived": None}

    async def set_levels(
        self,
        symbol: str,
        stop_loss: float | None = None,
        target: float | None = None,
        note: str | None = None,
    ) -> dict:
        try:
            position = self._repo.set_levels(symbol, stop_loss=stop_loss, target=target, note=note)
            return {"position": _position_to_dict(position)}
        except KeyError:
            return {"error": f"Pozisyon bulunamadı: {symbol}"}
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            _log.warning("set_levels failed for %s: %s", symbol, exc, exc_info=True)
            return {"error": str(exc)}

    async def remove_position(self, symbol: str) -> dict:
        try:
            removed = self._repo.remove(symbol)
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            _log.warning("remove_position failed for %s: %s", symbol, exc, exc_info=True)
            return {"error": str(exc)}
        if not removed:
            return {"error": f"Pozisyon bulunamadı: {symbol}"}
        return {"symbol": normalize_symbol(symbol), "removed": True}

    async def clear_portfolio(self) -> dict:
        try:
            return {"removed_count": self._repo.clear()}
        except Exception as exc:
            _log.warning("clear_portfolio failed: %s", exc, exc_info=True)
            return {"error": str(exc)}

    async def list_portfolio(self) -> dict:
        try:
            positions = self._repo.list()
        except Exception as exc:
            _log.warning("list_portfolio failed: %s", exc, exc_info=True)
            return {"error": str(exc)}

        if not positions:
            return {"positions": []}

        quotes = await asyncio.gather(*[self._safe_quote(p.symbol) for p in positions])
        return {"positions": [_position_with_quote(p, quote) for p, quote in zip(positions, quotes)]}

    async def get_position_tx(self, symbol: str) -> dict:
        try:
            txs = self._repo.transactions(symbol)
        except ValueError as exc:
            return {"error": str(exc)}
        except Exception as exc:
            _log.warning("get_position_tx failed for %s: %s", symbol, exc, exc_info=True)
            return {"error": str(exc)}
        if not txs:
            return {"error": f"Pozisyon bulunamadı: {symbol}"}
        return {
            "symbol": normalize_symbol(symbol),
            "transactions": [_tx_to_dict(t) for t in txs],
        }

    async def list_closed(
        self,
        since_days: int | None = None,
        symbol: str | None = None,
    ) -> dict:
        try:
            since = None
            if since_days is not None and since_days > 0:
                since = datetime.now(timezone.utc) - timedelta(days=since_days)
            closed = self._repo.list_closed(since=since, symbol=symbol)
            return {"closed": [_closed_to_dict(c) for c in closed]}
        except Exception as exc:
            _log.warning("list_closed failed: %s", exc, exc_info=True)
            return {"error": str(exc)}

    def as_tool_list(self) -> list[Tool]:
        return [
            Tool(
                name="buy_position",
                description=(
                    "Portföye alım kaydı ekler. Yeni sembol için pozisyon açar, "
                    "var olan pozisyona ekler (weighted-average otomatik hesaplanır). "
                    "stop_loss, target ve kısa pozisyon notu opsiyoneldir; "
                    "sonradan set_levels ile güncellenebilir. Kullanıcının gerçekleştirdiği "
                    "alımı kaydetmek için kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü"},
                        "quantity": {"type": "integer", "exclusiveMinimum": 0, "description": "Alınan lot adedi (pozitif)"},
                        "price": {"type": "number", "exclusiveMinimum": 0, "description": "Hisse başına alım fiyatı (TL)"},
                        "stop_loss": {"type": "number", "exclusiveMinimum": 0, "description": "Stop-loss seviyesi (TL)"},
                        "target": {"type": "number", "exclusiveMinimum": 0, "description": "Kar al hedef seviyesi (TL)"},
                        "note": {"type": "string", "maxLength": 400, "description": "Pozisyon amacı veya çıkış koşulu hakkında kısa not"},
                    },
                    "required": ["symbol", "quantity", "price"],
                },
                handler=self.buy_position,
                mutates=True,
            ),
            Tool(
                name="sell_position",
                description=(
                    "Portföyden satım kaydı ekler. Kısmi satış mümkün; net miktar 0'a "
                    "düşerse pozisyon otomatik kapanır; işlem geçmişi korunur ve kapanış "
                    "özeti işlemlerden hesaplanır. Mevcut miktardan fazla satılamaz. "
                    "Döner: closed (bool); position (kapanmadıysa güncel pozisyon, kapandıysa null); "
                    "archived (kapandıysa kapanmış özet, açık kaldıysa null)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü"},
                        "quantity": {"type": "integer", "exclusiveMinimum": 0, "description": "Satılan lot adedi (pozitif)"},
                        "price": {"type": "number", "exclusiveMinimum": 0, "description": "Hisse başına satım fiyatı (TL)"},
                    },
                    "required": ["symbol", "quantity", "price"],
                },
                handler=self.sell_position,
                mutates=True,
            ),
            Tool(
                name="set_levels",
                description=(
                    "Mevcut bir pozisyonun stop_loss, target veya kısa notunu günceller. "
                    "En az biri verilmelidir; verilmeyen alan mevcut değerini korur. "
                    "Boş note metni mevcut notu temizler."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü"},
                        "stop_loss": {"type": "number", "exclusiveMinimum": 0, "description": "Yeni stop-loss seviyesi (TL)"},
                        "target": {"type": "number", "exclusiveMinimum": 0, "description": "Yeni hedef seviyesi (TL)"},
                        "note": {"type": "string", "maxLength": 400, "description": "Yeni pozisyon notu; boş metin notu temizler"},
                    },
                    "required": ["symbol"],
                },
                handler=self.set_levels,
                mutates=True,
            ),
            Tool(
                name="remove_position",
                description=(
                    "Aktif pozisyonu ve o pozisyon döneminin işlemlerini siler. "
                    "Önceki kapanmış dönemlerin geçmişi korunur. 'Yanlış girdim' "
                    "senaryosu için; sat işlemi için sell_position kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü"},
                    },
                    "required": ["symbol"],
                },
                handler=self.remove_position,
                mutates=True,
            ),
            Tool(
                name="clear_portfolio",
                description=(
                    "Tüm aktif pozisyonları siler. Yıkıcı işlem — önce kullanıcıdan onay al. "
                    "Aktif dönemlerin işlemleri silinir; kapanmış dönemlerin geçmişi korunur."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=self.clear_portfolio,
                mutates=True,
            ),
            Tool(
                name="list_portfolio",
                description=(
                    "Aktif pozisyonları döner. Her pozisyon için: symbol, quantity, avg_cost, "
                    "opened_at, hold_days (açılış günü dahil inclusive), realized_pnl, "
                    "stop_loss, target (set edilmemişse null), note (kısa pozisyon notu), "
                    "current_price (gecikmeli seans fiyatı veya son kapanış; veri yoksa null), "
                    "price_kind (delayed/close/unavailable), as_of, session_date. "
                    "Fiyat varsa ek alanlar: unrealized_pnl, unrealized_pnl_pct, "
                    "distance_to_stop_pct (stop_loss set ise), distance_to_target_pct (target set ise)."
                ),
                input_schema={"type": "object", "properties": {}, "required": []},
                handler=self.list_portfolio,
            ),
            Tool(
                name="get_position_tx",
                description=(
                    "Bir sembolün açık ve kapanmış pozisyon dönemlerindeki alım/satım kayıtlarını "
                    "kronolojik olarak döner. Her transaction için: id, kind (BUY/SELL), "
                    "quantity, price, created_at. İşlem kaydı yoksa error döner."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü"},
                    },
                    "required": ["symbol"],
                },
                handler=self.get_position_tx,
            ),
            Tool(
                name="list_closed",
                description=(
                    "Kapanmış pozisyonların işlemlerden hesaplanan özetlerini döner. Her kayıt için: "
                    "realized_pnl (toplam TL), realized_pnl_pct, avg_buy_cost, avg_sell_price, "
                    "hold_days."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "since_days": {
                            "type": "integer",
                            "exclusiveMinimum": 0,
                            "description": "Son N gün içinde kapananları getir (opsiyonel)",
                        },
                        "symbol": {"type": "string", "description": "Sadece bu sembol (opsiyonel)"},
                    },
                    "required": [],
                },
                handler=self.list_closed,
            ),
        ]

    # ---- private -------------------------------------------------------------

    async def _safe_quote(self, symbol: str) -> PriceQuote:
        try:
            return await self._market.get_quote(symbol)
        except Exception as exc:
            _log.warning("price fetch failed for %s: %s", symbol, exc)
            return PriceQuote(symbol, None, None, "unavailable")


# ---- helpers -----------------------------------------------------------------

def _position_with_quote(p: Position, quote: PriceQuote) -> dict:
    return {
        **_enriched_position(p, quote.price),
        "as_of": quote.as_of.isoformat() if quote.as_of else None,
        "price_kind": quote.kind,
        "session_date": quote.session_date.isoformat() if quote.session_date else None,
    }

def _position_to_dict(p: Position) -> dict:
    return {
        "symbol": p.symbol,
        "quantity": p.quantity,
        "avg_cost": round(p.avg_cost, 4),
        "opened_at": p.opened_at.isoformat(),
        "hold_days": p.hold_days,
        "realized_pnl": round(p.realized_pnl, 2),
        "stop_loss": p.stop_loss,
        "target": p.target,
        "note": p.note,
    }


def _enriched_position(p: Position, current_price: float | None) -> dict:
    d = _position_to_dict(p)
    d["current_price"] = current_price
    if current_price is None:
        return d
    d["unrealized_pnl"] = round(p.quantity * (current_price - p.avg_cost), 2)
    d["unrealized_pnl_pct"] = round((current_price - p.avg_cost) / p.avg_cost * 100, 2)
    if p.stop_loss is not None:
        d["distance_to_stop_pct"] = round((current_price - p.stop_loss) / current_price * 100, 2)
    if p.target is not None:
        d["distance_to_target_pct"] = round((p.target - current_price) / current_price * 100, 2)
    return d


def _tx_to_dict(tx: PositionTransaction) -> dict:
    return {
        "id": tx.id,
        "kind": tx.kind,
        "quantity": tx.quantity,
        "price": tx.price,
        "created_at": tx.created_at.isoformat(),
    }


def _closed_to_dict(c: ClosedPosition) -> dict:
    return {
        "symbol": c.symbol,
        "opened_at": c.opened_at.isoformat(),
        "closed_at": c.closed_at.isoformat(),
        "hold_days": c.hold_days,
        "quantity": c.quantity,
        "realized_pnl": round(c.realized_pnl, 2),
        "realized_pnl_pct": round(c.realized_pnl_pct, 2),
        "avg_buy_cost": round(c.avg_buy_cost, 4),
        "avg_sell_price": round(c.avg_sell_price, 4),
    }
