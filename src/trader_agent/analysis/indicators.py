from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd
import ta

from ..market.types import Bar, TimeFrame


@dataclass(frozen=True)
class Profile:
    """Bir timeframe için indikatör parametrelerini tanımlar.

    Her timeframe farklı bir Profile kullanır; PROFILES sözlüğünden seçilir
    ya da compute() çağrısında açıkça geçilebilir.
    """
    ema_fast: int
    ema_slow: int | None  # None ise hesaplanmaz
    rsi_period: int
    use_macd: bool
    atr_period: int
    use_bollinger: bool
    bb_period: int
    use_rsi_divergence: bool


PROFILES: dict[TimeFrame, Profile] = {
    TimeFrame.D1: Profile(
        ema_fast=9,
        ema_slow=21,
        rsi_period=14,
        use_macd=True,
        atr_period=14,
        use_bollinger=True,
        bb_period=20,
        use_rsi_divergence=True,
    ),
    TimeFrame.M15: Profile(
        ema_fast=8,
        ema_slow=13,
        rsi_period=9,
        use_macd=False,
        atr_period=7,
        use_bollinger=True,
        bb_period=20,
        use_rsi_divergence=False,
    ),
    TimeFrame.M5: Profile(
        ema_fast=9,
        ema_slow=None,
        rsi_period=14,
        use_macd=False,
        atr_period=5,
        use_bollinger=False,
        bb_period=20,
        use_rsi_divergence=False,
    ),
}


@dataclass(frozen=True)
class MACDResult:
    """MACD indikatörünün üç bileşeni."""
    macd: float      # MACD hattı (hızlı EMA − yavaş EMA)
    signal: float    # Sinyal hattı (MACD'nin EMA'sı)
    histogram: float # MACD − sinyal


@dataclass(frozen=True)
class BollingerResult:
    """Bollinger Bands bileşenleri ve bağlam metrikleri."""
    upper: float   # Üst band (middle + 2σ)
    middle: float  # Orta band (SMA)
    lower: float   # Alt band (middle − 2σ)
    width: float   # (upper − lower) / middle; düşük = sıkışma, yüksek = genişleme
    pct_b: float   # (close − lower) / (upper − lower); 0=alt band, 1=üst band


@dataclass(frozen=True)
class IndicatorSet:
    """Tek bir bar anının hesaplanmış teknik indikatör anlık görüntüsü.

    Zaman serisi değil; bars[-1]'in değerlerini taşır.
    Yeterli veri yoksa ilgili alan None döner.
    """
    symbol: str
    timeframe: TimeFrame
    datetime: datetime
    profile: Profile

    ema_fast: float | None
    ema_slow: float | None
    ema_trend: str | None      # "above" | "below" — close'un ema_fast'a göre konumu
    ema_alignment: str | None  # "bullish" | "bearish" — ema_fast'ın ema_slow'a göre konumu

    rsi: float | None
    rsi_divergence: str | None  # "bullish" | "bearish" — fiyat/RSI uyumsuzluğu
    macd: MACDResult | None
    atr: float | None
    bb: BollingerResult | None


def compute(bars: list[Bar], profile: Profile | None = None) -> IndicatorSet:
    """Bar listesinden profil tabanlı teknik indikatör seti hesaplar.

    Profile verilmezse bars'ın timeframe'inden otomatik seçilir.
    Yeterli veri yoksa ilgili alan None döner.

    Raises:
        ValueError: bars boşsa, farklı semboller içeriyorsa veya
                    timeframe için tanımlı profil yoksa.
    """
    if not bars:
        raise ValueError("bars cannot be empty.")

    symbol = bars[0].symbol
    if any(b.symbol != symbol for b in bars):
        raise ValueError("All bars must belong to the same symbol.")

    tf = bars[0].timeframe
    if any(b.timeframe is not tf for b in bars):
        raise ValueError("All bars must have the same timeframe.")
    p = profile or PROFILES.get(tf)
    if p is None:
        raise ValueError(f"No profile defined for timeframe {tf.name}. Pass a profile explicitly.")

    df = pd.DataFrame({
        "open":   [b.open for b in bars],
        "high":   [b.high for b in bars],
        "low":    [b.low for b in bars],
        "close":  [b.close for b in bars],
        "volume": [b.volume for b in bars],
    })

    close = df["close"]

    ema_fast = _val(ta.trend.ema_indicator(close, window=p.ema_fast))
    ema_slow = _val(ta.trend.ema_indicator(close, window=p.ema_slow)) if p.ema_slow else None
    last_close = bars[-1].close
    ema_trend = ("above" if last_close >= ema_fast else "below") if ema_fast is not None else None
    ema_alignment = (
        ("bullish" if ema_fast >= ema_slow else "bearish")
        if ema_fast is not None and ema_slow is not None
        else None
    )

    rsi_series = ta.momentum.rsi(close, window=p.rsi_period)
    rsi = _val(rsi_series)

    rsi_divergence = _detect_rsi_divergence(close, rsi_series) if p.use_rsi_divergence else None

    macd = None
    if p.use_macd:
        ind = ta.trend.MACD(close)
        mv, sv, hv = _val(ind.macd()), _val(ind.macd_signal()), _val(ind.macd_diff())
        if mv is not None and sv is not None and hv is not None:
            macd = MACDResult(macd=mv, signal=sv, histogram=hv)

    atr = (
        _val(ta.volatility.average_true_range(df["high"], df["low"], close, window=p.atr_period))
        if len(df) >= p.atr_period
        else None
    )

    bb = None
    if p.use_bollinger and len(df) >= p.bb_period:
        ind_bb = ta.volatility.BollingerBands(close, window=p.bb_period, window_dev=2)
        upper_v = _val(ind_bb.bollinger_hband())
        middle_v = _val(ind_bb.bollinger_mavg())
        lower_v = _val(ind_bb.bollinger_lband())
        if upper_v is not None and middle_v is not None and lower_v is not None and middle_v > 0:
            band_range = upper_v - lower_v
            pct_b_v = (last_close - lower_v) / band_range if band_range > 0 else 0.5
            bb = BollingerResult(
                upper=upper_v,
                middle=middle_v,
                lower=lower_v,
                width=round((upper_v - lower_v) / middle_v, 4),
                pct_b=round(pct_b_v, 3),
            )

    return IndicatorSet(
        symbol=symbol,
        timeframe=tf,
        datetime=bars[-1].datetime,
        profile=p,
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        ema_trend=ema_trend,
        ema_alignment=ema_alignment,
        rsi=rsi,
        rsi_divergence=rsi_divergence,
        macd=macd,
        atr=atr,
        bb=bb,
    )


def _val(series: pd.Series) -> float | None:
    """Pandas serisinin son elemanını döner; NaN ise None."""
    v = series.iloc[-1]
    return None if pd.isna(v) else float(v)


def _detect_rsi_divergence(close: pd.Series, rsi: pd.Series, lookback: int = 20) -> str | None:
    """Son ``lookback`` barda fiyat/RSI uyumsuzluğunu tespit eder.

    Bearish divergence: fiyat yeni zirve yaparken RSI yapmıyor → momentum zayıflıyor.
    Bullish divergence: fiyat yeni dip yaparken RSI yapmıyor → satış baskısı azalıyor.
    """
    if len(close) < lookback + 2:
        return None

    c = close.iloc[-lookback:].reset_index(drop=True)
    r = rsi.iloc[-lookback:].reset_index(drop=True)
    n = len(c)

    highs = [
        i for i in range(1, n - 1)
        if c.iloc[i] > c.iloc[i - 1] and c.iloc[i] > c.iloc[i + 1]
        and not pd.isna(r.iloc[i])
    ]
    lows = [
        i for i in range(1, n - 1)
        if c.iloc[i] < c.iloc[i - 1] and c.iloc[i] < c.iloc[i + 1]
        and not pd.isna(r.iloc[i])
    ]

    if len(highs) >= 2:
        i1, i2 = highs[-2], highs[-1]
        if c.iloc[i2] > c.iloc[i1] and r.iloc[i2] < r.iloc[i1]:
            return "bearish"

    if len(lows) >= 2:
        i1, i2 = lows[-2], lows[-1]
        if c.iloc[i2] < c.iloc[i1] and r.iloc[i2] > r.iloc[i1]:
            return "bullish"

    return None
