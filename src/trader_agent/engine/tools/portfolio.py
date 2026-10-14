from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from dataclasses import asdict

from ..analysis.portfolio import analyze_portfolio
from ..analysis import Horizon
from ..positions import Source, holding_days
from ..repository.helpers import utc_now

from ..marketdata.service import MarketService
from ..marketdata.types import PriceQuote
from ..repository import (
    ClosedPosition,
    PortfolioRepository,
    Position,
    PositionTransaction,
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
        self._quote_semaphore = asyncio.Semaphore(3)

    async def buy_position(self, symbol: str, quantity: int, price: float,
                           stop_loss: float | None = None, target: float | None = None,
                           note: str | None = None, horizon: Horizon | None = None,
                           position_id: str | None = None, opened_at: datetime | None = None,
                           operation_id: str | None = None) -> dict:
        try:
            position = await asyncio.to_thread(self._repo.buy, symbol, quantity, price,
                stop_loss, target, note, horizon, position_id=position_id,
                opened_at=opened_at, operation_id=operation_id)
            return {"position": _position_to_dict(position)}
        except (ValueError, KeyError) as exc:
            return {"error": str(exc)}

    async def accept_recommendation(self, recommendation_id: str, quantity: int, price: float,
                                    operation_id: str | None = None) -> dict:
        try:
            position = await asyncio.to_thread(self._repo.accept, recommendation_id, quantity,
                                               price, operation_id)
            return {"position": _position_to_dict(position)}
        except (ValueError, KeyError) as exc:
            return {"error": str(exc)}

    async def sell_position(self, position_id: str, quantity: int, price: float,
                            operation_id: str | None = None) -> dict:
        try:
            position = await asyncio.to_thread(self._repo.sell, position_id, quantity, price,
                                               operation_id=operation_id)
            closed_list = await asyncio.to_thread(self._repo.list_closed) if position is None else []
            archive = next((_closed_to_dict(c) for c in closed_list if c.id == position_id), None)
            return {"closed": position is None, "position": _position_to_dict(position) if position else None,
                    "archived": archive}
        except (ValueError, KeyError) as exc:
            return {"error": str(exc)}

    async def set_levels(self, position_id: str, stop_loss: float | None = None,
                         target: float | None = None, note: str | None = None,
                         horizon: Horizon | None = None) -> dict:
        try:
            position = await asyncio.to_thread(self._repo.set_levels, position_id, stop_loss, target, note, horizon)
            return {"position": _position_to_dict(position)}
        except (ValueError, KeyError) as exc:
            return {"error": str(exc)}

    async def remove_position(self, position_id: str) -> dict:
        removed = await asyncio.to_thread(self._repo.remove, position_id)
        return {"removed": True, "position_id": position_id} if removed else {"error": "Pozisyon bulunamadı."}

    async def clear_portfolio(self) -> dict:
        return {"removed_count": await asyncio.to_thread(self._repo.clear)}

    async def list_portfolio(self, source: Source | None = None) -> dict:
        positions = await asyncio.to_thread(self._repo.list, source=source)
        symbols = list(dict.fromkeys(p.symbol for p in positions))
        quotes = dict(zip(symbols, await asyncio.gather(*[self._safe_quote(s) for s in symbols])))
        summary = asdict(analyze_portfolio(positions, list(quotes.values())))
        performance = await asyncio.to_thread(self._repo.performance, source)
        summary["realized_pnl"] = performance["realized_pnl"]
        return {"positions": [_position_with_quote(p, quotes[p.symbol]) for p in positions], "summary": summary}

    async def get_position_tx(self, position_id: str) -> dict:
        try:
            txs = await asyncio.to_thread(self._repo.transactions, position_id)
            plans = await asyncio.to_thread(self._repo.plans, position_id)
            position = await asyncio.to_thread(self._repo.get, position_id)
            return {"position_id": position_id, "transactions": [_tx_to_dict(t) for t in txs],
                    "plans": plans, "initial_plan": position.initial_plan if position else None}
        except (ValueError, KeyError) as exc:
            return {"error": str(exc)}

    async def list_closed(self, since_days: int | None = None, symbol: str | None = None,
                          source: Source | None = None) -> dict:
        since = utc_now() - timedelta(days=since_days) if since_days and since_days > 0 else None
        closed = await asyncio.to_thread(self._repo.list_closed, since=since, symbol=symbol, source=source)
        return {"closed": [_closed_to_dict(c) for c in closed]}

    def as_tool_list(self) -> list[Tool]:
        identifier = {"type": "string", "description": "list_portfolio sonucundaki position id; hisse sembolü değildir"}
        positive = {"type": "number", "exclusiveMinimum": 0}
        plan = {"stop_loss": positive, "target": positive,
                "note": {"type": "string", "maxLength": 400},
                "horizon": {"type": "string", "enum": ["intraday", "swing", "position"]}}
        def tool(name, description, properties, required, handler, mutates=False):
            return Tool(name=name, description=description, input_schema={"type": "object",
                "properties": properties, "required": required, "additionalProperties": False},
                handler=handler, mutates=mutates)
        return [
            tool("buy_position", "Kullanıcının açık isteğiyle manuel pozisyon açar; position_id verilirse belirtilen pozisyona miktar ekler. Yeni pozisyonda horizon zorunlu. Öneri yayınlamak için publish_recommendation kullan. Sistem kaynaklı eklemelerde fiyat ve süre denetlenir.",
                 {"symbol": {"type": "string"}, "quantity": {"type": "integer", "minimum": 1},
                  "price": positive, "position_id": identifier, **plan}, ["symbol", "quantity", "price"], self.buy_position, True),
            tool("sell_position", "Kullanıcının açık isteğiyle pozisyonu kısmen veya tamamen kapatır; önce position_id öğren.",
                 {"position_id": identifier, "quantity": {"type": "integer", "minimum": 1}, "price": positive},
                 ["position_id", "quantity", "price"], self.sell_position, True),
            tool("set_levels", "Kullanıcının açık isteğiyle planı düzenler; ilk plan ve değişiklik geçmişi korunur.",
                 {"position_id": identifier, **plan}, ["position_id"], self.set_levels, True),
            tool("remove_position", "Kullanıcı hatalı kaydı iptal etmek istediğinde kullan; kapanış için sell_position kullan.",
                 {"position_id": identifier}, ["position_id"], self.remove_position, True),
            tool("clear_portfolio", "Yalnız açık kullanıcı isteğiyle açık kayıtları iptal eder; kapanmış geçmiş korunur.", {}, [], self.clear_portfolio, True),
            tool("list_portfolio", "Açık pozisyonların kimlik, kaynak, vade, maliyet, plan ve fiyat bilgileri. Aynı hissede birden fazla pozisyon olabilir.", {}, [], self.list_portfolio),
            tool("get_position_tx", "Pozisyonun miktar hareketleri ve plan değişikliklerini getirir.", {"position_id": identifier}, ["position_id"], self.get_position_tx),
            tool("list_closed", "Kapanan pozisyonların kaynak ve sonuçlarını getirir.",
                 {"since_days": {"type": "integer", "minimum": 1}, "symbol": {"type": "string"}}, [], self.list_closed),
        ]

    # ---- private -------------------------------------------------------------

    async def _safe_quote(self, symbol: str) -> PriceQuote:
        try:
            async with self._quote_semaphore:
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
        "id": p.id,
        "source": p.source,
        "producer": p.producer,
        "recommendation_id": p.recommendation_id,
        "initial_plan": p.initial_plan,
        "plan_changed": p.plan_changed,
        "created_at": p.created_at.isoformat(),
        "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        "symbol": p.symbol,
        "quantity": p.quantity,
        "avg_cost": round(p.avg_cost, 4),
        "opened_at": p.opened_at.isoformat() if p.opened_at else None,
        "hold_days": holding_days(p.opened_at, p.closed_at or utc_now()),
        "realized_pnl": round(p.realized_pnl, 2),
        "stop_loss": p.stop_loss,
        "target": p.target,
        "note": p.note,
        "horizon": p.horizon,
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
        "id": c.id,
        "source": c.source,
        "producer": c.producer,
        "horizon": c.horizon,
        "recommendation_id": c.recommendation_id,
        "symbol": c.symbol,
        "opened_at": c.opened_at.isoformat() if c.opened_at else None,
        "closed_at": c.closed_at.isoformat(),
        "hold_days": c.hold_days,
        "quantity": c.quantity,
        "realized_pnl": round(c.realized_pnl, 2),
        "realized_pnl_pct": round(c.realized_pnl_pct, 2),
        "avg_buy_cost": round(c.avg_buy_cost, 4),
        "avg_sell_price": round(c.avg_sell_price, 4),
    }
