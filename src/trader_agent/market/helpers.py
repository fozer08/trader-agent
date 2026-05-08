from __future__ import annotations

from dataclasses import replace
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from .types import Bar, TimeFrame


def aggregate_bars(
    bars: list[Bar],
    target_tf: TimeFrame,
    closed_until: datetime,
    session_start: time | None = None,
) -> list[Bar]:
    """Daha düşük timeframe barlarını hedef gün içi timeframe'e toplar.

    Bar zamanları bucket başlangıcı kabul edilir. ``session_start`` verilirse
    bucket hizalaması gece yarısı yerine piyasa seansına bağlanır; bu da
    09:30-16:00 gibi seanslarda H1 barlarının doğru başlamasını sağlar.
    ``closed_until`` her üretilen barın kapanıp kapanmadığını belirlemek için
    güvenilir kabul edilen son zamanı temsil eder.
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
            replace(
                bar,
                is_closed=bar.datetime + timedelta(minutes=target_tf.minutes) <= closed_until,
            )
            for bar in sorted(bars, key=lambda bar: bar.datetime)
        ]

    sorted_bars = sorted(bars, key=lambda bar: bar.datetime)
    result: list[Bar] = []
    bucket_start: datetime | None = None
    bucket: list[Bar] = []

    for bar in sorted_bars:
        current_start = floor_to_timeframe(
            bar.datetime,
            target_tf,
            session_start=session_start,
        )
        if bucket_start is not None and current_start != bucket_start:
            result.append(_aggregate_bucket(bucket_start, bucket, target_tf, closed_until))
            bucket = []

        bucket_start = current_start
        bucket.append(bar)

    if bucket_start is not None and bucket:
        result.append(_aggregate_bucket(bucket_start, bucket, target_tf, closed_until))

    return result


def calc_session_bounds(
    now: datetime,
    session_start: time,
    session_end: time,
    delay_minutes: int,
) -> tuple[datetime, datetime] | None:
    """Verilen saate göre güvenilir gün içi seans aralığını döndürür.

    Aralığın bitişi, gecikmeli piyasa verisini modellemek için
    ``delay_minutes`` kadar geriye çekilir. ``None`` dönüşü, gecikmeli seans
    başlangıcından önce veya hafta sonunda olduğu gibi güvenilir seans içi veri
    olmadığı anlamına gelir.
    """
    delayed_now = now - timedelta(minutes=delay_minutes)

    if not is_weekday(delayed_now):
        return None

    start = datetime.combine(
        delayed_now.date(),
        session_start,
        tzinfo=delayed_now.tzinfo,
    )
    end = datetime.combine(
        delayed_now.date(),
        session_end,
        tzinfo=delayed_now.tzinfo,
    )

    reliable_end = min(delayed_now, end)

    if reliable_end < start:
        return None

    return start, reliable_end


def _aggregate_bucket(
    bucket_start: datetime,
    bucket: list[Bar],
    target_tf: TimeFrame,
    closed_until: datetime,
) -> Bar:
    """Aynı bucket içindeki barlardan tek bir OHLCV bar üretir."""
    bar_end = bucket_start + timedelta(minutes=target_tf.minutes)
    high = bucket[0].high
    low = bucket[0].low
    volume = 0.0
    has_full_volume = True

    for bar in bucket:
        if bar.high > high:
            high = bar.high
        if bar.low < low:
            low = bar.low
        if bar.volume is None:
            has_full_volume = False
        elif has_full_volume:
            volume += bar.volume

    return Bar(
        symbol=bucket[0].symbol,
        datetime=bucket_start,
        timeframe=target_tf,
        open=bucket[0].open,
        high=high,
        low=low,
        close=bucket[-1].close,
        volume=volume if has_full_volume else None,
        is_closed=bar_end <= closed_until,
    )


def floor_to_timeframe(
    dt: datetime,
    timeframe: TimeFrame,
    session_start: time | None = None,
) -> datetime:
    """``dt`` değerini ait olduğu timeframe bucket başlangıcına indirger.

    Varsayılan olarak bucket'lar gece yarısına hizalanır. ``session_start``
    verilirse hizalama piyasa açılışına göre yapılır; bu, seans saat başında
    başlamadığında daha büyük gün içi timeframe'ler için önemlidir.
    """
    anchor = (
        dt.replace(
            hour=session_start.hour,
            minute=session_start.minute,
            second=0,
            microsecond=0,
        )
        if session_start is not None
        else dt.replace(hour=0, minute=0, second=0, microsecond=0)
    )

    elapsed_minutes = int((dt - anchor).total_seconds() // 60)
    bucket_offset = elapsed_minutes - (elapsed_minutes % timeframe.minutes)

    return anchor + timedelta(minutes=bucket_offset)


def to_timezone(
    dt: datetime,
    timezone: ZoneInfo,
) -> datetime:
    """``dt`` değerini ``timezone`` içinde döndürür; naive değerleri yerel kabul eder."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone)

    return dt.astimezone(timezone)


def is_weekday(dt: datetime) -> bool:
    """``dt`` değerinin Pazartesi-Cuma aralığında olup olmadığını döndürür."""
    return dt.weekday() < 5
