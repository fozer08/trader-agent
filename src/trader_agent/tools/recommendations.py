from __future__ import annotations

from ..repository.recommendations import Recommendation, RecommendationRepository
from .base import Tool


class RecommendationTools:
    """AL/SAT öneri kaydı; tüm hatalar ``{"error": "..."}`` olarak döner."""

    def __init__(self, repository: RecommendationRepository) -> None:
        self._repo = repository

    # ---- Tool handlers -------------------------------------------------------

    async def record_recommendation(
        self,
        symbol: str,
        decision: str,
        entry: float,
        stop: float,
        target: float | None = None,
        rationale: str | None = None,
    ) -> dict:
        try:
            rec = self._repo.record(symbol, decision, entry, stop, target, rationale)  # type: ignore[arg-type]
            return {"recommendation": _to_dict(rec)}
        except Exception as exc:
            return {"error": str(exc)}

    async def list_recommendations(self, symbol: str | None = None) -> dict:
        try:
            recs = self._repo.list(symbol)
            return {"recommendations": [_to_dict(r) for r in recs]}
        except Exception as exc:
            return {"error": str(exc)}

    def as_tool_list(self) -> list[Tool]:
        return [
            Tool(
                name="record_recommendation",
                description=(
                    "Bir AL veya SAT önerisini geçmiş kaydına işler. "
                    "AL/SAT kararı verdiğinde bu tool'u çağır; PAS/İZLE/TUT kararları için kullanma. "
                    "Kayıtlar 10 gün saklanır; eski kayıtlar otomatik silinir."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "BIST ticker sembolü, örn: THYAO"},
                        "decision": {"type": "string", "enum": ["BUY", "SELL"], "description": "BUY = AL, SELL = SAT"},
                        "entry": {"type": "number", "description": "Önerilen giriş fiyatı (TL)"},
                        "stop": {"type": "number", "description": "Önerilen stop-loss seviyesi (TL)"},
                        "target": {"type": "number", "description": "Önerilen kar al hedefi (TL); opsiyonel"},
                        "rationale": {"type": "string", "description": "Kısa gerekçe (1-2 cümle); opsiyonel"},
                    },
                    "required": ["symbol", "decision", "entry", "stop"],
                },
                handler=self.record_recommendation,
            ),
            Tool(
                name="list_recommendations",
                description=(
                    "Son 10 günde verilen AL/SAT önerilerini en yeniden eskiye döner. "
                    "symbol verilirse yalnızca o hissenin önerileri filtrelenir. "
                    "Kullanıcı geçmiş önerine atıf yaptığında veya tekrar değerlendirme istediğinde kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Opsiyonel: belirli bir sembolle filtrele"},
                    },
                    "required": [],
                },
                handler=self.list_recommendations,
            ),
        ]


# ---- helpers -----------------------------------------------------------------

def _to_dict(rec: Recommendation) -> dict:
    d: dict = {
        "id": rec.id,
        "symbol": rec.symbol,
        "decision": rec.decision,
        "entry": rec.entry,
        "stop": rec.stop,
        "created_at": rec.created_at.isoformat(),
    }
    if rec.target is not None:
        d["target"] = rec.target
    if rec.rationale is not None:
        d["rationale"] = rec.rationale
    return d
