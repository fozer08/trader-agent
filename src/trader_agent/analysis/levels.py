from __future__ import annotations

from dataclasses import dataclass

from ..market.types import Bar, IntradaySnapshot


@dataclass(frozen=True)
class PivotLevels:
    """Klasik pivot noktası seviyeleri.

    Formül: PP = (H + L + C) / 3
    R1 = 2·PP − L,  R2 = PP + (H − L)
    S1 = 2·PP − H,  S2 = PP − (H − L)
    """
    pp: float  # Pivot noktası
    r1: float  # Birinci direnç
    r2: float  # İkinci direnç
    s1: float  # Birinci destek
    s2: float  # İkinci destek


@dataclass(frozen=True)
class CandlePattern:
    """Tespit edilen mum formasyonu."""
    name: str    # "doji", "hammer", "shooting_star", "bullish_engulfing", "bearish_engulfing"
    bullish: bool


@dataclass(frozen=True)
class SessionSnapshot:
    """Seans içindeki günün anlık görünümü ve dün kapanışına göre değişim."""
    open: float
    high: float
    low: float
    close: float           # Şu anki fiyat
    volume: float | None
    change_pct: float      # (close − prev_close) / prev_close × 100
    gap_pct: float         # (open − prev_close) / prev_close × 100
    range_pct: float       # (high − low) / prev_close × 100
    range_position: float  # (close − low) / (high − low) × 100; 0=dip, 100=tepe


@dataclass(frozen=True)
class PriceLevels:
    """Bir referans günden türetilen fiyat seviyeleri ve bağlam bilgisi."""
    prev_high: float    # Referans günün en yüksek fiyatı
    prev_low: float     # Referans günün en düşük fiyatı
    prev_close: float   # Referans günün kapanış fiyatı
    pivot: PivotLevels
    weekly_high: float  # Son 5 barın en yüksek fiyatı
    weekly_low: float   # Son 5 barın en düşük fiyatı
    candle: CandlePattern | None
    relative_volume: float | None  # Güncel hacim / 20 günlük ortalama hacim


def compute_levels(bars: list[Bar]) -> PriceLevels:
    """Bar listesinden fiyat seviyeleri ve bağlam bilgisi hesaplar.

    bars[-1] referans gün (genellikle dün); pivot, PDH/PDL/PDC ve
    haftalık aralık bu bara göre hesaplanır. Mum formasyonu tespiti
    için bars[-2] önceki bar olarak kullanılır.

    Raises:
        ValueError: bars 2'den az eleman içeriyorsa.
    """
    if len(bars) < 2:
        raise ValueError("At least 2 bars are required.")

    symbol = bars[0].symbol
    if any(b.symbol != symbol for b in bars):
        raise ValueError("All bars must belong to the same symbol.")

    timeframe = bars[0].timeframe
    if any(b.timeframe is not timeframe for b in bars):
        raise ValueError("All bars must have the same timeframe.")

    ref = bars[-1]
    h, l, c = ref.high, ref.low, ref.close

    pivot = _pivot_levels(h, l, c)
    weekly = bars[-5:] if len(bars) >= 5 else bars
    candle = _candle_pattern(bars[-2], ref)
    rel_vol = _relative_volume(bars)

    return PriceLevels(
        prev_high=h,
        prev_low=l,
        prev_close=c,
        pivot=pivot,
        weekly_high=max(b.high for b in weekly),
        weekly_low=min(b.low for b in weekly),
        candle=candle,
        relative_volume=rel_vol,
    )


def _pivot_levels(high: float, low: float, close: float) -> PivotLevels:
    pp = (high + low + close) / 3
    r1 = 2 * pp - low
    r2 = pp + (high - low)
    s1 = 2 * pp - high
    s2 = pp - (high - low)
    return PivotLevels(pp=pp, r1=r1, r2=r2, s1=s1, s2=s2)


def _candle_pattern(prev: Bar, curr: Bar) -> CandlePattern | None:
    """curr barında bilinen mum formasyonlarından birini tespit eder.

    Öncelik sırası: engulfing → hammer → shooting_star → doji.
    Hiçbiri eşleşmezse None döner.
    """
    total = curr.high - curr.low
    if total == 0:
        return None

    body = abs(curr.close - curr.open)
    upper_shadow = curr.high - max(curr.open, curr.close)
    lower_shadow = min(curr.open, curr.close) - curr.low
    bullish = curr.close > curr.open

    # Engulfing: mevcut gövde önceki gövdeyi tamamen yutuyor
    prev_body_top = max(prev.open, prev.close)
    prev_body_bot = min(prev.open, prev.close)
    curr_body_top = max(curr.open, curr.close)
    curr_body_bot = min(curr.open, curr.close)

    if curr_body_top > prev_body_top and curr_body_bot < prev_body_bot:
        if bullish and prev.close < prev.open:
            return CandlePattern(name="bullish_engulfing", bullish=True)
        if not bullish and prev.close > prev.open:
            return CandlePattern(name="bearish_engulfing", bullish=False)

    # Hammer: alt gölge range'in %60'ından fazla, üst gölge %10'dan az
    if lower_shadow / total >= 0.6 and upper_shadow / total <= 0.1:
        return CandlePattern(name="hammer", bullish=True)

    # Shooting Star: üst gölge range'in %60'ından fazla, alt gölge %10'dan az
    if upper_shadow / total >= 0.6 and lower_shadow / total <= 0.1:
        return CandlePattern(name="shooting_star", bullish=False)

    # Doji: hammer/shooting_star elendikten sonra, gövde range'in %5'inden küçük
    if body / total < 0.05:
        return CandlePattern(name="doji", bullish=bullish)

    return None


def compute_session_snapshot(today: IntradaySnapshot, prev_close: float) -> SessionSnapshot:
    """Bugünkü canlı barı dün kapanışıyla ilişkilendirerek bağlam üretir.

    Yalnızca seans içinde çağrılmalıdır; ``today`` bar'ı ``get_today()``
    tarafından döndürülen intraday snapshot'tır.

    Raises:
        ValueError: prev_close sıfır veya negatifse.
    """
    if prev_close <= 0:
        raise ValueError("prev_close must be positive.")

    change_pct = (today.close - prev_close) / prev_close * 100
    gap_pct = (today.open - prev_close) / prev_close * 100
    range_pct = (today.high - today.low) / prev_close * 100
    day_range = today.high - today.low
    range_position = (today.close - today.low) / day_range * 100 if day_range > 0 else 50.0

    return SessionSnapshot(
        open=today.open,
        high=today.high,
        low=today.low,
        close=today.close,
        volume=today.volume,
        change_pct=round(change_pct, 2),
        gap_pct=round(gap_pct, 2),
        range_pct=round(range_pct, 2),
        range_position=round(range_position, 1),
    )


def _relative_volume(bars: list[Bar]) -> float | None:
    """Son barın hacmini önceki 20 barın ortalamasına böler.

    Son barın hacmi None ise None döner. Önceki 20 barda volume eksikse
    mevcut olanlarla ortalama hesaplanır; tamamı eksikse None döner.
    """
    if bars[-1].volume is None:
        return None

    history = [b.volume for b in bars[-21:-1] if b.volume is not None]
    if not history:
        return None

    avg = sum(history) / len(history)
    return bars[-1].volume / avg if avg > 0 else None
