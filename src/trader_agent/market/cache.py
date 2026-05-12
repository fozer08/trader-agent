from __future__ import annotations

from bisect import bisect_left, bisect_right
from datetime import datetime, timedelta
from operator import attrgetter

from .types import Bar, TimeFrame

_dt_key = attrgetter("datetime")


class MarketBarCache:
    """Sembol ve timeframe bazında datetime'a göre sıralı barları tutan bellek içi cache.

    Kontrat:
    - Çağıran, set'e contiguous (gap'siz) bars vermek zorundadır. Cache invariant
      zorlamaz; gap'li bars set edilirse missing orta gap'leri göremez ve
      tutarsız sonuç verir.
    - max_bars bucket başına FIFO limittir; çağıranın bir defada beklediği bar
      sayısından büyük olmalıdır. Aksi halde set sonrası baştan bar'lar
      trim'lenir, sonraki missing çağrısı yine "start kapsanmıyor" der ve
      sonsuz re-fetch döngüsüne yol açabilir.
    - Tip güvenliği: Bar frozen dataclass, depolanan barlar mutasyon edilmez.
    """

    def __init__(self, max_bars: int = 500) -> None:
        self._max_bars = max_bars
        self._buckets: dict[tuple[str, TimeFrame], list[Bar]] = {}

    def get(
        self,
        symbol: str,
        tf: TimeFrame,
        start: datetime,
        end: datetime,
    ) -> list[Bar] | None:
        """[start, end] aralığındaki bar'ları döndürür (iki uç da inclusive).

        start ve end birer bar datetime'ı olarak cache'te bulunmalıdır; aksi
        halde None döner. Bu tasarım çağıranı exact boundary tutmaya zorlar;
        kısmi sonuçlar için last_n veya last kullanılır.
        """
        inner = self._buckets.get((symbol, tf), [])
        lo = bisect_left(inner, start, key=_dt_key)
        hi = bisect_right(inner, end, key=_dt_key)
        result = inner[lo:hi]
        if not result or result[0].datetime != start or result[-1].datetime != end:
            return None
        return result

    def set(
        self,
        symbol: str,
        tf: TimeFrame,
        bars: list[Bar],
    ) -> None:
        """Sıralı ve contiguous bars'ı bucket'a yazar.

        Çağıran sıralılığı ve ardışıklığı garanti etmelidir (sınıf kontratına
        bakınız). Aynı datetime'daki eski bar üzerine yazılır. Kapasite
        aşılırsa en eski barlar baştan silinir (FIFO).
        """
        if not bars:
            return

        inner = self._buckets.setdefault((symbol, tf), [])

        if not inner or bars[0].datetime > inner[-1].datetime:
            inner.extend(bars)
        else:
            lo = bisect_left(inner, bars[0].datetime, key=_dt_key)
            hi = bisect_right(inner, bars[-1].datetime, key=_dt_key)
            inner[lo:hi] = bars

        excess = len(inner) - self._max_bars
        if excess > 0:
            del inner[:excess]

    def last(self, symbol: str, tf: TimeFrame) -> Bar | None:
        """Cache'in son barını veya bucket boşsa None döndürür."""
        inner = self._buckets.get((symbol, tf), [])
        return inner[-1] if inner else None

    def last_n(self, symbol: str, tf: TimeFrame, n: int) -> list[Bar]:
        """Cache'in son n barını döndürür; n <= 0 ise boş liste."""
        if n <= 0:
            return []
        inner = self._buckets.get((symbol, tf), [])
        return list(inner[-n:])

    def missing(
        self,
        symbol: str,
        tf: TimeFrame,
        start: datetime,
        end: datetime,
    ) -> tuple[datetime, datetime] | None:
        """Eksik aralığı (start_dt, end_dt) olarak döndürür; tam kapsama varsa None.

        Tuple'ın iki ucu da inclusive datetime'dır. Cache yalnızca tamamlanmış
        bar tutar; son bar end'i aşmışsa kuyruk eksiktir.
        """
        inner = self._buckets.get((symbol, tf), [])
        if not inner:
            return start, end

        first_dt = inner[0].datetime
        last_dt = inner[-1].datetime

        if first_dt > start or last_dt < start:
            return start, end

        if last_dt < end:
            return last_dt + timedelta(minutes=tf.minutes), end
        return None

    def clear(self, symbol: str, tf: TimeFrame) -> None:
        """Belirli sembol/timeframe bucket'ını siler; yoksa no-op."""
        self._buckets.pop((symbol, tf), None)

    def clear_all(self) -> None:
        """Tüm bucket'ları siler."""
        self._buckets.clear()

    def keys(self) -> list[tuple[str, TimeFrame]]:
        """Cache'te bulunan tüm (symbol, tf) anahtarları."""
        return list(self._buckets.keys())

    def size(self, symbol: str, tf: TimeFrame) -> int:
        """Belirli bucket'taki bar sayısı."""
        return len(self._buckets.get((symbol, tf), []))

    def __len__(self) -> int:
        """Cache'teki bucket sayısı (toplam bar değil; bar sayısı için size())."""
        return len(self._buckets)
