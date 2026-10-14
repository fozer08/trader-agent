from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ..analysis import Horizon, evaluate_trade
from ..analysis.service import AnalysisService
from ..calendar import CalendarCoverageError
from ..marketdata.base import ProviderError
from ..repository.watchlist import WatchlistRepository
from .base import Tool

Symbol = Annotated[str, Field(strict=True, pattern=r"^[A-Z0-9]{1,20}$")]
Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class ScanRequest(BaseModel):
    """Vadenin model tarafından açıkça seçilmesini sağlayan tarama sınırıdır."""

    model_config = ConfigDict(extra="forbid")
    horizon: Horizon


class AnalyzeRequest(ScanRequest):
    """Tek çağrıda sınırlı sayıda sembolü incelemek için dış girdiyi doğrular."""

    symbols: list[Symbol] = Field(min_length=1, max_length=10)


class TradeRiskRequest(BaseModel):
    """LLM'in sayısal girdilerini doğrular; plan kuralları risk hesabına aittir."""

    model_config = ConfigDict(extra="forbid")
    entry: Number
    stop: Number
    target: Number
    risk_budget: Number | None = None
    cash_budget: Number | None = None
    round_trip_cost_pct: Number | None = None


class AnalysisTools:
    """LLM'e tarama, detay ve risk hesabı sunan ince araç adaptörüdür.

    Formülleri veya veri pencerelerini tekrarlamaz. Beklenen sembol/veri hatalarını
    sonuçta korur; programlama hatalarını sessizce taramadan düşürmez.
    """

    def __init__(self, analysis: AnalysisService, watchlist: WatchlistRepository) -> None:
        self._analysis = analysis
        self._watchlist = watchlist

    async def scan(self, horizon: Horizon) -> list[dict]:
        request = ScanRequest(horizon=horizon)
        results = await asyncio.gather(*(
            self._one(entry["symbol"], request.horizon) for entry in self._watchlist.list()
        ))
        # Scan and detail share exactly the same calculations. Only payload size differs.
        return [self._summary(result) for result in results]

    async def analyze(self, symbols: list[str], horizon: Horizon) -> list[dict]:
        request = AnalyzeRequest(symbols=symbols, horizon=horizon)
        return list(await asyncio.gather(*(
            self._one(symbol, request.horizon) for symbol in dict.fromkeys(request.symbols)
        )))

    async def evaluate_trade(
        self, entry: float, stop: float, target: float, risk_budget: float | None = None,
        cash_budget: float | None = None, round_trip_cost_pct: float | None = None,
    ) -> dict:
        request = TradeRiskRequest(
            entry=entry, stop=stop, target=target, risk_budget=risk_budget,
            cash_budget=cash_budget, round_trip_cost_pct=round_trip_cost_pct,
        )
        return asdict(evaluate_trade(**request.model_dump()))

    async def _one(self, symbol: str, horizon: Horizon) -> dict:
        try:
            stock = self._watchlist.stock(symbol)
            return {**stock, **await self._analysis.analyze(stock["symbol"], horizon)}
        except (KeyError, ValueError, ProviderError, CalendarCoverageError) as exc:
            return {"symbol": symbol, "error": str(exc)}

    @staticmethod
    def _summary(result: dict) -> dict:
        if "error" in result:
            return result
        daily = result["daily"]
        return {**result, "daily": {key: daily[key] for key in (
            "as_of", "close", "price_vs_ema", "ema_alignment", "ema_fast_period",
            "ema_slow_period", "rsi", "atr_pct", "return_5_pct", "return_20_pct",
            "return_60_pct", "relative_volume", "unavailable",
        )}}

    def as_tool_list(self) -> list[Tool]:
        return [
            Tool("scan", (
                "Takip listesini seçilen vadeyle tara: intraday (aynı seans), swing "
                "(birkaç gün–hafta), position (1–3 ay). Günlük yapı ve gecikmeli seans "
                "gözlemi döner; adayları analyze ile incele. Otomatik AL/SAT veya puan üretmez."
            ), ScanRequest.model_json_schema(), self.scan),
            Tool("analyze", (
                "1–10 sembol için vadeye uygun günlük analiz, referans aralıkları ve seans "
                "gözlemi. as_of, delay_minutes, daily_stale, session_age_minutes ve unavailable "
                "alanlarını dikkate al. delay_minutes yayın gecikmesidir; price_age_minutes "
                "fiyatın yaşıdır. price_kind=close için kapanış tarihini kullan. "
                "Seans fiyatları örneklenmiş/gecikmeli olabilir; "
                "anlık işlem onayı değildir. M5/M15 indikatörü hesaplamaz."
            ), AnalyzeRequest.model_json_schema(), self.analyze),
            Tool("evaluate_trade", (
                "Yeni long planında stop < entry < target için brüt R/R ve yüzdeleri hesapla. "
                "Yalnız kullanıcının verdiği risk_budget/cash_budget ile lot üst sınırı hesaplanır; "
                "bütçeler aynı para biriminde. round_trip_cost_pct verilirse net R/R de döner. "
                "Maliyet bilinmiyorsa brüt sonuç; stop kaybı garanti değildir. Uygunluk filtresi "
                "veya emir değildir. Mevcut pozisyon satışı için yeni long planı uydurma."
            ), TradeRiskRequest.model_json_schema(), self.evaluate_trade),
        ]
