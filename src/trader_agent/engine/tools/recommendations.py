from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..analysis import Horizon, RiskLevel
from ..recommendations import RecommendationService
from .base import Tool


class RecommendationInput(BaseModel):
    """Model ve HTTP üreticilerinden gelen somut planı aynı sınır şemasıyla doğrular."""

    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(min_length=1, max_length=20)
    entry_min: float = Field(gt=0, allow_inf_nan=False, strict=True)
    entry_max: float = Field(gt=0, allow_inf_nan=False, strict=True)
    stop_loss: float = Field(gt=0, allow_inf_nan=False, strict=True)
    target: float = Field(gt=0, allow_inf_nan=False, strict=True)
    horizon: Horizon
    rationale: str = Field(min_length=1, max_length=400)
    data_as_of: datetime
    risk_level: RiskLevel | None = None


class RecommendationTools:
    """Asistanın doğrulanmış planını günlük karta dönüştürür; portföye kendiliğinden yazmaz."""

    def __init__(self, service: RecommendationService, version: str) -> None:
        self.service = service
        self.version = version

    async def publish_recommendation(self, **values: object) -> dict:
        try:
            data = RecommendationInput.model_validate(values)
            rec = await self.service.publish(
                **data.model_dump(), producer="assistant", producer_version=self.version
            )
            return {"recommendation": rec.to_dict()}
        except (ValueError, KeyError) as exc:
            return {"error": str(exc)}

    def as_tool_list(self) -> list[Tool]:
        return [
            Tool(
                name="publish_recommendation",
                description=(
                    "Somut, veriye dayalı long planını bugünkü Önerilenler listesine ve sohbet kartına ekler. "
                    "Önce analyze ve evaluate_trade kullan; fiyat/veri zamanı uydurma. "
                    "Stop < entry_min <= entry_max < target olmalı. Genel hisse yorumları için kullanma. "
                    "Bu işlem pozisyon açmaz; kullanıcı karttan lot ve aralık içindeki fiyatı seçer."
                ),
                input_schema=RecommendationInput.model_json_schema(),
                handler=self.publish_recommendation,
                mutates=True,
            )
        ]
