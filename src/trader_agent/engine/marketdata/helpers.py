from __future__ import annotations

from datetime import datetime, time, timedelta

from .types import OHLCV, BarData, TimeFrame
from ..calendar import TradingCalendar


def delayed_now(now: datetime, delay_minutes: int) -> datetime:
    """Verilen zamandan veri kaynağının gecikmesini düşer."""
    return now - timedelta(minutes=delay_minutes)


def expected_period_start(
    tf: TimeFrame,
    calendar: TradingCalendar,
    delayed_time: datetime,
    *,
    allow_uncovered: bool = False,
) -> datetime | None:
    """Gecikmesi düşülmüş zamana göre son tamamlanmış barın başlangıcını döndürür.

    delayed_time seans timezone'unda verilmelidir; gecikme burada tekrar düşülmez.
    D1 için son tamamlanmış seans esas alınır. Gün içi periyotlarda bugünün
    seansına hizalanmış son tamamlanmış bar döner; böyle bir bar yoksa None.
    Seans kapanışı aşılmaz; tatiller ve tarihli seans saatleri takvimden çözülür.
    """
    if tf is TimeFrame.D1:
        return calendar.last_completed_session(delayed_time, allow_uncovered=allow_uncovered).start

    bounds = calendar.reliable_bounds(delayed_time)
    if bounds is None:
        return None
    start, end = bounds
    period = timedelta(minutes=tf.minutes)
    completed_periods = (end - start) // period
    if completed_periods == 0:
        return None
    return start + (completed_periods - 1) * period


def aggregate_bars(
    series: BarData,
    target_tf: TimeFrame,
    closed_until: datetime,
    session_start: time | None = None,
) -> BarData:
    """Düşük timeframe barlarını hedef intraday timeframe'e toplar.

    session_start verilirse bucket'lar gece yarısına değil seans başına hizalanır.
    closed_until sonrasında biten bucket'lar döndürülmez.
    """
    if target_tf is TimeFrame.D1:
        raise ValueError("Use get_daily() for daily bars.")

    bars = series.bars
    if not bars:
        return BarData(symbol=series.symbol, timeframe=target_tf, bars=[])

    source_tf = series.timeframe
    if source_tf.minutes > target_tf.minutes:
        raise ValueError(
            "Source timeframe cannot be coarser than target timeframe. "
            f"source_tf={source_tf.name}, target_tf={target_tf.name}"
        )

    if source_tf is target_tf:
        completed = [
            bar
            for bar in sorted(bars, key=lambda b: b.period_start)
            if bar.period_start + timedelta(minutes=target_tf.minutes) <= closed_until
        ]
        return BarData(symbol=series.symbol, timeframe=target_tf, bars=completed)

    sorted_bars = sorted(bars, key=lambda b: b.period_start)
    result: list[OHLCV] = []
    bucket_start: datetime | None = None
    bucket: list[OHLCV] = []

    for bar in sorted_bars:
        current_start = floor_to_timeframe(bar.period_start, target_tf, session_start)
        if bucket_start is not None and current_start != bucket_start:
            if _is_bucket_closed(bucket_start, target_tf, closed_until):
                result.append(_aggregate_bucket(bucket_start, bucket))
            bucket = []
        bucket_start = current_start
        bucket.append(bar)

    if bucket_start is not None and bucket:
        if _is_bucket_closed(bucket_start, target_tf, closed_until):
            result.append(_aggregate_bucket(bucket_start, bucket))

    return BarData(symbol=series.symbol, timeframe=target_tf, bars=result)


def ohlcv_from_bars(bars: list[OHLCV]) -> tuple[float, float, float, float, float | None]:
    """OHLCV listesini tek (open, high, low, close, volume) tuple'ına indirger.

    Listede bir bar bile volume=None ise toplam volume None olur.
    """
    high = bars[0].high
    low = bars[0].low
    total_volume = 0.0
    all_have_volume = True

    for bar in bars:
        if bar.high > high:
            high = bar.high
        if bar.low < low:
            low = bar.low
        if bar.volume is None:
            all_have_volume = False
        elif all_have_volume:
            total_volume += bar.volume

    volume = total_volume if all_have_volume else None
    return bars[0].open, high, low, bars[-1].close, volume


def floor_to_timeframe(
    dt: datetime,
    timeframe: TimeFrame,
    session_start: time | None,
) -> datetime:
    """dt'yi ait olduğu timeframe bucket'ının başlangıcına yuvarlar.

    session_start verilirse seans açılışına, aksi halde gece yarısına hizalar.
    """
    if session_start is not None:
        anchor = dt.replace(
            hour=session_start.hour,
            minute=session_start.minute,
            second=session_start.second,
            microsecond=0,
        )
    else:
        anchor = dt.replace(hour=0, minute=0, second=0, microsecond=0)

    elapsed_minutes = int((dt - anchor).total_seconds() // 60)
    bucket_offset = elapsed_minutes - (elapsed_minutes % timeframe.minutes)
    return anchor + timedelta(minutes=bucket_offset)


def _aggregate_bucket(
    bucket_start: datetime,
    bucket: list[OHLCV],
) -> OHLCV:
    """Bucket içindeki barlardan tek bir aggregate OHLCV üretir."""
    open_, high, low, close, volume = ohlcv_from_bars(bucket)
    return OHLCV(
        period_start=bucket_start,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        ohlc_kind=("close_only" if any(b.ohlc_kind == "close_only" for b in bucket)
                   else "sampled" if any(b.ohlc_kind == "sampled" for b in bucket)
                   else "range_only" if any(b.ohlc_kind == "range_only" for b in bucket)
                   else "reported"),
    )


def _is_bucket_closed(
    bucket_start: datetime,
    timeframe: TimeFrame,
    closed_until: datetime,
) -> bool:
    return bucket_start + timedelta(minutes=timeframe.minutes) <= closed_until
