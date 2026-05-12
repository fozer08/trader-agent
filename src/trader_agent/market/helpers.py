from __future__ import annotations

from datetime import datetime, time, timedelta

from .types import Bar, TimeFrame


def aggregate_bars(
    bars: list[Bar],
    target_tf: TimeFrame,
    closed_until: datetime,
    session_start: time | None = None,
) -> list[Bar]:
    """Düşük timeframe barlarını hedef intraday timeframe'e toplar.

    session_start verilirse bucket'lar gece yarısına değil seans başına hizalanır.
    closed_until sonrasında biten bucket'lar döndürülmez.
    """
    if target_tf is TimeFrame.D1:
        raise ValueError("Use get_daily() for daily bars.")

    if not bars:
        return []

    source_tf = bars[0].timeframe
    if source_tf.minutes > target_tf.minutes:
        raise ValueError(
            "Source timeframe cannot be coarser than target timeframe. "
            f"source_tf={source_tf.name}, target_tf={target_tf.name}"
        )

    if source_tf is target_tf:
        return [
            bar
            for bar in sorted(bars, key=lambda b: b.datetime)
            if bar.datetime + timedelta(minutes=target_tf.minutes) <= closed_until
        ]

    sorted_bars = sorted(bars, key=lambda b: b.datetime)
    result: list[Bar] = []
    bucket_start: datetime | None = None
    bucket: list[Bar] = []

    for bar in sorted_bars:
        current_start = floor_to_timeframe(bar.datetime, target_tf, session_start)
        if bucket_start is not None and current_start != bucket_start:
            if _is_bucket_closed(bucket_start, target_tf, closed_until):
                result.append(_aggregate_bucket(bucket_start, bucket, target_tf))
            bucket = []
        bucket_start = current_start
        bucket.append(bar)

    if bucket_start is not None and bucket:
        if _is_bucket_closed(bucket_start, target_tf, closed_until):
            result.append(_aggregate_bucket(bucket_start, bucket, target_tf))

    return result


def calc_session_bounds(
    now: datetime,
    session_start: time,
    session_end: time,
    delay_minutes: int,
) -> tuple[datetime, datetime] | None:
    """Şu an için güvenilir kabul edilen seans aralığını döndürür.

    Bitiş, feed gecikmesini modellemek için delay_minutes kadar geri çekilir.
    Hafta sonu veya seans başlamadan önce çağrılırsa None döner.
    """
    delayed_now = now - timedelta(minutes=delay_minutes)
    if delayed_now.weekday() >= 5:  # Cumartesi/Pazar: piyasa kapalı
        return None

    start = datetime.combine(delayed_now.date(), session_start, tzinfo=delayed_now.tzinfo)
    end = datetime.combine(delayed_now.date(), session_end, tzinfo=delayed_now.tzinfo)
    reliable_end = min(delayed_now, end)

    if reliable_end < start:
        return None
    return start, reliable_end


def ohlcv_from_bars(bars: list[Bar]) -> tuple[float, float, float, float, float | None]:
    """Bar listesini tek (open, high, low, close, volume) tuple'ına indirger.

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
            second=0,
            microsecond=0,
        )
    else:
        anchor = dt.replace(hour=0, minute=0, second=0, microsecond=0)

    elapsed_minutes = int((dt - anchor).total_seconds() // 60)
    bucket_offset = elapsed_minutes - (elapsed_minutes % timeframe.minutes)
    return anchor + timedelta(minutes=bucket_offset)


def is_weekday(dt: datetime) -> bool:
    """dt Pazartesi-Cuma aralığında mı?"""
    return dt.weekday() < 5


def _aggregate_bucket(
    bucket_start: datetime,
    bucket: list[Bar],
    target_tf: TimeFrame,
) -> Bar:
    """Bucket içindeki barlardan tek bir aggregate Bar üretir."""
    open_, high, low, close, volume = ohlcv_from_bars(bucket)
    return Bar(
        symbol=bucket[0].symbol,
        datetime=bucket_start,
        timeframe=target_tf,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
    )


def _is_bucket_closed(
    bucket_start: datetime,
    timeframe: TimeFrame,
    closed_until: datetime,
) -> bool:
    return bucket_start + timedelta(minutes=timeframe.minutes) <= closed_until
