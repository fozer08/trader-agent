from __future__ import annotations

import asyncio
import dataclasses

from ..analysis import compute, compute_levels
from ..analysis.levels import PriceLevels
from ..analysis.technical import IndicatorSet
from ..market.provider_base import MarketDataProvider
from ..market.types import Bar, TimeFrame
from ..utils.logging import get_logger
from .base import Tool

_log = get_logger(__name__)


class AnalysisTools:
    """Provider ve watchlist üzerinden Claude'a sunulan analiz araçları.

    Tüm araçlar hataları exception fırlatmak yerine ``{"error": "..."}``
    dict'i olarak döner; Claude bunu kullanıcıya doğal dille aktarır.
    """

    def __init__(
        self,
        provider: MarketDataProvider,
        watchlist: list[dict],
    ) -> None:
        self._provider = provider
        self._watchlist = watchlist

    # ---- Tool handlers -------------------------------------------------------

    async def scan(self, symbols: list[str] | None = None) -> list[dict]:
        """D1 özet tarama — hızlı piyasa görünümü."""
        if symbols is not None:
            watchlist_map = {e["symbol"]: e for e in self._watchlist}
            entries = [watchlist_map.get(s, {"symbol": s}) for s in symbols]
        else:
            entries = self._watchlist

        results = await asyncio.gather(
            *[self._scan_one(entry) for entry in entries],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    async def get_technicals(self, symbols: list[str]) -> list[dict]:
        """D1 teknik analiz; seans açıksa session ve M15/M5 eklenir."""
        watchlist_map = {e["symbol"]: e.get("name", e["symbol"]) for e in self._watchlist}
        results = await asyncio.gather(
            *[self._technicals_one(symbol, watchlist_map.get(symbol, symbol)) for symbol in symbols],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, dict)]

    async def get_levels(self, symbol: str) -> dict:
        """Fiyat yapısı: pivot, PDH/PDL/PDC, haftalık aralık, mum formasyonu."""
        try:
            bars = await self._provider.get_daily(symbol, period=30)
            if len(bars) < 2:
                return {"symbol": symbol, "error": "insufficient data"}
            lvl = compute_levels(bars)
            return {
                "symbol": symbol,
                "levels": _levels_to_dict(lvl),
            }
        except Exception as exc:
            return {"symbol": symbol, "error": str(exc)}

    def as_tool_list(self) -> list[Tool]:
        """Claude agent'ına register edilecek tool listesini döndürür."""
        return [
            Tool(
                name="scan",
                description=(
                    "Birden fazla hisseyi hızlıca tarar. "
                    "symbols verilmezse tüm takip listesi taranır. "
                    "Her hisse için: d1 (önceki kapanış bazlı) — EMA trend/hizalama, RSI, ATR, göreceli hacim, mum; "
                    "session (seans açıksa) — canlı fiyat, % değişim, gap, gün içi yüksek/düşük/aralık, "
                    "canlı fiyatın D1 EMA'ya göre konumu; seans kapalıysa null. "
                    "Geniş piyasa taraması, fırsat arama veya hisse önerisi için kullan. "
                    "Belirli bir hisse için indikatör detayı, giriş zamanlaması veya stop/hedef gerekiyorsa get_technicals kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Taranacak BIST ticker sembolleri. "
                                "Belirtilmezse tüm takip listesi taranır."
                            ),
                        },
                    },
                    "required": [],
                },
                handler=self.scan,
            ),
            Tool(
                name="get_technicals",
                description=(
                    "Az sayıda belirli hisse için kapsamlı teknik analiz yapar; geniş tarama için scan kullan. "
                    "d1.indicators: EMA trend/hizalama, RSI (+ bullish/bearish uyumsuzluk), MACD histogram, ATR, Bollinger width/pct_b. "
                    "d1.levels: pivot (PP/R1/R2/S1/S2), PDH/PDL/PDC, haftalık aralık, mum formasyonu, göreceli hacim. "
                    "session (seans açıksa): canlı fiyat, % değişim, gap, gün içi aralık/konum, price_vs_ema; seans kapalıysa null. "
                    "m15/m5: seans açıksa gün içi EMA, RSI, ATR, Bollinger; seans kapalıysa bu alanlar dönmez. "
                    "Detaylı analiz, giriş zamanlaması veya stop/hedef hesaplamak için kullan."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbols": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Analiz edilecek BIST ticker sembolleri listesi.",
                        },
                    },
                    "required": ["symbols"],
                },
                handler=self.get_technicals,
            ),
            Tool(
                name="get_levels",
                description=(
                    "Tek bir hisse için yalnızca fiyat yapısını döner: "
                    "pivot (PP/R1/R2/S1/S2), PDH/PDL/PDC, haftalık aralık, mum formasyonu, göreceli hacim. "
                    "Sadece destek/direnç veya stop seviyesi sorulduğunda kullan. "
                    "EMA, RSI gibi indikatörler de gerekiyorsa get_technicals kullan (levels zaten içinde döner)."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "BIST ticker sembolü, örn: THYAO, AKBNK",
                        },
                    },
                    "required": ["symbol"],
                },
                handler=self.get_levels,
            ),
        ]

    # ---- private -------------------------------------------------------------

    async def _scan_one(self, entry: dict) -> dict:
        symbol = entry["symbol"]
        try:
            bars, today_bar = await asyncio.gather(
                self._provider.get_daily(symbol, period=30),
                self._provider.get_today(symbol),
            )
            if len(bars) < 2:
                return {"symbol": symbol, "error": "insufficient data"}

            ind = compute(bars)
            lvl = compute_levels(bars)

            return {
                "symbol": symbol,
                "name": entry.get("name", symbol),
                "d1": {
                    "close": bars[-1].close,
                    "ema_trend": ind.ema_trend,
                    "ema_alignment": ind.ema_alignment,
                    "rsi": ind.rsi,
                    "atr": ind.atr,
                    "relative_volume": lvl.relative_volume,
                    "candle": lvl.candle.name if lvl.candle else None,
                },
                "session": _build_session(today_bar, lvl.prev_close, bars, ind.ema_fast),
            }
        except Exception as exc:
            return {"symbol": symbol, "error": str(exc)}

    async def _technicals_one(self, symbol: str, name: str) -> dict:
        try:
            bars, today_bar, m15_bars, m5_bars = await asyncio.gather(
                self._provider.get_daily(symbol, period=100),
                self._provider.get_today(symbol),
                self._provider.get_intraday(symbol, TimeFrame.M15),
                self._provider.get_intraday(symbol, TimeFrame.M5),
            )
        except Exception as exc:
            return {"symbol": symbol, "error": str(exc)}

        if len(bars) < 2:
            return {"symbol": symbol, "error": "Insufficient daily data."}

        try:
            indicators = compute(bars)
            levels = compute_levels(bars)
        except Exception as exc:
            return {"symbol": symbol, "error": str(exc)}

        session_active = bool(m15_bars or m5_bars)

        m15: dict | None = None
        if len(m15_bars) >= 2:
            try:
                m15 = _indicators_to_dict(compute(m15_bars))
            except Exception as exc:
                _log.warning("M15 compute failed for %s: %s", symbol, exc)

        m5: dict | None = None
        if len(m5_bars) >= 2:
            try:
                m5 = _indicators_to_dict(compute(m5_bars))
            except Exception as exc:
                _log.warning("M5 compute failed for %s: %s", symbol, exc)

        result: dict = {
            "symbol": symbol,
            "name": name,
            "session_active": session_active,
            "d1": {
                "close": bars[-1].close,
                "indicators": _indicators_to_dict(indicators),
                "levels": _levels_to_dict(levels),
            },
            "session": _build_session(today_bar, levels.prev_close, bars, indicators.ema_fast),
        }

        if session_active:
            result["m15"] = m15
            result["m5"] = m5

        return result


# ---- Session builder ---------------------------------------------------------

def _build_session(
    today_bar: Bar | None,
    prev_close: float,
    bars: list[Bar],
    ema_fast: float | None,
) -> dict | None:
    if today_bar is None:
        return None
    day_range = today_bar.high - today_bar.low
    range_position = (
        round((today_bar.close - today_bar.low) / day_range * 100, 1)
        if day_range > 0 else 50.0
    )
    return {
        "current_price": today_bar.close,
        "change_pct": _change_pct(today_bar.close, prev_close),
        "gap_pct": _change_pct(today_bar.open, prev_close),
        "high": today_bar.high,
        "low": today_bar.low,
        "range_pct": round((today_bar.high - today_bar.low) / prev_close * 100, 2) if prev_close > 0 else None,
        "range_position": range_position,
        "volume": today_bar.volume,
        "relative_volume": _session_relative_volume(today_bar, bars),
        "price_vs_ema": _price_vs_ema(today_bar.close, ema_fast),
    }


# ---- Helpers -----------------------------------------------------------------

def _change_pct(price: float, prev_close: float) -> float | None:
    if prev_close <= 0:
        return None
    return round((price - prev_close) / prev_close * 100, 2)


def _price_vs_ema(close: float, ema_fast: float | None) -> str | None:
    if ema_fast is None:
        return None
    return "above" if close >= ema_fast else "below"


def _session_relative_volume(today_bar: Bar, bars: list[Bar]) -> float | None:
    if today_bar.volume is None:
        return None
    history = [b.volume for b in bars[-21:-1] if b.volume is not None]
    if not history:
        return None
    avg = sum(history) / len(history)
    return round(today_bar.volume / avg, 2) if avg > 0 else None


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
