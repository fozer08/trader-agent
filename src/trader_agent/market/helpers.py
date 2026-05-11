from __future__ import annotations

from dataclasses import replace
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
    closed_until, üretilen barların is_closed'ını belirlemek için kullanılır.
    """
    # Daily aggregation farklı bir kaynak (historical endpoint) gerektirir, bu fonksiyonun kapsamı dışı
    if target_tf is TimeFrame.D1:
        raise ValueError("Use get_daily() for daily bars.")

    if not bars:
        return []

    # Coarser → finer aggregation mümkün değil (M5'ten M1 üretilemez)
    source_tf = bars[0].timeframe
    if source_tf.minutes > target_tf.minutes:
        raise ValueError(
            "Source timeframe cannot be coarser than target timeframe. "
            f"source_tf={source_tf.name}, target_tf={target_tf.name}"
        )

    # Aynı timeframe: aggregation yok, sadece güncel zamana göre is_closed yenilenir
    if source_tf is target_tf:
        return [
            replace(
                bar,
                is_closed=bar.datetime + timedelta(minutes=target_tf.minutes) <= closed_until,
            )
            for bar in sorted(bars, key=lambda b: b.datetime)
        ]

    sorted_bars = sorted(bars, key=lambda b: b.datetime)
    result: list[Bar] = []
    bucket_start: datetime | None = None
    bucket: list[Bar] = []

    # Bucket akışı: her bar kendi target_tf bucket'ına atanır; bucket değişince bir önceki tamamlanır
    for bar in sorted_bars:
        current_start = floor_to_timeframe(bar.datetime, target_tf, session_start)
        if bucket_start is not None and current_start != bucket_start:
            result.append(_aggregate_bucket(bucket_start, bucket, target_tf, closed_until))
            bucket = []
        bucket_start = current_start
        bucket.append(bar)

    # Son bucket loop bittiğinde flush edilmemiş olur
    if bucket_start is not None and bucket:
        result.append(_aggregate_bucket(bucket_start, bucket, target_tf, closed_until))

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
    # Gecikmeli feed: gerçek "şu an"dan delay_minutes geri çekilir, çünkü o zamana kadar olan veri güvenilir
    delayed_now = now - timedelta(minutes=delay_minutes)
    if delayed_now.weekday() >= 5:  # Cumartesi/Pazar: piyasa kapalı
        return None

    start = datetime.combine(delayed_now.date(), session_start, tzinfo=delayed_now.tzinfo)
    end = datetime.combine(delayed_now.date(), session_end, tzinfo=delayed_now.tzinfo)
    # Seans bitti mi? Bittiyse end sabit, bitmediyse delayed_now (henüz oluşmamış barlar dahil edilmesin)
    reliable_end = min(delayed_now, end)

    # Henüz seans başlamamış (delayed_now < session_start)
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

    # Tek geçişte high/low ve volume toplanır; volume "ya hep ya hiç" prensibi: bir tek None varsa toplam None
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
    # Open ilk bar'dan, close son bar'dan: bars sıralı kabul edilir
    return bars[0].open, high, low, bars[-1].close, volume


def floor_to_timeframe(
    dt: datetime,
    timeframe: TimeFrame,
    session_start: time | None,
) -> datetime:
    """dt'yi ait olduğu timeframe bucket'ının başlangıcına yuvarlar.

    session_start verilirse seans açılışına, aksi halde gece yarısına hizalar.
    """
    # Anchor: bucket sıralamasının başlangıç noktası. Seans 10:00'da başlıyorsa H1 bucket'ları
    # 10:00, 11:00... olur; gece yarısına anchor'lansa 09:00, 10:00, 11:00 olurdu (yanlış).
    if session_start is not None:
        anchor = dt.replace(
            hour=session_start.hour,
            minute=session_start.minute,
            second=0,
            microsecond=0,
        )
    else:
        anchor = dt.replace(hour=0, minute=0, second=0, microsecond=0)

    # dt'nin anchor'dan kaç tam timeframe.minutes uzakta olduğunu bul, o bucket'ın başına döndür
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
    closed_until: datetime,
) -> Bar:
    """Bucket içindeki barlardan tek bir aggregate Bar üretir."""
    open_, high, low, close, volume = ohlcv_from_bars(bucket)
    bar_end = bucket_start + timedelta(minutes=target_tf.minutes)
    return Bar(
        symbol=bucket[0].symbol,
        datetime=bucket_start,
        timeframe=target_tf,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=volume,
        is_closed=bar_end <= closed_until,
    )
