from __future__ import annotations

import asyncio
import dataclasses

from ..analysis import (
    IndicatorSet,
    PriceLevels,
    compute_indicator,
    compute_levels,
    compute_pulse,
)
from ..market.base import MarketDataProvider
from ..types import TimeFrame
from ..utils.logging import get_logger
from .base import Tool

_log = get_logger(__name__)


class AnalysisTools:
    """Provider ve watchlist üzerinden LLM'e sunulan analiz araçları.

    Tüm araçlar hataları exception fırlatmak yerine ``{"error": "..."}``
    dict'i olarak döner; LLM bunu kullanıcıya doğal dille aktarır.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        watchlist: list[dict],
    ) -> None:
        self._provider = provider
        self._watchlist = watchlist
        self._name_map = {e["symbol"]: e.get("name", e["symbol"]) for e in watchlist}

    # ---- Tool handlers -------------------------------------------------------

    async def scan(self) -> list[dict]:
        """Tüm watchlist için kısa özet tarama."""
        results = await asyncio.gather(
            *[self._scan_one(e["symbol"]) for e in self._watchlist],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    async def get_daily_indicators(self, symbols: list[str]) -> list[dict]:
        """Belirli semboller için D1 teknik indikatörler."""
        results = await asyncio.gather(
            *[self._daily_indicators_one(s) for s in symbols],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    async def get_intraday_indicators(self, symbols: list[str]) -> list[dict]:
        """Belirli semboller için M15 + M5 intraday teknik indikatörler."""
        results = await asyncio.gather(
            *[self._intraday_indicators_one(s) for s in symbols],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    async def get_pulse(self, symbols: list[str]) -> list[dict]:
        """Belirli semboller için canlı seans nabzı; seans kapalıysa error döner."""
        results = await asyncio.gather(
            *[self._pulse_one(s) for s in symbols],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    async def get_levels(self, symbols: list[str]) -> list[dict]:
        """Belirli semboller için pivot, PDH/PDL/PDC, haftalık aralık, mum formasyonu."""
        results = await asyncio.gather(
            *[self._levels_one(s) for s in symbols],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    def as_tool_list(self) -> list[Tool]:
        """Claude agent'ına register edilecek tool listesini döndürür."""
        return [
            Tool(
                name="scan",
                description=(
                    "Tüm watchlist için curated kısa özet tarama; geniş piyasa görünümü ve fırsat arama için kullan. "
                    "Her hisse için: daily (son seans bazlı) — EMA trend/hizalama, RSI, ATR, göreceli hacim, mum formasyonu; "
                    "pulse (seans açıksa) — canlı fiyat, % değişim (gap dahil/hariç), gap, gün içi yüksek/düşük/aralık, "
                    "göreceli hacim, EMA'ya konum. "
                    "Belirli sembol(ler) için daha derinlemesine bilgi gerekiyorsa get_daily_indicators / get_intraday_indicators / get_pulse / get_levels kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
                handler=self.scan,
            ),
            Tool(
                name="get_daily_indicators",
                description=(
                    "Belirli semboller için D1 (günlük) teknik indikatörler: EMA trend/hizalama, "
                    "RSI + bullish/bearish uyumsuzluk, MACD histogram, ATR, Bollinger width/pct_b. "
                    "Yapısal trend ve karar için kullan; intraday momentum için get_intraday_indicators."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "BIST ticker sembolleri listesi.",
                        },
                    },
                    "required": ["symbols"],
                },
                handler=self.get_daily_indicators,
            ),
            Tool(
                name="get_intraday_indicators",
                description=(
                    "Belirli semboller için M15 + M5 intraday teknik indikatörler. "
                    "Her hisse için iki timeframe paralel döner — EMA, RSI, ATR, Bollinger. "
                    "Seans açıkken intraday momentum/giriş timing'i için kullan; yapısal trend için get_daily_indicators."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "BIST ticker sembolleri listesi.",
                        },
                    },
                    "required": ["symbols"],
                },
                handler=self.get_intraday_indicators,
            ),
            Tool(
                name="get_pulse",
                description=(
                    "Belirli semboller için canlı seans nabzı: anlık fiyat, change_pct (gap dahil), "
                    "open_change_pct (gap hariç salt intraday), gap, gün içi aralık/konum, "
                    "göreceli hacim, price_vs_ema. "
                    "Seans kapalıyken sembol için error döner — yalnızca seans açıkken anlamlı."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "BIST ticker sembolleri listesi.",
                        },
                    },
                    "required": ["symbols"],
                },
                handler=self.get_pulse,
            ),
            Tool(
                name="get_levels",
                description=(
                    "Belirli semboller için fiyat yapısı: pivot (PP/R1/R2/S1/S2), PDH/PDL/PDC, "
                    "haftalık aralık, mum formasyonu, göreceli hacim. "
                    "Destek/direnç, stop seviyesi veya hedef hesabı için kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                            "description": "BIST ticker sembolleri listesi.",
                        },
                    },
                    "required": ["symbols"],
                },
                handler=self.get_levels,
            ),
        ]

    # ---- private -------------------------------------------------------------

    async def _scan_one(self, symbol: str) -> dict:
        try:
            bars, today_bar = await asyncio.gather(
                self._provider.get_daily(symbol, period=30),
                self._provider.get_today(symbol),
            )
            if len(bars) < 2:
                return _error(symbol, "insufficient data")

            ind = compute_indicator(bars)
            lvl = compute_levels(bars)

            result: dict = {
                "symbol": symbol,
                "name": self._name_map.get(symbol, symbol),
                "daily": {
                    "close": lvl.prev_close,
                    "ema_trend": ind.ema_trend,
                    "ema_alignment": ind.ema_alignment,
                    "rsi": ind.rsi,
                    "atr": ind.atr,
                    "relative_volume": lvl.relative_volume,
                    "candle": lvl.candle.name if lvl.candle else None,
                },
            }
            result["pulse"] = (
                dataclasses.asdict(compute_pulse(today_bar, bars, ind.ema_fast))
                if today_bar is not None
                else None
            )
            return result
        except Exception as exc:
            _log.warning("scan_one failed for %s: %s", symbol, exc, exc_info=True)
            return _error(symbol, str(exc))

    async def _daily_indicators_one(self, symbol: str) -> dict:
        try:
            bars = await self._provider.get_daily(symbol, period=100)
            if len(bars) < 2:
                return _error(symbol, "insufficient data")
            return {
                "symbol": symbol,
                "name": self._name_map.get(symbol, symbol),
                "indicators": _indicators_to_dict(compute_indicator(bars)),
            }
        except Exception as exc:
            _log.warning("daily_indicators_one failed for %s: %s", symbol, exc, exc_info=True)
            return _error(symbol, str(exc))

    async def _intraday_indicators_one(self, symbol: str) -> dict:
        try:
            m15_bars, m5_bars = await asyncio.gather(
                self._provider.get_intraday(symbol, TimeFrame.M15),
                self._provider.get_intraday(symbol, TimeFrame.M5),
            )
        except Exception as exc:
            _log.warning("intraday_indicators_one fetch failed for %s: %s", symbol, exc, exc_info=True)
            return _error(symbol, str(exc))

        result: dict = {
            "symbol": symbol,
            "name": self._name_map.get(symbol, symbol),
        }
        for label, bars in (("m15", m15_bars), ("m5", m5_bars)):
            if len(bars) < 2:
                continue
            try:
                result[label] = _indicators_to_dict(compute_indicator(bars))
            except Exception as exc:
                _log.warning("%s compute failed for %s: %s", label, symbol, exc)

        if "m15" not in result and "m5" not in result:
            return _error(symbol, "no intraday data")
        return result

    async def _pulse_one(self, symbol: str) -> dict:
        try:
            bars, today_bar = await asyncio.gather(
                self._provider.get_daily(symbol, period=30),
                self._provider.get_today(symbol),
            )
            if today_bar is None:
                return _error(symbol, "no live session data")
            if len(bars) < 2:
                return _error(symbol, "insufficient daily data")
            ind = compute_indicator(bars)
            pulse = compute_pulse(today_bar, bars, ind.ema_fast)
            return {
                "symbol": symbol,
                "name": self._name_map.get(symbol, symbol),
                "pulse": dataclasses.asdict(pulse),
            }
        except Exception as exc:
            _log.warning("pulse_one failed for %s: %s", symbol, exc, exc_info=True)
            return _error(symbol, str(exc))

    async def _levels_one(self, symbol: str) -> dict:
        try:
            bars = await self._provider.get_daily(symbol, period=30)
            if len(bars) < 2:
                return _error(symbol, "insufficient data")
            return {
                "symbol": symbol,
                "name": self._name_map.get(symbol, symbol),
                "levels": _levels_to_dict(compute_levels(bars)),
            }
        except Exception as exc:
            _log.warning("levels_one failed for %s: %s", symbol, exc, exc_info=True)
            return _error(symbol, str(exc))

# ---- Helpers -----------------------------------------------------------------

def _error(symbol: str, msg: str) -> dict:
    return {"symbol": symbol, "error": msg}


def _indicators_to_dict(ind: IndicatorSet) -> dict:
    return {
        "ema_trend": ind.ema_trend,
        "ema_alignment": ind.ema_alignment,
        "rsi": ind.rsi,
        "rsi_divergence": ind.rsi_divergence,
        "macd_histogram": ind.macd.histogram if ind.macd else None,
        "atr": ind.atr,
        "bb_width": ind.bb.width if ind.bb else None,
        "bb_pct_b": ind.bb.pct_b if ind.bb else None,
    }


def _levels_to_dict(lvl: PriceLevels) -> dict:
    return {
        "prev_high": lvl.prev_high,
        "prev_low": lvl.prev_low,
        "prev_close": lvl.prev_close,
        "pivot": dataclasses.asdict(lvl.pivot),
        "weekly_high": lvl.weekly_high,
        "weekly_low": lvl.weekly_low,
        "candle": dataclasses.asdict(lvl.candle) if lvl.candle else None,
        "relative_volume": lvl.relative_volume,
    }
