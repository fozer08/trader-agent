"""Completed-session calculations; no providers, clocks, LLMs or trade verdicts."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from ..marketdata.types import BarData, OHLCV, TimeFrame

Horizon = Literal["intraday", "swing", "position"]
RiskLevel = Literal["low", "moderate", "high"]
PricePosition = Literal["above", "below", "equal"]


@dataclass(frozen=True)
class AnalysisPolicy:
    """Vadenin günlük referans pencerelerini tek yerde tutar; işlem kararı üretmez.

    history_bars, tarama ve detayda aynı EMA başlangıcını sağlar. Parametreler
    test edilecek başlangıç seçimleridir; kârlılık veya optimumluk iddiası değildir.
    """

    ema_fast_period: int
    ema_slow_period: int

    @property
    def history_bars(self) -> int:
        return self.ema_slow_period * 5


def policy_for(horizon: Horizon) -> AnalysisPolicy:
    match horizon:
        case "intraday":
            return AnalysisPolicy(9, 21)
        case "swing":
            return AnalysisPolicy(20, 50)
        case "position":
            return AnalysisPolicy(20, 60)
        case _:
            raise ValueError(f"Unknown horizon: {horizon}")


@dataclass(frozen=True)
class ReferenceLevels:
    """Gerçek günlük aralıkları taşır; türetilmiş pivotları destek/direnç diye sunmaz.

    rolling alanları takvim haftası değil, son N tamamlanmış seanstır. Eksik
    yüksek/düşük veya kısa geçmiş varsa ilgili pencere None kalır.
    """

    last_high: float | None
    last_low: float | None
    last_close: float
    rolling_5_high: float | None
    rolling_5_low: float | None
    rolling_20_high: float | None
    rolling_20_low: float | None
    rolling_60_high: float | None
    rolling_60_low: float | None


@dataclass(frozen=True)
class DailyAnalysis:
    """Agent ve web'in aynı günlük ölçümleri kullanması için tek analiz sonucudur.

    Fiyat konumu, EMA hizalaması ve getiri ayrı kavramlardır; hiçbiri AL/SAT
    sinyali değildir. unavailable alanı eksik hesapların nedenlerini açıklar.
    """

    symbol: str
    horizon: Horizon
    as_of: datetime
    close: float
    bars_available: int
    history_required: int
    ema_fast_period: int
    ema_slow_period: int
    ema_fast: float | None
    ema_slow: float | None
    price_vs_ema: PricePosition | None
    ema_alignment: Literal["bullish", "bearish", "flat"] | None
    rsi: float | None
    atr: float | None
    atr_pct: float | None
    return_5_pct: float | None
    return_20_pct: float | None
    return_60_pct: float | None
    relative_volume: float | None
    levels: ReferenceLevels
    unavailable: dict[str, str]


def price_position(price: float, reference: float | None) -> PricePosition | None:
    if reference is None:
        return None
    return "above" if price > reference else "below" if price < reference else "equal"


def analyze_daily(series: BarData, horizon: Horizon) -> DailyAnalysis:
    """D1 veriyi sabit geçmişle analiz eder; eksik hesapları sıfırla doldurmaz."""
    if series.timeframe is not TimeFrame.D1 or not series.bars:
        raise ValueError("Daily analysis requires non-empty D1 bars.")
    policy = policy_for(horizon)
    bars = series.bars[-policy.history_bars:]
    closes = [bar.close for bar in bars]
    missing: dict[str, str] = {}
    fast = slow = None
    if len(bars) >= policy.history_bars:
        fast = _ema(closes, policy.ema_fast_period)
        slow = _ema(closes, policy.ema_slow_period)
    else:
        missing["ema"] = f"Requires {policy.history_bars} completed sessions; received {len(bars)}."

    # Wilder smoothing uses one fixed 100-session seed window across all horizons.
    rsi = atr = None
    if len(bars) >= 100:
        rsi = _rsi(closes[-100:])
        if all(bar.ohlc_kind in ("reported", "range_only") for bar in bars[-100:]):
            atr = _atr(bars[-100:])
        else:
            missing["atr"] = "Requires reported high/low throughout the 100-session window."
    else:
        missing["rsi"] = missing["atr"] = "Requires 100 completed sessions for smoothing."
    if rsi is None and "rsi" not in missing:
        missing["rsi"] = "No price movement in the calculation window."

    volume = None
    volumes = [bar.volume for bar in bars[-21:]]
    if len(volumes) == 21 and all(v is not None for v in volumes):
        known = [v for v in volumes if v is not None]
        average = sum(known[:-1]) / 20
        if average > 0:
            volume = known[-1] / average
    if volume is None:
        missing["relative_volume"] = "Requires latest volume and 20 preceding volumes with a positive mean."

    def bounds(count: int) -> tuple[float | None, float | None]:
        window = bars[-count:]
        if len(window) != count or any(bar.ohlc_kind not in ("reported", "range_only") for bar in window):
            missing[f"range_{count}"] = f"Requires {count} sessions with reported high/low."
            return None, None
        return max(bar.high for bar in window), min(bar.low for bar in window)

    def change(count: int) -> float | None:
        if len(closes) <= count:
            missing[f"return_{count}_pct"] = f"Requires {count + 1} completed closes."
            return None
        return (closes[-1] / closes[-count - 1] - 1) * 100

    last_high, last_low = bounds(1)
    h5, l5 = bounds(5)
    h20, l20 = bounds(20)
    h60, l60 = bounds(60)
    alignment: Literal["bullish", "bearish", "flat"] | None = None if fast is None or slow is None else (
        "bullish" if fast > slow else "bearish" if fast < slow else "flat"
    )
    return DailyAnalysis(
        symbol=series.symbol, horizon=horizon, as_of=bars[-1].period_start,
        close=closes[-1], bars_available=len(bars), history_required=policy.history_bars,
        ema_fast_period=policy.ema_fast_period, ema_slow_period=policy.ema_slow_period,
        ema_fast=fast, ema_slow=slow, price_vs_ema=price_position(closes[-1], fast),
        ema_alignment=alignment, rsi=rsi, atr=atr,
        atr_pct=None if atr is None else atr / closes[-1] * 100,
        return_5_pct=change(5), return_20_pct=change(20), return_60_pct=change(60),
        relative_volume=volume,
        levels=ReferenceLevels(last_high, last_low, closes[-1], h5, l5, h20, l20, h60, l60),
        unavailable=missing,
    )


def _ema(values: list[float], period: int) -> float:
    result = values[0]
    alpha = 2 / (period + 1)
    for value in values[1:]:
        result += alpha * (value - result)
    return result


def _wilder(values: list[float], period: int = 14) -> float:
    result = sum(values[:period]) / period
    for value in values[period:]:
        result = (result * (period - 1) + value) / period
    return result


def _rsi(closes: list[float]) -> float | None:
    moves = [current - previous for previous, current in zip(closes, closes[1:])]
    gains = _wilder([max(move, 0) for move in moves])
    losses = _wilder([max(-move, 0) for move in moves])
    if gains == losses == 0:
        return None
    return 100 if losses == 0 else 100 - 100 / (1 + gains / losses)


def _atr(bars: list[OHLCV]) -> float:
    ranges = [max(bar.high - bar.low, abs(bar.high - prev.close), abs(bar.low - prev.close))
              for prev, bar in zip(bars, bars[1:])]
    return _wilder(ranges)
