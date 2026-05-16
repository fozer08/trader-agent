from __future__ import annotations

from dataclasses import dataclass

from ..types import Bar, IntradaySnapshot


@dataclass(frozen=True)
class LiveSessionPulse:
    """Canlı seansın anlık görünümü ve son seans kapanışına göre değişim."""
    open: float
    high: float
    low: float
    current_price: float    # Son tick / şu anki fiyat (canlı seans henüz kapanmadı)
    volume: float | None
    change_pct: float       # (current_price − prev_close) / prev_close × 100; gap dahil
    gap_pct: float          # (open − prev_close) / prev_close × 100
    open_change_pct: float  # (current_price − open) / open × 100; gap hariç, salt intraday
    range_pct: float        # (high − low) / prev_close × 100
    range_position: float   # (current_price − low) / (high − low) × 100; 0=dip, 100=tepe
    relative_volume: float | None  # Bugünkü hacim / son 20 günlük ortalama
    price_vs_ema: str | None       # "above" | "below" — current_price'ın günlük ema_fast'a göre konumu


def compute_pulse(
    today: IntradaySnapshot,
    daily_bars: list[Bar],
    ema_fast: float | None,
) -> LiveSessionPulse:
    """Canlı barı son seans kapanışı, günlük hacim geçmişi ve günlük EMA ile ilişkilendirir.

    ``today`` ``get_today()`` snapshot'ı; ``daily_bars`` son seans dahil günlük bar
    listesi — son barın kapanışı prev_close olarak, son 20 barın hacim ortalaması
    relative_volume için kullanılır; ``ema_fast`` günlük IndicatorSet'in ema_fast değeridir.

    Raises:
        ValueError: daily_bars boşsa veya son barın kapanışı sıfır/negatifse.
    """
    if not daily_bars:
        raise ValueError("daily_bars cannot be empty.")
    prev_close = daily_bars[-1].close
    if prev_close <= 0:
        raise ValueError("Last daily bar close must be positive.")

    change_pct = (today.close - prev_close) / prev_close * 100
    gap_pct = (today.open - prev_close) / prev_close * 100
    open_change_pct = (today.close - today.open) / today.open * 100 if today.open > 0 else 0.0
    range_pct = (today.high - today.low) / prev_close * 100
    day_range = today.high - today.low
    range_position = (today.close - today.low) / day_range * 100 if day_range > 0 else 50.0

    return LiveSessionPulse(
        open=today.open,
        high=today.high,
        low=today.low,
        current_price=today.close,
        volume=today.volume,
        change_pct=round(change_pct, 2),
        gap_pct=round(gap_pct, 2),
        open_change_pct=round(open_change_pct, 2),
        range_pct=round(range_pct, 2),
        range_position=round(range_position, 1),
        relative_volume=_relative_volume(today.volume, daily_bars),
        price_vs_ema=None if ema_fast is None else ("above" if today.close >= ema_fast else "below"),
    )


def _relative_volume(current_volume: float | None, daily_bars: list[Bar]) -> float | None:
    """Bugünkü hacmi son 20 günlük barın ortalamasına böler."""
    if current_volume is None:
        return None
    history = [b.volume for b in daily_bars[-20:] if b.volume is not None]
    if not history:
        return None
    avg = sum(history) / len(history)
    return round(current_volume / avg, 2) if avg > 0 else None
