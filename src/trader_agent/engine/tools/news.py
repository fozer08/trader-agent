from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from ..news.service import NewsService
from ..news.types import NewsDigest
from .base import Tool


class MarketNewsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    days: int = Field(default=1, ge=1, le=14)


class SymbolNewsInput(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    symbols: list[str] = Field(min_length=1, max_length=100)
    days: int = Field(default=7, ge=1, le=30)


class NewsTools:
    """Agent için NewsService üzerinden digest okuma araçları.

    Tool sadece read-only — yeni içerik daily_digest job'unda
    NewsService.refresh_daily_digest tarafından üretilir.
    """

    def __init__(self, service: NewsService) -> None:
        self._service = service

    # ---- Tool handlers -------------------------------------------------------

    async def get_market_news(self, days: int = 1) -> dict:
        """Son `days` günün market-scope digest'lerini döndürür."""
        request = MarketNewsInput(days=days)
        digests = await self._service.get_market_news(days=request.days)
        if not digests:
            return {"days": days, "digests": [], "note": "Bu aralıkta market digest yok."}
        return {
            "days": days,
            "digests": [_digest_to_dict(d) for d in digests],
        }

    async def get_symbol_news(self, symbols: list[str], days: int = 7) -> list[dict]:
        """Verilen sembollerin son `days` günündeki digest'leri."""
        request = SymbolNewsInput(symbols=symbols, days=days)
        normalized = list(dict.fromkeys(sym.strip().upper() for sym in request.symbols))
        if any(not sym or not sym.isalnum() for sym in normalized):
            raise ValueError("Geçersiz hisse sembolü.")
        out: list[dict] = []
        for sym in normalized:
            digests = await self._service.get_symbol_news(sym, days=request.days)
            out.append({
                "symbol": sym,
                "days": days,
                "digests": [_digest_to_dict(d) for d in digests],
            })
        return out

    def as_tool_list(self) -> list[Tool]:
        return [
            Tool(
                name="get_market_news",
                description=(
                    "BIST/Türkiye genel piyasa, makro ekonomi ve sektör için son `days` günün "
                    "günlük haber özetlerini döndürür. Hisse-bazlı haberler buraya dahil değil — "
                    "onun için get_symbol_news kullan. Kullanıcı 'BIST'te bugün ne oluyor', "
                    "'piyasa nasıl' gibi geniş sorular sorduğunda kullan."
                ),
                input_schema=MarketNewsInput.model_json_schema(),
                handler=self.get_market_news,
            ),
            Tool(
                name="get_symbol_news",
                description=(
                    "Belirli sembollerin son `days` günündeki haber özetlerini döndürür. "
                    "Medyada o şirket için geçen haberler ve material açıklamalar (bilanço, "
                    "temettü, sermaye değişikliği vb.) dahil. Kullanıcı bir hissenin "
                    "haberlerini sorduğunda kullan."
                ),
                input_schema=SymbolNewsInput.model_json_schema(),
                handler=self.get_symbol_news,
            ),
        ]


# ---- helpers -----------------------------------------------------------------

def _digest_to_dict(d: NewsDigest) -> dict[str, str | int | list[str]]:
    return {
        "date": d.digest_date.isoformat(),
        "sources": d.sources,
        "item_count": d.item_count,
        "summary": d.summary,
    }
