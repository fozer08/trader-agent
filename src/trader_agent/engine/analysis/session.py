from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..marketdata.types import BarData, OHLCQuality, SessionSnapshot


@dataclass(frozen=True)
class SessionAnalysis:
    """Gecikmeli seans gözlemini taşır; örneklenen fiyatları gerçek OHLC saymaz.

    İlk gözlem açılış işlemi olmayabilir. Bu nedenle gap, VWAP, ATR veya gün
    içi göreli hacim üretmez; gözlenen aralık ve önceki kapanış bağlamını sunar.
    """

    as_of: datetime
    observed_open: float
    observed_high: float
    observed_low: float
    price: float
    ohlc_kind: OHLCQuality
    previous_close: float | None
    change_pct: float | None
    range_position_pct: float | None


def analyze_session(snapshot: SessionSnapshot, daily: BarData) -> SessionAnalysis:
    """Snapshot'tan önceki günlük kapanışı kullanır; aynı günü referans almaz."""
    tz = snapshot.ohlcv.period_start.tzinfo
    previous = [b for b in daily.bars
                if b.period_start.astimezone(tz).date() < snapshot.ohlcv.period_start.date()]
    close = previous[-1].close if previous else None
    bar = snapshot.ohlcv
    spread = bar.high - bar.low
    return SessionAnalysis(
        as_of=snapshot.as_of, observed_open=bar.open, observed_high=bar.high,
        observed_low=bar.low, price=bar.close, ohlc_kind=bar.ohlc_kind,
        previous_close=close, change_pct=None if close is None else (bar.close / close - 1) * 100,
        range_position_pct=(bar.close - bar.low) / spread * 100 if spread > 0 else None,
    )
