from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from operator import attrgetter

from .types import OHLCV, BarData, TimeFrame

_period_start_key = attrgetter("period_start")


class MarketBarCache:
    """Sembol ve timeframe bazında datetime'a göre sıralı barları tutan bellek içi store.

    Sağlayıcının biriktirdiği kaynak barları ve servisin günlük sonuçları aynı
    store'da, ayrı timeframe bucket'larında tutulabilir.

    Eksik zamanlar doldurulmaz; sorgular yalnız eldeki barları döndürür.
    Aynı zamandaki bar güncellenir, diğer barlar korunur. max_bars aşıldığında
    en eski barlar silinir; sorgunun yeterliliğine çağıran karar verir.
    """

    def __init__(self, max_bars: int = 500) -> None:
        self._max_bars = max_bars
        self._buckets: dict[tuple[str, TimeFrame], list[OHLCV]] = {}

    def get(
        self,
        symbol: str,
        tf: TimeFrame,
        start: datetime,
        end: datetime,
    ) -> BarData:
        """[start, end] aralığındaki mevcut barları döndürür; uçlar dahil."""
        inner = self._buckets.get((symbol, tf), [])
        lo = bisect_left(inner, start, key=_period_start_key)
        hi = bisect_right(inner, end, key=_period_start_key)
        return BarData(symbol=symbol, timeframe=tf, bars=inner[lo:hi])

    def set(self, series: BarData) -> None:
        """Barları zamanlarına göre birleştirir ve son max_bars adedini saklar."""
        if not series.bars:
            return
        key = (series.symbol, series.timeframe)
        merged = {bar.period_start: bar for bar in self._buckets.get(key, [])}
        merged.update((bar.period_start, bar) for bar in series.bars)
        self._buckets[key] = sorted(merged.values(), key=_period_start_key)[-self._max_bars:]

    def last(self, symbol: str, tf: TimeFrame) -> OHLCV | None:
        """Store'un son barını veya bucket boşsa None döndürür."""
        inner = self._buckets.get((symbol, tf), [])
        return inner[-1] if inner else None

    def last_n(self, symbol: str, tf: TimeFrame, n: int) -> BarData:
        """Store'un son n barını seri olarak döndürür; n <= 0 ise seri boştur."""
        if n <= 0:
            return BarData(symbol=symbol, timeframe=tf, bars=[])
        inner = self._buckets.get((symbol, tf), [])
        return BarData(symbol=symbol, timeframe=tf, bars=list(inner[-n:]))

    def missing_tail(
        self,
        symbol: str,
        tf: TimeFrame,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime] | None:
        """Son bardan sonraki aralık; kaynakta boş kalan eski dakikaları yeniden istemez."""
        inner = self._buckets.get((symbol, tf), [])
        if not inner:
            return start, end

        last_dt = inner[-1].period_start

        if last_dt < start:
            return start, end

        if last_dt < end:
            return last_dt + timedelta(minutes=tf.minutes), end
        return None
